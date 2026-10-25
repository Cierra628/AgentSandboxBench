"""Reuse the pinned Exp2 four-round runner on one owned AgentENV service."""
import argparse
import ast
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
from sandbox_runtime_monitor import RuntimeMonitor

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT/'configs/exp2-prettier6604.json'


def sha(path):
    with Path(path).open('rb') as stream: return hashlib.file_digest(stream, 'sha256').hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False)+'\n')


def rows(path):
    with path.open() as stream: return list(csv.DictReader(stream, delimiter='\t'))


def validate():
    cfg = json.loads(CONFIG.read_text()); workload = ROOT/cfg['workload']
    assert cfg['experiment']=='exp2' and cfg['memory_mib']==4096 and cfg['cpu']==2
    assert cfg['source_image']==json.loads((ROOT/'configs/prettier6604-backbone.json').read_text())['source_image']
    for name, digest in cfg['file_sha256'].items(): assert sha(workload/name)==digest, name
    for n in range(1,5):
        manifest = rows(workload/f'rounds/{n}/actions.tsv')
        assert [r['step'] for r in manifest]==[f'{s:03}' for s in range(1,9)]
        assert all(r['sha256']==sha(workload/f'rounds/{n}/actions/{r["step"]}.sh') for r in manifest)
    return cfg, workload


def final_source(base, action):
    fragment = action.split("python3 - <<'PY'\n",1)[1].rsplit('\nPY',1)[0]
    strings = {}
    for item in ast.parse(fragment).body:
        if isinstance(item,ast.Assign) and isinstance(item.targets[0],ast.Name) and item.targets[0].id in ('old','new'):
            strings[item.targets[0].id]=ast.literal_eval(item.value)
    assert base.count(strings['old'])==1
    return base.replace(strings['old'], strings['new'], 1)


def check_steps(directory, number, cfg, workload):
    steps = rows(directory/'steps.tsv'); manifest = rows(directory/'actions.tsv')
    assert manifest==rows(workload/f'rounds/{number}/actions.tsv')
    assert [s['step'] for s in steps]==[f'{i:03}' for i in range(1,9)]
    assert all(int(s['round'])==number and int(s['duration_ns'])>0 for s in steps)
    assert [int(s['exit_code']) for s in steps]==cfg['expected_exit_codes'][str(number)]
    return sum(int(s['duration_ns']) for s in steps)


def verify_boundary(before, after, probe_before, probe_after):
    assert before==after, 'restored files, patch or completed-round progress differ'
    for key in ('pid','start_ticks','boot_id','anonymous_sha256','open_fd_sha256','pathname_sha256'):
        assert probe_before[key]==probe_after[key], 'restored process state differs: '+key
    assert probe_after['count']>probe_before['count']


def initialize_progress(cli, sid):
    cli('create-workspace','exec',sid,'mkdir','-p','/workspace')
    cli('initialize-progress','exec',sid,'bash','-lc','printf 0 > /workspace/asb-exp2-progress')


def run(args, bridge=None):
    cfg, workload = validate()
    if args.check_only: print('status=VALIDATED experiment=exp2 cache_mode='+args.cache_mode); return 0
    out=args.output.resolve(); out.mkdir(exist_ok=False)
    if bridge is None:
        assert out.parent.name=='controller' and out.parents[3]==ROOT/'.artifacts/storage-isolated-service'
    else:
        assert out==bridge.out/'replay'
    env=dict(os.environ,PATH=str(ROOT/'runtime/bin')+':'+os.environ['PATH'],XDG_CONFIG_HOME=str(args.config_home))
    events=[]; lock=threading.Lock(); counter=0; live=set(); snapshots=[]; started=time.monotonic_ns(); stage='setup'
    result=dict(status='FAIL',experiment='exp2',backend='trenvx' if bridge else 'agentenv',cache_mode=args.cache_mode,task_id=cfg['task_id'],source_image=cfg['source_image'],
        cpu=2,memory_mib=4096,action_count=0,checks={},restore_semantics='complete VM state',official_task_status='NOT_RUN',
        scope='original four rounds/32 commands/four snapshots; original 20 ms runner plus 100 ms continuous metrics and explicit state probes; focused Jest tests only')
    def call(label, argv, timeout=60):
        nonlocal counter
        with lock: index=counter; counter+=1
        begin=time.monotonic_ns(); stamp=time.time_ns(); code=None
        try:
            if bridge is None:
                with (out/f'{index:04}-{label}.stderr').open('x') as log:
                    p=subprocess.run(list(map(str,argv)),cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=log,text=True,timeout=timeout)
                stdout=p.stdout;code=p.returncode
            else:
                stdout=bridge.command(label,list(argv)[1:],timeout);code=0
                (out/f'{index:04}-{label}.stderr').write_text('')
            (out/f'{index:04}-{label}.stdout').write_text(stdout)
            if code: raise RuntimeError(label+': exit '+str(code))
            return stdout.strip()
        finally:
            with lock:
                events.append(dict(stage=label,timestamp_ns=stamp,start_monotonic_ns=begin,duration_ns=time.monotonic_ns()-begin,exit_code=code))
                write(out/'events.json',events)
    def cli(label,*argv,timeout=60): return call(label,[ROOT/'runtime/bin/aenv',*argv],timeout)
    def probe(sid): return json.loads(cli('process-state','exec',sid,'python3','/workspace/asb-checkpoint-probe.py','get'))
    state_code="""import hashlib,json,pathlib,subprocess
files=['/testbed/src/language-js/printer-estree.js','/tmp/mixfs-cases-round2.ts','/tmp/mixfs-cases-round3.ts','/tmp/mixfs-cases-round4.ts','/workspace/asb-exp2-progress']
value={'files':{n:hashlib.sha256(pathlib.Path(n).read_bytes()).hexdigest() if pathlib.Path(n).exists() else None for n in files},'progress':pathlib.Path('/workspace/asb-exp2-progress').read_text(),'head':subprocess.check_output(['git','-C','/testbed','rev-parse','HEAD'],text=True).strip(),'patch':subprocess.check_output(['git','-C','/testbed','diff','--binary'],text=True)}
print(json.dumps(value))"""
    def state(sid): return json.loads(cli('files-and-progress','exec',sid,'python3','-c',state_code))
    def memory(sid):
        text=cli('memory-boundary','exec',sid,'cat','/proc/meminfo')
        return {line.split(':',1)[0]:int(line.split()[1]) for line in text.splitlines() if len(line.split())>=3 and line.split()[2]=='kB'}
    runtime=RuntimeMonitor(out,cli,container=args.container,batch_control=bridge is not None)
    if bridge is not None:runtime.host_binding=bridge.binding
    def cancel(*_): raise RuntimeError('owned Exp2 interrupted')
    for sig in (signal.SIGTERM,signal.SIGINT): signal.signal(sig,cancel)
    try:
        if bridge is None:
            labels=json.loads(call('service-labels',['docker','inspect','--format','{{json .Config.Labels}}',args.container]))
            assert labels.get('agentsandboxbench.run_id')==out.parents[2].name
        else:
            result['template_provenance']=bridge.provenance
        shutil.copytree(workload,out/'frozen-input');write(out/'effective-input.json',cfg)
        (out/'rounds').mkdir();(out/'boundaries').mkdir()
        (out/'sandbox-ids.tsv').write_text('role\tsandbox_id\tparent_id\tcreated_from\n')
        stage='cold-create'
        if bridge is None:
            sid=call(stage,[sys.executable,ROOT/'scripts/agentenv_cold_start.py','--config-home',args.config_home,'--service-url',args.service_url,'--image',cfg['source_image']],610)
        else:
            sid=cli('template-create','start','--cold',cfg['source_image'],timeout=180)
        live.add(sid)
        base=cli('initial-source','exec',sid,'cat','/testbed/src/language-js/printer-estree.js')+'\n'
        expected=final_source(base,(workload/'rounds/3/actions/003.sh').read_text())
        result['expected_final_source_sha256']=hashlib.sha256(expected.encode()).hexdigest()
        initialize_progress(cli,sid)
        assert state(sid)['head']==cfg['base_commit']
        cli('upload-rounds','upload',sid,workload/'rounds','/workspace/mixfs-rounds')
        cli('upload-original-runner','upload',sid,workload/'guest-runner.sh','/workspace/mixfs-guest-runner.sh')
        cli('upload-process-probe','upload',sid,ROOT/'scripts/guest_checkpoint_probe.py','/workspace/asb-checkpoint-probe.py')
        cli('start-process-probe','exec',sid,'bash','-lc','nohup python3 /workspace/asb-checkpoint-probe.py serve </dev/null >/workspace/asb-checkpoint-probe.log 2>&1 &')
        with (out/'sandbox-ids.tsv').open('a') as f:f.write(f'round-1\t{sid}\t\tclean-image\n')
        runtime.start(sid,'round-1','round-1'); transitions=[]; action_sum=0
        for n in range(1,5):
            stage='round-'+str(n)
            command=f"ROUND={n} ACTIONS_ROOT=/workspace/mixfs-rounds ACTION_TIMEOUT_SECONDS=300 MEMORY_SAMPLE_INTERVAL_SECONDS=0.02 OUTPUT_DIR=/workspace/artifacts/{n} bash /workspace/mixfs-guest-runner.sh"
            cli(stage,'exec',sid,'bash','-lc',command,timeout=360)
            cli('download-round-'+str(n),'download',sid,f'/workspace/artifacts/{n}',out/'rounds',timeout=60)
            action_sum+=check_steps(out/'rounds'/str(n),n,cfg,workload);result['action_count']+=8
            cli('update-progress','exec',sid,'bash','-lc',f'printf {n} > /workspace/asb-exp2-progress')
            runtime.stop(sid,'checkpoint-'+str(n)); before=state(sid); probe_before=probe(sid)
            assert before['progress']==str(n)
            if n>=3: assert before['files']['/testbed/src/language-js/printer-estree.js']==result['expected_final_source_sha256']
            mem_before=memory(sid);clear_ns=0
            if args.cache_mode=='drop':
                clear_start=time.monotonic_ns()
                cli('guest-drop-caches-'+str(n),'exec',sid,'bash','-lc','sync; echo 3 > /proc/sys/vm/drop_caches')
                clear_ns=time.monotonic_ns()-clear_start
            mem_snapshot=memory(sid);alias='asb-exp2-'+out.parents[2].name+'-r'+str(n);snapshots.append(alias)
            stage='checkpoint-'+str(n);begin=time.monotonic_ns();cli(stage,'snapshot','create',sid,'--name',alias,timeout=180);checkpoint_ns=time.monotonic_ns()-begin
            rebuild_ns=0
            if bridge is not None:
                stage='promote-template-'+str(n);begin=time.monotonic_ns();cli(stage,'promote',sid,alias,timeout=180);rebuild_ns=time.monotonic_ns()-begin
            original=sid;cli('delete-original','delete',sid);live.remove(sid)
            stage='restore-'+str(n);begin=time.monotonic_ns();sid=cli(stage,'start',alias,'--detach','--timeout','1800',timeout=180);live.add(sid)
            assert sid!=original
            mem_restored=memory(sid);cli('tool-ready','exec',sid,'true');restore_ns=time.monotonic_ns()-begin
            check_start=time.monotonic_ns();after=state(sid);probe_after=probe(sid);verify_boundary(before,after,probe_before,probe_after)
            proof=dict(status='PASS',after_round=n,next_round=n+1 if n<4 else None,before=before,after=after,probe_before=probe_before,probe_after=probe_after,
                memory_before_drop_kib=mem_before,memory_at_snapshot_kib=mem_snapshot,memory_immediate_restored_kib=mem_restored)
            write(out/'boundaries'/f'{n}.json',proof)
            transitions.append(dict(after_round=n,checkpoint_ns=checkpoint_ns,restore_ns=restore_ns,image_rebuild_ns=rebuild_ns,state_verification_ns=time.monotonic_ns()-check_start,guest_cache_clear_ns=clear_ns))
            write(out/'transitions.json',transitions)
            role='round-'+str(n+1) if n<4 else 'final-restored'
            with (out/'sandbox-ids.tsv').open('a') as f:f.write(f'{role}\t{sid}\t{original}\t{alias}\n')
            runtime.start(sid,role,role,reuse_code=bridge is not None)
        stage='final-checks';time.sleep(.25)
        assert cfg['oracle'] in (out/'rounds/4/stdout/004.log').read_text()
        final=state(sid);assert final['progress']=='4' and final['files']['/testbed/src/language-js/printer-estree.js']==result['expected_final_source_sha256']
        write(out/'final-state.json',final)
        result.update(status='PASS',guest_action_sum_ns=action_sum,transitions=transitions,checkpoint_count=4,restore_count=4)
        result['checks'].update(actions_hash_order_expected_exits='PASS',four_restored_files_progress_and_process='PASS',original_oracle_and_final_source='PASS')
    except Exception as error:
        result.update(error=repr(error),failed_stage=stage);(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        errors=[]
        for owned in list(live):
            try:runtime.stop(owned);cli('cleanup-owned-vm','delete',owned);live.remove(owned)
            except Exception as error:errors.append(repr(error))
        removed=[]
        for alias in reversed(snapshots):
            try:cli('cleanup-owned-snapshot','template','delete',alias);removed.append(alias)
            except Exception as error:errors.append(repr(error))
        cleanup=dict(owned_vms_removed=not live,owned_snapshots_removed=len(removed)==len(snapshots),collector_sessions_complete=bool(runtime.sessions) and all(s.get('status')=='PASS' for s in runtime.sessions))
        try:cleanup['service_sandbox_list_empty']=json.loads(cli('list-after','list','--output','json'))==[]
        except Exception:cleanup['service_sandbox_list_empty']=False
        result.update(cleanup=cleanup,controller_elapsed_ns=time.monotonic_ns()-started)
        if errors or not all(cleanup.values()):result.update(status='FAIL',cleanup_errors=errors)
        write(out/'result.json',result)
    print('status='+result['status']+' result_dir='+str(out));return 0 if result['status']=='PASS' else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache-mode',choices=['preserve','drop'],required=True);parser.add_argument('--check-only',action='store_true')
    for name in ('output','config-home'):parser.add_argument('--'+name,type=Path)
    for name in ('container','service-url'):parser.add_argument('--'+name)
    raise SystemExit(run(parser.parse_args()))
