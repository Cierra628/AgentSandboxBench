"""Run sampled GRPO, BPO or TVCache paths on one owned isolated service."""
import argparse
import asyncio
import csv
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

from prettier6604_inputs import ROOT, validate, sha
from prepare_prettier6604_rollouts import audit, sampled_segment
from exp1_official_task import load, guest_command
from audit_prettier6604_official import inspect as inspect_official, reference, signature
from prettier6604_boundary import verify as verify_boundary


def execution_paths(rollouts,schedule,role=None,root_count=None):
    if root_count is not None:
        assert schedule=='grpo' and role is None and root_count in (1,2,7)
        selected=['backbone'] if root_count==1 else ['backbone','early_1'] if root_count==2 else list(rollouts)
        return {r:rollouts[r] for r in selected}
    if schedule=='grpo':return {r:path for r,path in rollouts.items() if not role or r==role}
    assert schedule in ('bpo','tvcache') and role in (None,'early_1'), 'restore smoke supports early_1 only'
    if schedule=='tvcache':
        selected={r:path for r,path in rollouts.items() if not role or r in ('backbone',role)}
        plan=prefix_tree_plan(selected)
        return {r:[s for label,s in plan['segments'] if label==r] for r in selected}
    if role:return {'backbone':rollouts['backbone'][:2],role:rollouts[role][2:]}
    return {r:path if r=='backbone' else path[2:] if r.startswith('early') else path[4:]
            for r,path in rollouts.items()}


def prefix_tree_plan(paths):
    """Mirror the pinned controller's deterministic trie traversal, without I/O."""
    forks={};segments=[]
    def visit(roles,offset):
        label='backbone' if 'backbone' in roles else roles[0]
        while all(offset<len(paths[r]) for r in roles):
            candidate=paths[roles[0]][offset]
            if any(paths[r][offset]!=candidate for r in roles[1:]):break
            segments.append([label,candidate]);offset+=1
        if len(roles)==1:
            segments.extend([label,s] for s in paths[label][offset:]);return
        assert all(offset<len(paths[r]) for r in roles), 'strict prefix is unsupported'
        groups={}
        for r in roles:groups.setdefault(paths[r][offset],[]).append(r)
        assert len(groups)>1
        ordered=list(groups.values())
        primary=ordered.pop(next((i for i,g in enumerate(ordered) if 'backbone' in g),0))
        assert all(len(g)==1 for g in ordered), 'this adapter requires singleton secondary groups'
        point=f'longest-prefix-{label}-{offset}'
        forks[point]=dict(parent_role=label,prefix=paths[label][:offset],offset=offset,
                          child_roles=[g[0] for g in ordered])
        for group in [primary,*ordered]:visit(group,offset)
    assert paths and all(paths.values())
    visit(list(paths),0)
    return dict(forks=forks,segments=segments)


def verify_process(before,after):
    assert all(before[k]==after[k] for k in ('pid','start_ticks','boot_id'))
    assert int(after['count'])>int(before['count'])
    return dict(status='PASS',before=before,after=after)


def validated_export(directory):
    cfg, original, patch = validate()
    directory = directory.resolve()
    provenance = json.loads((directory/'provenance.json').read_text())
    workload = directory/'workload'
    sample = json.loads((workload/'agent-sampling.json').read_text())['segments']
    assert sha(workload/'agent-sampling.json') == cfg['sampling_sha256']
    assert sha(workload/'guest-runner.sh') == cfg['runner_sha256']
    expected = json.loads((original/'rollouts.json').read_text())
    expected = {role: [role+'_inspect' if s in ('early_inspect','late_inspect') else s for s in path]
                for role, path in expected.items()}
    rollouts = json.loads((workload/'rollouts.json').read_text())
    assert rollouts == expected
    names = {s: 'early_inspect' if s.startswith('early_') and s.endswith('_inspect') else
             'late_inspect' if s.startswith('late_') and s.endswith('_inspect') else s
             for path in expected.values() for s in path}
    assert provenance['original_segment_names'] == names
    assert provenance['sampling_sha256'] == cfg['sampling_sha256']
    for name, digest in provenance['exported_sha256'].items():
        assert sha(workload/name) == digest, name
    assert sha(workload/'task.json') == sha(original/'task.json')
    check = audit(workload, sample, rollouts, names)
    assert check['status'] == 'PASS' and check['logical_action_count'] == 189 and check['logical_segment_count'] == 35
    return cfg, workload, patch, sample, rollouts, names


async def joined(coroutines):
    # Wait for every owned request before cleanup, including after one fails.
    values = await asyncio.gather(*coroutines, return_exceptions=True)
    errors = [v for v in values if isinstance(v, BaseException)]
    if errors:
        raise RuntimeError('owned concurrent operation failed: '+repr(errors[0]))
    return values


def verify_path(raw, role, path, workload, samples, names, sampling=True):
    count = 0
    duration = 0
    peaks = []
    for segment in path:
        output = raw/'rollouts'/role/segment
        manifest = workload/'segments'/segment/'actions.tsv'
        assert sha(output/'actions.tsv') == sha(manifest)
        with (output/'steps.tsv').open() as stream:
            rows = list(csv.DictReader(stream, delimiter='\t'))
        actions = sampled_segment(samples, role, names[segment])['actions']
        assert [r['step'] for r in rows] == [f'{i:03}' for i in range(1,len(actions)+1)]
        assert [int(r['exit_code']) for r in rows] == [a['exit_code'] for a in actions]
        assert all(r['branch']==role and r['round']==segment for r in rows)
        count += len(rows)
        duration += sum(int(r['duration_ns']) for r in rows)
        with (output/'guest-memory.tsv').open() as stream:
            memory = list(csv.DictReader(stream, delimiter='\t'))
        assert bool(memory)==sampling, 'memory samples do not match requested mode'
        peaks.extend((int(r['MemTotal_kib'])-int(r['MemFree_kib']))*1024 for r in memory)
    return dict(action_count=count,guest_action_sum_ns=duration,
                guest_observed_peak_bytes=max(peaks) if peaks else None,final_patch_sha256=sha(raw/'rollouts'/role/path[-1]/'patch.diff'))


def storage_allocation(mount):
    totals={};seen=set();begin=time.monotonic_ns()
    for top in ('env','run','tmp','controller'):
        for parent,dirs,files in os.walk(mount/top,followlinks=False):
            for name in files:
                p=Path(parent)/name
                try:info=p.lstat()
                except FileNotFoundError:continue
                if not stat.S_ISREG(info.st_mode) or (info.st_dev,info.st_ino) in seen:continue
                seen.add((info.st_dev,info.st_ino))
                rel=p.relative_to(mount)
                category='/'.join(rel.parts[:2]) if len(rel.parts)>2 else top
                totals[category]=totals.get(category,0)+info.st_blocks*512
    return dict(allocated_regular_file_bytes=totals,scan_elapsed_ns=time.monotonic_ns()-begin,
                scope='terminal file allocations, hard links counted once; not atomic; excludes directory/filesystem metadata')


def run(args):
    cfg, workload, patch, samples, rollouts, names = validated_export(args.frozen_export)
    task = load(args.official_task)
    expected_official = reference(args.expected_official_audit)
    assert task['instance_id']==cfg['task_id'] and task['base_commit']==cfg['base_commit']
    assert expected_official['F2P']==task['F2P'] and expected_official['P2P']==task['P2P']
    schedule=getattr(args,'schedule','grpo')
    root_count=getattr(args,'root_count',None)
    shared_cache=getattr(args,'shared_cache',False)
    assert not root_count or shared_cache, 'root-count is an explicit shared-cache variant'
    assert not shared_cache or schedule=='grpo' and not args.role
    executed=execution_paths(rollouts,schedule,args.role,root_count)
    roles=list(executed)
    final_roles=[args.role] if args.role and schedule!='tvcache' else roles
    tree_plan=prefix_tree_plan({r:rollouts[r] for r in roles}) if schedule=='tvcache' else None
    assert all(role in rollouts for role in roles)
    if args.check_only:
        print('status=VALIDATED schedule='+schedule+' roles='+','.join(roles));return 0
    out = args.output.resolve()
    assert out.parent.name=='controller' and out.parent.parent.name=='mount'
    assert out.parents[3]==ROOT/'.artifacts/storage-isolated-service'
    assert args.config_home.resolve()==out.parent/'config'
    out.mkdir(parents=True,exist_ok=False)
    os.umask(0o077)
    env = dict(os.environ, PATH=str(ROOT/'runtime/bin')+':'+os.environ['PATH'],
               XDG_CONFIG_HOME=str(args.config_home),HOST_MONITOR_LIGHT='1')
    from sandbox_runtime_monitor import RuntimeMonitor, boundary_runner
    lock = threading.Lock();counter=0;events=[];controller=None;monitor=None;physical=None;success=False
    scope='one corrected sampled branch' if args.role else 'GRPO: seven independent roots; concurrent full paths; no snapshots'
    if schedule=='bpo':scope='BPO early restore smoke: one parent prefix and one resumed branch' if args.role else 'BPO: one backbone, two full-VM snapshots, six resumed suffixes'
    if schedule=='tvcache':scope='TVCache smoke: full backbone and one restored suffix; deterministic trie' if args.role else 'TVCache: deterministic corrected trie, two full-VM snapshots and six restored suffixes'
    if shared_cache:scope=f'GRPO {len(roles)} roots: identical step-11 measurement barrier, then concurrent original suffixes; paused analysis variant'
    result = dict(status='FAIL',backend='agentenv',task_id=cfg['task_id'],roles=roles,paths={},checks={},schedule=schedule,
                  final_roles=final_roles,scope=scope,
                  physical_page_cache='NOT_MEASURED',VMM_PSS='NOT_MEASURED',monitoring_impact='NOT_VALIDATED')
    stage='preflight';started=time.monotonic_ns()

    def call(label, argv, timeout=180):
        nonlocal counter
        begin=time.monotonic_ns();wall=time.time_ns()
        with lock:
            index=counter;counter+=1
        label=f'{index:04}-{label}'
        code=None
        try:
            with (out/(label+'.stderr')).open('x') as log:
                proc=subprocess.run(list(map(str,argv)),cwd=ROOT,env=env,stdout=subprocess.PIPE,
                                    stderr=log,text=True,timeout=timeout)
            (out/(label+'.stdout')).write_text(proc.stdout)
            code=proc.returncode
            if code:raise RuntimeError(label+': exit '+str(code))
            return proc.stdout.strip()
        finally:
            with lock:
                events.append(dict(index=index,stage=label,start_monotonic_ns=begin,timestamp_ns=wall,
                                   duration_ns=time.monotonic_ns()-begin,exit_code=code))
                (out/'events.json').write_text(json.dumps(sorted(events,key=lambda r:r['index']),indent=2)+'\n')

    def cli(label,*argv,timeout=180):return call(label,[ROOT/'runtime/bin/aenv',*argv],timeout)
    def guest(label,sid,command,timeout=180):return cli(label,'exec',sid,'bash','-lc',command,timeout=timeout)
    runtime=RuntimeMonitor(out,cli,container=args.container)
    def interrupted(signum,frame):raise RuntimeError('owned rollout interrupted: '+str(signum))
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,interrupted)
    try:
        info=json.loads(call('service-state',['docker','inspect','--format','{{json .State}}',args.container],10))
        labels=json.loads(call('service-labels',['docker','inspect','--format','{{json .Config.Labels}}',args.container],10))
        assert info['Running'] and labels.get('agentsandboxbench.run_id')==out.parents[2].name
        assert labels.get('agentenv.experiment')=='isolated-storage-check'
        vendor=ROOT/'IncrementalDAX_moti/motivation/experiments/exp3-RL-fork'
        os.environ['WORKLOAD_NAME']='prettier-6604-rl-fork'
        sys.path.insert(0,str(vendor))
        spec=importlib.util.spec_from_file_location('prettier6604_grpo_controller',vendor/'controller.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        module.WORKLOAD=workload
        if schedule=='tvcache':module.ROLES=tuple(roles)
        def shell(*argv,capture=True):
            argv=list(argv)
            if argv[0]=='aenv':argv[0]=ROOT/'runtime/bin/aenv'
            label='snapshot-call' if argv[1:3]==['snapshot','create'] else 'controller-call'
            return call(label,argv,600)
        module.shell=shell
        class SampledPaths(module.Controller):
            async def physical_memory(self,label):return None

            async def start(self,role,*,template=None,parent=''):
                begin=time.monotonic_ns()
                if template:
                    sid=await asyncio.to_thread(cli,'restore-'+role,'start',template,'--detach','--timeout','1800',timeout=150)
                else:
                    sid=await asyncio.to_thread(call,'cold-'+role,[sys.executable,'-B',ROOT/'scripts/agentenv_cold_start.py',
                           '--config-home',args.config_home,'--service-url',args.service_url,'--image',cfg['source_image']],610)
                assert re.fullmatch(r'[a-fA-F0-9-]{36}',sid) and sid not in self.live
                self.live[sid]=sid  # Track immediately, before tool checks/uploads can fail.
                if schedule=='tvcache':self.by_role[role]=sid
                self.record('sandbox-ids.tsv',f'{role}\t{sid}\t{parent}\t{template or "root"}')
                self.record('events.tsv',f'{time.time_ns()}\tstart\troot\t{parent}\t{sid}\t{template or ""}\t{time.monotonic_ns()-begin}')
                await asyncio.to_thread(runtime.start,sid,role,'restored-checks' if template else 'setup')
                if template:
                    saved=self.checkpoints[template];step=saved['step']
                    assert saved['parent']==parent and sid!=parent
                    restored=json.loads(await asyncio.to_thread(guest,'boundary-'+role,sid,
                        'python3 /workspace/prettier6604-boundary.py --after-global-step '+str(step)))
                    boundary=verify_boundary(saved['boundary'],restored,step)
                    proc=dict(line.split('=',1) for line in (await asyncio.to_thread(guest,'process-'+role,sid,
                         'sleep 0.3; bash /workspace/exp1-process-probe.sh')).splitlines())
                    process=verify_process(saved['process'],proc)
                    (out/('restored-'+role+'.json')).write_text(json.dumps(dict(status='PASS',template=template,
                        parent=parent,sandbox_id=sid,before=saved['boundary'],after=restored,boundary=boundary,
                        process=process,branch_next_segment=executed[role][0]),indent=2)+'\n')
                    return sid
                source=await asyncio.to_thread(guest,'source-'+role,sid,
                    'cd /testbed && python3 - <<\'EOF\'\nimport subprocess,json\nfrom pathlib import Path\nhead=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()\nassert head=="'+cfg['base_commit']+'"\np=Path("src/language-js/needs-parens.js");assert p.read_bytes()==subprocess.check_output(["git","show","HEAD:"+str(p)])\nprint(json.dumps(dict(base_commit=head,cwd=str(Path.cwd()),initial_git_status=subprocess.check_output(["git","status","--porcelain"],text=True))))\nEOF')
                (out/('source-'+role+'.json')).write_text(json.dumps(json.loads(source),indent=2)+'\n')
                await asyncio.to_thread(cli,'upload-check-'+role,'upload',sid,ROOT/'scripts/prettier6604_check.cjs','/workspace/prettier6604-check.cjs')
                baseline=json.loads(await asyncio.to_thread(guest,'baseline-'+role,sid,'node /workspace/prettier6604-check.cjs --baseline'))
                assert baseline['status']=='PASS'
                await asyncio.to_thread(cli,'upload-workload-'+role,'upload',sid,out/'frozen-input','/workspace/mixfs-workload')
                if schedule in ('bpo','tvcache'):
                    await asyncio.to_thread(cli,'upload-boundary','upload',sid,ROOT/'scripts/prettier6604_boundary.py','/workspace/prettier6604-boundary.py')
                    await asyncio.to_thread(cli,'upload-process','upload',sid,ROOT/'scripts/exp1-process-probe.sh','/workspace/exp1-process-probe.sh')
                    self.root_process=dict(line.split('=',1) for line in (await asyncio.to_thread(guest,'process-start',sid,
                         'bash /workspace/exp1-process-probe.sh start')).splitlines())
                return sid

            async def save_prefix(self,sid,point,step):
                boundary=json.loads(await asyncio.to_thread(guest,'boundary-before-'+str(step),sid,
                         'python3 /workspace/prettier6604-boundary.py --after-global-step '+str(step)))
                verify_boundary(boundary,boundary,step)
                proc=dict(line.split('=',1) for line in (await asyncio.to_thread(guest,'process-before-'+str(step),sid,
                     'bash /workspace/exp1-process-probe.sh')).splitlines())
                assert all(proc[k]==self.root_process[k] for k in ('pid','start_ticks','boot_id'))
                await asyncio.to_thread(runtime.stop,sid,'checkpoint')
                try:template=await super().capture_agentenv_template(sid,point)
                finally:await asyncio.to_thread(runtime.start,sid,'backbone','after-checkpoint')
                assert re.fullmatch(r'[a-fA-F0-9-]{36}',template)
                self.checkpoints[template]=dict(parent=sid,step=step,boundary=boundary,process=proc)
                (out/('checkpoint-'+str(step)+'.json')).write_text(json.dumps(dict(status='PASS',template=template,
                    **self.checkpoints[template]),indent=2)+'\n')
                return template

            async def do_bpo(self):
                self.checkpoints={}
                root=await self.start('backbone');backbone=self.rollouts['backbone']
                await self.run_path(root,'backbone',backbone[:2])
                early=await self.save_prefix(root,'save-after-backbone-2',11)
                if args.role:
                    child=await self.start(args.role,template=early,parent=root)
                    await self.run_path(child,args.role,executed[args.role])
                    return {'backbone':root,args.role:child}
                await self.run_path(root,'backbone',backbone[2:4])
                late=await self.save_prefix(root,'save-after-backbone-4',24)
                await self.run_path(root,'backbone',backbone[4:])
                early_roles=[r for r in roles if r.startswith('early')]
                late_roles=[r for r in roles if r.startswith('late')]
                early_ids=await joined(self.start(r,template=early,parent=root) for r in early_roles)
                late_ids=await joined(self.start(r,template=late,parent=root) for r in late_roles)
                for point,ids,template in [('expand-after-backbone-2',early_ids,early),('expand-after-backbone-4',late_ids,late)]:
                    self.record('events.tsv',f'{time.time_ns()}\tfork\t{point}\t{root}\t{",".join(ids)}\t{template}\t0')
                by_role=dict(backbone=root,**dict(zip(early_roles+late_roles,early_ids+late_ids,strict=True)))
                await joined(self.run_path(by_role[r],r,executed[r]) for r in early_roles+late_roles)
                return by_role

            async def fork(self,sandbox,count,point,role,*,event='fork'):
                assert schedule=='tvcache'
                planned=tree_plan['forks'][point]
                assert planned['parent_role']==role=='backbone' and len(planned['child_roles'])==count
                assert planned['prefix']==rollouts[role][:planned['offset']]
                begin=time.monotonic_ns();self.phase('fork:'+point)
                step={2:11,4:24}[planned['offset']]
                template=await self.save_prefix(sandbox,point,step)
                children=await joined(self.start(r,template=template,parent=sandbox) for r in planned['child_roles'])
                self.record('events.tsv',f'{time.time_ns()}\t{event}\t{point}\t{sandbox}\t{",".join(children)}\t{template}\t{time.monotonic_ns()-begin}')
                return sandbox,children

            async def do_tvcache(self):
                self.checkpoints={};self.by_role={}
                await super().do_tvcache()
                return self.by_role

            async def do_grpo(self):
                # Same successful scheduling shape as upstream; join partial failures before deletion.
                sandboxes=await joined(self.start(role) for role in roles)
                self.phase('root-ready')
                if physical:
                    physical.ready()
                    assert all(self.rollouts[role][:2]==['read_repo','read_typescript'] for role in roles)
                    await joined(self.run_path(sid,role,self.rollouts[role][:2]) for sid,role in zip(sandboxes,roles,strict=True))
                    self.phase('shared-cache-prefix11')
                    for sid in sandboxes:await asyncio.to_thread(runtime.mark,sid,'shared-cache-analysis')
                    from shared_task_cache import measure
                    measurement=await asyncio.to_thread(measure,physical,dict(zip(roles,sandboxes,strict=True)),cfg['source_image'])
                    result['shared_cache_status']=measurement['status']
                    result['physical_page_cache']='MEASURED_AT_COMMON_PREFIX'
                    result['checks']['shared_cache_probe_and_task_state']='PASS'
                    await joined(self.run_path(sid,role,self.rollouts[role][2:]) for sid,role in zip(sandboxes,roles,strict=True))
                else:
                    await joined(self.run_path(sid,role,self.rollouts[role]) for sid,role in zip(sandboxes,roles,strict=True))
                return dict(zip(roles,sandboxes,strict=True))

        controller=SampledPaths(SimpleNamespace(system='agentenv',raw_dir=out,timeout=600))
        controller.phase('prepare')
        shutil.copytree(workload,out/'frozen-input')
        runner=out/'frozen-input/guest-runner.sh'
        shutil.copy2(runner,runner.with_name('guest-runner.original.sh'))
        runner.write_text(boundary_runner(runner.read_text(),guest_phases=True))
        (out/'sampling-variant.json').write_text(json.dumps(dict(mode='continuous per sandbox; boundary samples per segment',phase_updates='guest runner',original_runner_sha256=sha(workload/'guest-runner.sh'),executed_runner_sha256=sha(runner)),indent=2)+'\n')
        shutil.copy2(args.frozen_export/'provenance.json',out/'input-provenance.json')
        (out/'reference-source.patch').write_text(patch)
        (out/'expected-official-audit.json').write_text(json.dumps(expected_official,indent=2)+'\n')
        code=out/'code';code.mkdir()
        for p in [Path(__file__),ROOT/'scripts/prepare_prettier6604_rollouts.py',ROOT/'scripts/prettier6604_inputs.py',
                  ROOT/'scripts/agentenv_cold_start.py',ROOT/'scripts/prettier6604_check.cjs',
                  ROOT/'scripts/exp1_official_task.py',ROOT/'scripts/audit_prettier6604_official.py',
                  ROOT/'scripts/prettier6604_boundary.py',ROOT/'scripts/exp1-process-probe.sh',vendor/'controller.py']:
            shutil.copy2(p,code/p.name)
        (out/'source-sha256.json').write_text(json.dumps({p.name:sha(p) for p in code.iterdir()},indent=2)+'\n')
        (out/'effective-config.json').write_text(json.dumps(dict(cfg,paths=rollouts,selected_roles=roles,scope=result['scope'],
               schedule=schedule,executed_paths=executed,final_roles=final_roles,
               prefix_tree_plan=tree_plan,
               shared_cache_measurement=shared_cache,root_count=root_count,
               variant='role-specific inspect manifests from original sampling',sandbox_ttl_seconds=1800,
               host_scope='one temporary service cgroup; includes all VMMs and image cache; host phases may overlap',
               guest_scope='continuous per actual VM ID after monitor setup; explicit snapshot gaps; logical memory, not physical deduplication'),indent=2)+'\n')
        with (out/'host-monitor.log').open('x') as log:
            monitor=subprocess.Popen(['bash',ROOT/'IncrementalDAX_moti/motivation/experiments/lib/host-memory-monitor.sh',
                    out/'host/memory-samples.tsv',controller.phase_file,'0.1',args.container],env=env,
                    stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        if shared_cache:
            sources=['shared_task_cache.py','task_physical_cache.py','agentenv_whole_cache.py','measure_file_physical.py',
                     'guest_whole_cache_probe.py','guest_cache_marker.py','trenvx_whole_cache.py','paused_agentenv_boundary.py',
                     'cached_file_inventory.py','guest_cached_file_inventory.py','guest_named_file_probe.py',
                     'guest_file_type_check.py','kernel_file_cache.py','report_file_inventory.py']
            physical_code=out/'physical-code';physical_code.mkdir()
            for name in sources:shutil.copy2(ROOT/'scripts'/name,physical_code/name)
            (out/'physical-source-sha256.json').write_text(json.dumps({p.name:sha(p) for p in physical_code.iterdir()},indent=2)+'\n')
            from task_physical_cache import TaskPhysicalCache
            physical=TaskPhysicalCache(out,args.container,cli)
        stage='frozen-paths';controller.phase(stage)
        by_role=asyncio.run(getattr(controller,'do_'+schedule)())
        with (out/'segments.tsv').open() as stream:trace=list(csv.DictReader(stream,delimiter='\t'))
        assert len(set(by_role.values()))==len(roles)
        for role,path in executed.items():
            assert [r['segment'] for r in trace if r['rollout']==role]==path
            assert all(r['sandbox_id']==by_role[role] for r in trace if r['rollout']==role)
            result['paths'][role]=verify_path(out,role,path,workload,samples,names)
        result['checks']['sampled_actions_order_exit_codes_and_distinct_instances' if schedule!='grpo' else 'sampled_actions_order_exit_codes_and_distinct_roots']='PASS'
        if schedule=='tvcache':
            assert [[r['rollout'],r['segment']] for r in trace]==tree_plan['segments']
            result['checks']['deterministic_prefix_tree_order']='PASS'
        if schedule in ('bpo','tvcache'):
            result['checks']['restored_files_progress_and_process']='PASS'
            result['logical_action_count']=sum(len(sampled_segment(samples,r,names[s])['actions']) for r in final_roles for s in rollouts[r])
        result['action_count']=sum(p['action_count'] for p in result['paths'].values())
        result['segment_count']=len(trace)
        stage='final-state-and-official-tests';controller.phase(stage)
        async def final_check(role):
            sid=by_role[role]
            await asyncio.to_thread(runtime.mark,sid,'official-task-tests')
            diff=await asyncio.to_thread(guest,'source-diff-'+role,sid,'git -C /testbed diff -- src/language-js/needs-parens.js')
            assert diff==('' if role=='early_2' else patch.strip()),'unexpected source patch: '+role
            mode=' --baseline' if role=='early_2' else ''
            oracle=json.loads(await asyncio.to_thread(guest,'oracle-'+role,sid,'node /workspace/prettier6604-check.cjs'+mode))
            assert oracle['status']=='PASS'
            (out/('independent-'+role+'.json')).write_text(json.dumps(oracle,indent=2)+'\n')
            await asyncio.to_thread(cli,'upload-official-'+role,'upload',sid,args.official_task/'test.patch','/workspace/prettier6604-official.patch')
            content=await asyncio.to_thread(guest,'official-'+role,sid,guest_command(args.official_task,'/workspace/prettier6604-official.patch'),timeout=160)
            official=inspect_official(args.official_task,content)
            summary=official['summary']
            assert summary['numTotalTests']==2 and summary['numPendingTests']==0 and not summary['wasInterrupted']
            observed=official['parser_results']['passed_tests']+official['parser_results']['failed_tests']
            assert sorted(observed)==sorted(expected_official['parser_results']['failed_tests'])
            assert all(summary['snapshot'][k]==0 for k in ('added','updated','filesRemoved'))
            if role!='early_2':assert signature(official)==signature(expected_official)
            (out/('official-result-'+role+'.json')).write_text(json.dumps(official,indent=2)+'\n')
            result['paths'][role].update(source_patch_check='PASS',independent_check='PASS',
                    official_task_status=official['status'],official_passed=official['parser_results']['num_tests_passed'],
                    official_failed=official['parser_results']['num_tests_failed'])
        asyncio.run(joined(final_check(role) for role in final_roles))
        for role in set(roles)-set(final_roles):result['paths'][role]['official_task_status']='NOT_RUN_PREFIX_ONLY'
        result['checks']['final_source_properties_and_complete_official_outcomes']='PASS'
        (out/'terminal-storage-allocation.json').write_text(json.dumps(storage_allocation(out.parent.parent),indent=2)+'\n')
        success=True
    except BaseException as error:
        result.update(error=repr(error),failed_stage=stage)
        import traceback
        (out/'failure.txt').write_text(traceback.format_exc())
    finally:
        for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,signal.SIG_IGN)
        cleanup={}
        if physical:
            physical.close();cleanup['physical_tracer_stopped']=physical.tracer.poll() is not None
        if controller:
            removed=[]
            for sid in list(controller.live):
                try:runtime.stop(sid)
                except Exception as error:cleanup.setdefault('errors',[]).append(repr(error));success=False
                try:cli('delete-owned','delete',sid,timeout=60);removed.append(sid);controller.live.pop(sid)
                except Exception as error:cleanup.setdefault('errors',[]).append(repr(error));success=False
            cleanup['sandboxes_removed']=removed
            if controller.agentenv_templates:
                if success:
                    removed_templates=[]
                    for template in controller.agentenv_templates:
                        try:cli('delete-snapshot','template','delete',template,timeout=60);removed_templates.append(template)
                        except Exception as error:cleanup.setdefault('errors',[]).append(repr(error));success=False
                    cleanup['snapshots_removed']=removed_templates
                else:cleanup['snapshots_retained_for_failure']=controller.agentenv_templates
            try:
                remaining=json.loads(cli('list-after','list','--output','json',timeout=30))
                assert remaining==[], 'owned service has remaining sandboxes'
                cleanup['service_sandbox_list_empty']=True
            except Exception as error:cleanup.setdefault('errors',[]).append(repr(error));success=False
        if monitor:
            if monitor.poll() is not None:cleanup['monitor_unexpected_exit']=monitor.returncode;success=False
            else:
                os.killpg(monitor.pid,signal.SIGTERM)
                try:monitor.wait(timeout=5)
                except subprocess.TimeoutExpired:os.killpg(monitor.pid,signal.SIGKILL);monitor.wait(timeout=5)
                cleanup['monitor_stopped']=True
        result.update(status='PASS' if success else 'FAIL',cleanup=cleanup,controller_elapsed_ns=time.monotonic_ns()-started)
        if success:result['VMM_PSS']='MEASURED_PER_OWNED_VMM'
        (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print('status='+result['status']+' result_dir='+str(out),flush=True)
    return 0 if success else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frozen-export',type=Path,required=True)
    parser.add_argument('--official-task',type=Path,required=True)
    parser.add_argument('--expected-official-audit',type=Path,required=True)
    parser.add_argument('--role',choices=['early_1','early_2','early_3','late_1','late_2','late_3'])
    parser.add_argument('--schedule',choices=['grpo','bpo','tvcache'],default='grpo')
    parser.add_argument('--shared-cache',action='store_true',help='Pause all roots after the common 11-step prefix and count shared PFNs')
    parser.add_argument('--root-count',type=int,choices=[1,2,7])
    parser.add_argument('--container');parser.add_argument('--config-home',type=Path)
    parser.add_argument('--service-url');parser.add_argument('--output',type=Path)
    parser.add_argument('--check-only',action='store_true')
    args=parser.parse_args()
    if not args.check_only and not all((args.container,args.config_home,args.service_url,args.output)):
        parser.error('live run requires owned container, config-home, service-url, output')
    raise SystemExit(run(args))
