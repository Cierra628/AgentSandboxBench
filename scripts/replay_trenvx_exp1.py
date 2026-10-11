"""Frozen Exp1 replay on the isolated service, reusing the existing runner."""
import asyncio
import csv
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time
from types import SimpleNamespace

from smoke_trenvx_service import ROOT, EXP, BIN, CH, dump, sha
from trenvx_diagnostic_metrics import DiagnosticMetrics

WORKLOAD = ROOT/'IncrementalDAX_moti/motivation/experiments/exp1-single-app-smoke/workloads/prettier-14400'
RUNNER = WORKLOAD.parents[1]/'systems/agentenv/guest-runner.sh'
GUEST = '/opt/asb-exp1'
OUTPUT = '/root/asb-exp1/replay'


async def exercise_exp1(out, checks,kvm_capture=None):
    config=json.loads((out/'config.json').read_text())
    metrics=DiagnosticMetrics(out,config.get('diagnostic_metrics',False),record_phases=config.get('record_phases',False))
    reference=Path(config['reference']) if config['reference'] else None
    rows=[line.split('\t',2) for line in (WORKLOAD/'actions.tsv').read_text().splitlines()]
    assert [r[0] for r in rows]==[f'{i:03d}' for i in range(1,27)]
    for step,digest,_ in rows:
        assert sha(WORKLOAD/'actions'/f'{step}.sh')==digest
    if reference:
        prior=json.loads((reference/'result.json').read_text())
        assert prior['status']=='PASS' and prior['full_trajectory_verified']
        assert (reference/'replay/actions.tsv').read_bytes()==(WORKLOAD/'actions.tsv').read_bytes()
    inputs=out/'inputs';inputs.mkdir()
    shutil.copytree(WORKLOAD/'actions',inputs/'actions')
    for source,name in [(WORKLOAD/'actions.tsv','actions.tsv'),(RUNNER,'runner.sh'),
                        (ROOT/'scripts/exp1_independent_check.cjs','check.cjs')]:
        shutil.copy2(source,inputs/name)
    persistent=config.get('guest_collector','original')=='persistent'
    if persistent:
        from persistent_guest_sampling import instrument
        (inputs/'runner.sh').write_text(instrument((inputs/'runner.sh').read_text()))
        shutil.copy2(ROOT/'scripts/guest_memory_collector.py',inputs/'collector.py')
    fixed_identity=config.get('fixed_git_identity',False)
    if fixed_identity:
        from fixed_runner_git_identity import instrument as instrument_identity
        (inputs/'runner.sh').write_text(instrument_identity((inputs/'runner.sh').read_text()))
    lifecycle=config.get('runner_lifecycle_diagnostic',False)
    if lifecycle:
        from guest_runner_lifecycle import instrument as instrument_lifecycle
        shutil.copy2(inputs/'runner.sh',inputs/'lifecycle-original.sh')
        (inputs/'runner.sh').write_text(instrument_lifecycle((inputs/'runner.sh').read_text(),collector=persistent))
        shutil.copy2(out/'guest_runner_lifecycle.py',inputs/'guest_runner_lifecycle.py')
    if config.get('collector_calibration',False):
        for source,name in (('calibrate_guest_collector.py','calibrate_guest_collector.py'),
                            ('persistent_guest_sampling.py','persistent_guest_sampling.py'),
                            ('guest_memory_collector.py','calibration-collector.py')):
            shutil.copy2(out/source,inputs/name)
        with (out/'build-fixed-work.log').open('wb') as log:
            subprocess.run(['gcc','-O2','-static','-Wall','-Wextra','-Werror',str(out/'collector_fixed_work.c'),
                '-o',str(inputs/'fixed-work')],check=True,stdout=log,stderr=subprocess.STDOUT,timeout=30)
    runner_phases=config.get('runner_phases',False)
    git_wait=config.get('git_wait_diagnostic',False)
    if runner_phases or lifecycle:
        with (out/'build-command-clock.log').open('wb') as log:
            subprocess.run(['gcc','-O2','-static','-Wall','-Wextra','-Werror',str(out/'command_clock.c'),
                            '-o',str(inputs/'command-clock')],check=True,stdout=log,stderr=subprocess.STDOUT,timeout=30)
    if runner_phases:
        from guest_runner_phases import instrument
        shutil.copy2(inputs/'runner.sh',inputs/'guest-runner-original.sh')
        (inputs/'runner.sh').write_text(instrument((inputs/'runner.sh').read_text(),baseline_commands=True))
        for name in ('guest_runner_phases.py','calibrate_trenvx_runner.py'):
            shutil.copy2(out/name,inputs/name)
    if git_wait:
        from guest_git_wait import instrument as instrument_wait
        (inputs/'runner.sh').write_text(instrument_wait((inputs/'runner.sh').read_text()))
        shutil.copy2(out/'guest_git_wait.py',inputs/'guest_git_wait.py')
    named_cfg=None
    if config.get('file_origin_diagnostic',False):
        shutil.copy2(out/'guest_file_origins.py',inputs/'guest_file_origins.py')
        shutil.copy2(out/'file-origin-input.json',inputs/'file-origin-input.json')
        assert sha(inputs/'guest_file_origins.py')==config['file_origin_source_sha256']
        assert sha(inputs/'file-origin-input.json')==config['file_origin_manifest_sha256']
    if config.get('guest_file_physical_diagnostic',False):
        assert kvm_capture is not None
        for name,digest in config['guest_file_physical_sources'].items():assert sha(out/name)==digest
        shutil.copy2(out/'guest_named_file_probe.py',inputs/'guest_named_file_probe.py')
    if config.get('whole_cache_diagnostic',False) or config.get('kernel_cache_calibration',False):
        for name,digest in config['whole_cache_sources'].items():assert sha(out/name)==digest
        shutil.copy2(out/'guest_whole_cache_probe.py',inputs/'guest_whole_cache_probe.py')
        if config.get('paused_cache_boundary',False):
            shutil.copy2(out/'guest_cache_marker.py',inputs/'guest_cache_marker.py')
        if config.get('kernel_file_types',False) or config.get('kernel_cache_calibration',False):
            for name in ('guest_cached_file_inventory.py','guest_named_file_probe.py'):
                shutil.copy2(out/name,inputs/name)
    if config.get('file_physical_manifest'):
        shutil.copy2(out/config['file_physical_manifest'],inputs/'named-files.json')
        shutil.copy2(ROOT/'scripts/guest_layer_file_state.py',inputs/'layer-state.py')
        assert sha(inputs/'named-files.json')==config['file_physical_manifest_sha256']
        assert sha(inputs/'layer-state.py')==config['guest_layer_state_sha256']
        named_cfg=json.loads((inputs/'named-files.json').read_text())
    env=dict(ACTIONS_DIR=GUEST+'/actions',OUTPUT_DIR=OUTPUT,ACTION_TIMEOUT_SECONDS='60',
             MEMORY_SAMPLE_INTERVAL_SECONDS=str(config.get('guest_sample_interval_seconds',.1)),
             GUEST_MEMORY_SAMPLING='1' if config.get('guest_memory_sampling',False) else '0',
             DROP_GUEST_CACHES='0')
    if persistent:env['ASB_GUEST_MEMORY_COLLECTOR']=GUEST+'/collector.py'
    if git_wait:env['ASB_GIT_WAIT_HELPER']=GUEST+'/guest_git_wait.py'
    dump(out/'replay-config.json',dict(action_manifest_sha256=sha(inputs/'actions.tsv'),
        runner_sha256=sha(inputs/'runner.sh'),independent_check_sha256=sha(inputs/'check.cjs'),
        cwd='/testbed',session_semantics='one bash per frozen action',environment=env,
        checkpoint_after=config['checkpoint_after'],reference=str(reference) if reference else None,
        runner_phase_diagnostic=runner_phases,
        runner_lifecycle_diagnostic=lifecycle,
        lifecycle_clock_sha256=sha(inputs/'command-clock') if lifecycle else None,
        collector_calibration=config.get('collector_calibration',False),
        fixed_work_binary_sha256=sha(inputs/'fixed-work') if config.get('collector_calibration',False) else None,
        fixed_git_identity=config.get('fixed_git_identity_values') if fixed_identity else None,
        fixed_git_identity_scope='runner setup/finalization commands only; no action environment export' if fixed_identity else None,
        runner_phase_calibration=config.get('runner_phase_calibration',False),
        clock_sha256=sha(inputs/'command-clock') if runner_phases else None,
        cache_policy='preserve; no cache drop',
        measurement='frozen replay; persistent asynchronous guest collector' if persistent else
                    'frozen replay; original per-step guest sampling' if config.get('guest_memory_sampling',False)
                    else 'frozen replay; periodic guest sampling disabled',
        file_physical_diagnostic=config.get('file_physical_diagnostic',False)))
    checks['frozen_input_hashes']='PASS'
    sys.path.insert(0,str(EXP))
    from controller import Controller
    os.environ.update(DATA_ROOT=str(out/'data'),TEMPLATE_ID='asb-task-base',ORCHESTRATOR_PORT='15005',
        ASB_CH_SOCKET_DIR='/tmp',ASB_TRENVX_BUILD_CONFIG=str(out/'controller.toml'),
        ASB_TEMPLATE_MANAGER=str(BIN/'template-manager'))
    ctl=Controller(SimpleNamespace(raw_dir=out/'controller',system='trenvx',timeout=30))
    ctl._templates={}
    await ctl.init_trenv()

    async def tool(sb,label,cmd,expected=0,variables=None,timeout=30):
        begin=time.monotonic_ns()
        with metrics.phase('tool:'+label):
            proc=await sb.simple_process.start(cmd,user='root',env_vars=variables or {})
            result=await proc.wait(timeout=timeout)
        dump(out/(label+'.json'),dict(cmd=cmd,stdout=result.stdout,stderr=result.stderr,
            exit_code=result.exit_code,controller_elapsed_ns=time.monotonic_ns()-begin))
        assert result.exit_code==expected,f'{label}: exit {result.exit_code}'
        return result.stdout

    async def collect(sb,label):
        await tool(sb,'pack-'+label,f'tar -C {OUTPUT} -czf /dev/shm/asb-exp1-{label}.tar.gz .')
        archive=out/(label+'.tar.gz')
        archive.write_bytes(await sb.download_file(f'/dev/shm/asb-exp1-{label}.tar.gz',timeout=30))
        with tarfile.open(archive,'r:gz') as f:
            f.extractall(out/label,filter='data')

    async def file_snapshot(label, template):
        if not config.get('file_physical_diagnostic',False):
            return
        from trenvx_file_diagnostic import snapshot, guest_observation
        raw=await tool(sb,'guest-memory-'+label,'cat /proc/meminfo; cat /proc/iomem; getconf PAGESIZE')
        guest=guest_observation(raw)
        if metrics.enabled:
            from trenvx_diagnostic_metrics import guest_meminfo
            dump(out/('guest-memory-'+label+'.json'),guest_meminfo(raw))
        state_before=None
        if named_cfg:
            state_before=json.loads(await tool(sb,'layer-state-before-'+label,
                f'python3 {GUEST}/layer-state.py {GUEST}/named-files.json'))
            assert state_before['status']=='PASS'
        with metrics.phase('file-physical:'+label):
            await asyncio.to_thread(snapshot,out,label,sb.id,template,config['cgroup'],CH,guest,
                                   named_cfg['files'] if named_cfg else None)
        if named_cfg:
            state_after=json.loads(await tool(sb,'layer-state-after-'+label,
                f'python3 {GUEST}/layer-state.py {GUEST}/named-files.json --content'))
            for a,b in zip(state_before['files'],state_after['files']):
                assert all(a[key]==b[key] for key in ('path','origin','candidate','visible','underlying'))
            dump(out/'file-physical'/label/'guest-layer-proof.json',dict(before=state_before,after=state_after,
                hash_scope='full content read only after the physical measurement'))
        checks['file_physical_'+label]='PASS'

    async def guest_boundary(label):
        if not metrics.enabled:
            return
        from trenvx_diagnostic_metrics import guest_meminfo
        raw=await tool(sb,'guest-memory-'+label,'cat /proc/meminfo; getconf PAGESIZE')
        dump(out/('guest-memory-'+label+'.json'),guest_meminfo(raw))

    try:
        with metrics.phase('create-to-first-tool-success'):
            with metrics.phase('sdk-create'):
                sb=await ctl.sdk.create(template='asb-task-base',target_addr='127.0.0.1',timeout=30)
            ctl.live[sb.id]=sb
            dump(out/'parent.json',dict(id=sb.id,url=sb.get_sbx_url()))
            await tool(sb,'mkdir-inputs',f'mkdir -p {GUEST}/actions')
        if config.get('guest_file_physical_diagnostic',False) or config.get('whole_cache_diagnostic',False):
            from trenvx_named_guest_files import prepare_ram_capture
            await prepare_ram_capture(sb,out,tool,kvm_capture)
            checks['owned_ram_registration']='PASS'
        for p in sorted(inputs.rglob('*')):
            if p.is_file():
                await sb.filesystem.write_bytes(GUEST+'/'+str(p.relative_to(inputs)),p.read_bytes(),timeout=30)
        hashes=await tool(sb,'guest-input-hashes',f'sha256sum {GUEST}/actions/*.sh')
        assert [line.split()[0] for line in hashes.splitlines()]==[r[1] for r in rows]
        await tool(sb,'guest-environment','uname -r; node --version; git -C /testbed rev-parse HEAD; git -C /testbed status --porcelain; printf "%s\\n" "PATH=$PATH" "HOME=$HOME" "SHELL=$SHELL" "LANG=${LANG-}"; cat /proc/mounts')
        before=json.loads(await tool(sb,'independent-before',f'node {GUEST}/check.cjs',expected=1))
        assert not before['svg']['javascript_formatted']
        checks['unfixed_seed_oracle']='PASS'
        if config.get('kernel_file_types',False) or config.get('kernel_cache_calibration',False):
            from trenvx_whole_cache import run as calibrate_kernel
            with metrics.phase('kernel-cache-calibration'):
                calibration=await calibrate_kernel(sb,out,tool,kvm_capture,paused_boundary=True,kernel_file_types=True,name='kernel-cache-calibration')
            assert calibration['status']=='PASS'
            checks['kernel_cache_fixture_before_replay']='PASS'
        if git_wait:
            await tool(sb,'git-effective-config',
                'command -v git; git --version; git -C /testbed config --show-origin --list; '
                'git -C /testbed rev-parse --git-dir; ls -la /testbed/.git/hooks; '
                'command -v strace || true; cat /proc/sys/kernel/yama/ptrace_scope 2>/dev/null || true')
        if runner_phases or lifecycle:
            await tool(sb,'clock-executable',f'chmod 755 {GUEST}/command-clock')
        if config.get('collector_calibration',False):
            await tool(sb,'fixed-work-executable',f'chmod 755 {GUEST}/fixed-work')
            from calibrate_guest_collector import run as calibrate_collector
            await calibrate_collector(sb,out,tool)
            checks['fixed_work_collector_calibration']='PASS'
        if config.get('lifecycle_calibration',False):
            calibration_error=None
            try:
                await tool(sb,'lifecycle-calibration',f'python3 {GUEST}/guest_runner_lifecycle.py --calibrate {GUEST}',timeout=120)
            except BaseException as exc:calibration_error=exc
            try:
                await tool(sb,'pack-lifecycle-calibration',f'tar -C {GUEST}/lifecycle-calibration -czf /dev/shm/asb-lifecycle-calibration.tar.gz .')
                archive=out/'lifecycle-calibration.tar.gz'
                archive.write_bytes(await sb.download_file('/dev/shm/asb-lifecycle-calibration.tar.gz',timeout=30))
                with tarfile.open(archive,'r:gz') as f:
                    f.extractall(out/'lifecycle-calibration',filter='data')
            except BaseException as download_error:
                if calibration_error:
                    raise BaseExceptionGroup('calibration and data download failed',[calibration_error,download_error])
                raise
            if calibration_error:raise calibration_error
            from guest_runner_lifecycle import audit_calibration
            dump(out/'lifecycle-calibration-audit.json',audit_calibration(out/'lifecycle-calibration'))
            checks['lifecycle_calibration']='PASS'
        if config.get('git_identity_diagnostic',False):
            from guest_git_wait import download_identity_probe, audit_identity
            await download_identity_probe(sb,out,tool)
            dump(out/'identity-audit.json',audit_identity(out/'identity-probe'))
            checks['identity_diagnostic_records']='PASS'
        if config.get('runner_phase_calibration',False):
            await tool(sb,'runner-calibration',f'python3 {GUEST}/calibrate_trenvx_runner.py --guest {GUEST}',timeout=120)
            await tool(sb,'pack-calibration',f'tar -C {GUEST}/calibration -czf /dev/shm/asb-runner-calibration.tar.gz .')
            archive=out/'runner-calibration.tar.gz'
            archive.write_bytes(await sb.download_file('/dev/shm/asb-runner-calibration.tar.gz',timeout=30))
            with tarfile.open(archive,'r:gz') as f:
                f.extractall(out/'runner-calibration',filter='data')
            from calibrate_trenvx_runner import audit
            dump(out/'runner-calibration-audit.json',audit(out/'runner-calibration'))
            checks['runner_phase_calibration']='PASS'
        if metrics.enabled:
            await guest_boundary('before-replay')
            await asyncio.to_thread(metrics.snapshot,'before-replay',sb.id,config['cgroup'],CH)
        boundary=config['checkpoint_after']
        replay_command=f'bash {GUEST}/runner.sh'
        if runner_phases:
            replay_command=f'{GUEST}/command-clock {OUTPUT}/runner-envelope.json '+replay_command
        if lifecycle:
            # The output directory must exist before the clock writes its final receipt.
            replay_command=f'{GUEST}/command-clock {OUTPUT}/lifecycle-envelope.json '+replay_command
        await tool(sb,'replay-prefix' if boundary else 'replay-all',replay_command,
            variables=dict(env,START_STEP='001',END_STEP=boundary or '026'),timeout=180)
        if boundary:
            boundary_idx=int(boundary)
            next_step=f'{boundary_idx+1:03d}'
            await collect(sb,'prefix')
            prefix=list(csv.DictReader((out/'prefix/steps.tsv').open(),delimiter='\t'))
            assert [r['step'] for r in prefix]==[f'{i:03d}' for i in range(1,boundary_idx+1)]
            expected_prefix_failures={step:127 for step in ('008','016') if int(step)<=boundary_idx}
            assert {r['step']:int(r['exit_code']) for r in prefix if int(r['exit_code'])}==expected_prefix_failures
            if boundary=='022':
                source_before=await tool(sb,'source-before',
                    'sha256sum /testbed/src/language-html/utils/index.js')
                diff_before=await tool(sb,'source-diff-before',
                    'git -C /testbed diff HEAD -- src/language-html/utils/index.js')
                assert any(line.startswith('+') and 'node.fullName === "svg:script" ||' in line
                           for line in diff_before.splitlines())
            if metrics.enabled:
                await guest_boundary('before-checkpoint')
                await asyncio.to_thread(metrics.snapshot,'before-checkpoint',sb.id,config['cgroup'],CH)
            with metrics.phase('checkpoint'):
                template=await ctl.capture_template(sb,f'exp1-after-{boundary}')
            checks[f'checkpoint_after_{boundary}']='PASS'
            parent_id=sb.id
            with metrics.phase('parent-delete'):
                await ctl.delete(sb)
            with metrics.phase('restore-to-first-tool-success'):
                with metrics.phase('restore-interface'):
                    sb=await ctl.start('exp1-continuation',template=template,parent=parent_id)
                dump(out/'checkpoint.json',dict(after_step=boundary,next_step=next_step,template=template,
                     parent=parent_id,restored=sb.id,restore_semantics='file-state-only'))
                mounts=await tool(sb,'restore-environment','cat /proc/mounts; cat /proc/sys/kernel/random/boot_id')
            assert any('/dev/pmem1 ' in line and 'ro,' in line and 'dax=always' in line for line in mounts.splitlines())
            checks['file_restore_interface']='PASS'
            await file_snapshot('restored-before-source-read',template)
            if metrics.enabled:
                if not config.get('file_physical_diagnostic',False):
                    await guest_boundary('restored-before-source-read')
                await asyncio.to_thread(metrics.snapshot,'restored',sb.id,config['cgroup'],CH)
            if boundary=='022':
                source_after=await tool(sb,'source-after',
                    'sha256sum /testbed/src/language-html/utils/index.js')
                diff_after=await tool(sb,'source-diff-after',
                    'git -C /testbed diff HEAD -- src/language-html/utils/index.js')
                assert source_after==source_before and diff_after==diff_before
                checks['modified_source_preserved']='PASS'
            await tool(sb,'replay-continuation',f'bash {GUEST}/runner.sh',
                variables=dict(env,START_STEP=next_step,END_STEP='026'),timeout=180)
            await file_snapshot('after-continuation',template)
        if metrics.enabled:
            if not (boundary and config.get('file_physical_diagnostic',False)):
                await guest_boundary('after-replay')
            await asyncio.to_thread(metrics.snapshot,'after-replay',sb.id,config['cgroup'],CH)
            checks['vmm_membership_and_pss']='PASS'
        await collect(sb,'replay')
        if config.get('guest_memory_sampling',False):
            with (out/'replay/guest-memory.tsv').open() as stream:
                memory_rows=list(csv.DictReader(stream,delimiter='\t'))
            if persistent:
                receipt=json.loads((out/'replay/collector-status.json').read_text())
                assert receipt['status']=='PASS' and len(memory_rows)>=2
                assert receipt.get('samples_in_file',receipt['samples'])==len(memory_rows)
            else:
                assert len(memory_rows)>=55, 'guest memory samples missing'
                assert {row['step'] for row in memory_rows if row['phase']=='before'}=={r[0] for r in rows}
                assert {row['step'] for row in memory_rows if row['phase']=='after'}=={r[0] for r in rows}
            checks['guest_memory_sampling']='PASS'
        steps=list(csv.DictReader((out/'replay/steps.tsv').open(),delimiter='\t'))
        assert [r['step'] for r in steps]==[r[0] for r in rows]
        if lifecycle:
            from guest_runner_lifecycle import collect as collect_lifecycle
            dump(out/'runner-lifecycle.json',collect_lifecycle(out/'replay',persistent,
                sum(int(r['duration_ns']) for r in steps),json.loads((out/'replay-all.json').read_text())['controller_elapsed_ns']))
            checks['runner_lifecycle_diagnostic']='PASS'
        if runner_phases:
            from guest_runner_phases import collect as collect_phases
            phase_input=out/'replay'
            if git_wait:
                # Keep the strict existing phase parser unchanged. Audit native traces separately.
                phase_input=out/'phase-input';phase_input.mkdir()
                for p in (out/'replay').iterdir():
                    if p.is_file() and not any(p.name.endswith(s) for s in ('.jsonl','.performance.txt','.stop')):
                        shutil.copy2(p,phase_input/p.name)
            dump(out/'runner-phases.json',collect_phases(phase_input,
                sum(int(r['duration_ns']) for r in steps),baseline_commands=True))
            checks['runner_phase_diagnostic']='PASS'
        if boundary:
            assert steps[:boundary_idx]==prefix
            assert (out/'prefix/baseline-tree.txt').read_bytes()==(out/'replay/baseline-tree.txt').read_bytes()
            checks['prefix_state_preserved']='PASS'
        failures={r['step']:int(r['exit_code']) for r in steps if int(r['exit_code'])}
        assert failures=={'008':127,'016':127},failures
        assert (out/'replay/actions.tsv').read_bytes()==(WORKLOAD/'actions.tsv').read_bytes()
        output24=(out/'replay/stdout/024.log').read_text()
        assert '    document.addEventListener("DOMContentLoaded", () => {' in output24
        assert '      const node = document.getElementById("lastStroke");' in output24
        patch=out/'replay/patch.diff'
        assert patch.stat().st_size>0
        after=json.loads(await tool(sb,'independent-after',f'node {GUEST}/check.cjs'))
        assert all(r['javascript_formatted'] and r['idempotent'] for r in after.values())
        correctness=dict(action_order='PASS',input_hashes='PASS',expected_failures=failures,
             action_024_oracle='PASS',independent_svg_properties='PASS',official_full_tests='NOT_RUN',
             patch_sha256=sha(patch),patch_reference_match='NOT_CHECKED')
        if reference:
            assert patch.read_bytes()==(reference/'replay/patch.diff').read_bytes()
            assert output24==(reference/'replay/stdout/024.log').read_text()
            assert (out/'replay/original-head.txt').read_bytes()==(reference/'replay/original-head.txt').read_bytes()
            correctness['patch_reference_match']='PASS'
            checks['patch_matches_full_replay']='PASS'
        dump(out/'correctness.json',correctness)
        checks['exp1_replay_correctness']='PASS'
        if config.get('official_task'):
            from exp1_official_task import guest_command, grade
            task=Path(config['official_task'])
            patch_path=GUEST+'/official-test.patch'
            await sb.filesystem.write_bytes(patch_path,(task/'test.patch').read_bytes(),timeout=30)
            official=await tool(sb,'official-task-tests',guest_command(task,patch_path,guest_output=GUEST),timeout=145)
            dump(out/'official-task-result.json',grade(task,official))
            checks['official_task_tests']='PASS'
            correctness['official_task_tests']='PASS'
            dump(out/'correctness.json',correctness)
        if config.get('whole_cache_diagnostic',False):
            from trenvx_whole_cache import run as run_whole_cache
            with metrics.phase('whole-cache-analysis'):
                whole=await run_whole_cache(sb,out,tool,kvm_capture,paused_boundary=config.get('paused_cache_boundary',False),kernel_file_types=config.get('kernel_file_types',False))
            checks['whole_cache_collection']=whole['status']
        if config.get('file_origin_diagnostic',False):
            command=f'python3 {GUEST}/guest_file_origins.py {GUEST}/file-origin-input.json'
            before=json.loads(await tool(sb,'file-origin-metadata',command))
            if config.get('guest_file_physical_diagnostic',False):
                from trenvx_named_guest_files import run
                physical=await run(sb,out,tool,kvm_capture,before)
                checks['upper_file_physical_proof']=physical['status']
                assert physical['status']=='PASS','upper file page classification incomplete; see raw results'
                if config.get('base_file_physical_diagnostic',False):
                    from trenvx_named_guest_files import run_base_files
                    combined=run_base_files(out,before)
                    checks['combined_file_physical_proof']=combined['status']
            after=json.loads(await tool(sb,'file-origin-content',command+' --content'))
            dump(out/'file-origin-result.json',dict(before=before,after=after,
                scope='after frozen replay and official task tests; reads may warm cache; no physical page count'))
            assert before['status']==after['status']=='PASS'
            for left,right in zip(before['files'],after['files']):
                assert all(left[key]==right[key] for key in ('path','status','origin','candidate','visible','underlying','proof'))
                assert right['content_verified']
            assert len(before['files'])==len(after['files'])==3
            checks['merged_file_origins']='PASS'
            if config.get('base_file_physical_diagnostic',False):
                hashes=json.loads((out/'base-guest-files/file-hashes.json').read_text())
                assert hashes=={row['path']:row['sha256'] for row in after['files'] if row['origin']=='base'}
                checks['base_file_contents']='PASS'
            if config.get('monitoring_acceptance',False) or config.get('whole_cache_diagnostic',False) or config.get('monitoring_control'):
                from exp1_official_task import guest_command, grade
                checked_after=json.loads(await tool(sb,'independent-post-scan',f'node {GUEST}/check.cjs'))
                assert checked_after==json.loads(json.loads((out/'independent-after.json').read_text())['stdout'])
                post_patch=await tool(sb,'patch-post-scan','git -C /testbed diff -- src/language-html/utils/index.js')
                assert post_patch==patch.read_text(),'task patch changed during analysis'
                post_official=await tool(sb,'official-task-post-scan',guest_command(
                    Path(config['official_task']),GUEST+'/official-test.patch',guest_output=GUEST,
                    apply_test_patch=False),timeout=145)
                post_grade=grade(Path(config['official_task']),post_official)
                assert post_grade['status']=='PASS'
                dump(out/'official-task-post-scan-result.json',post_grade)
                dump(out/'post-scan-correctness.json',dict(status='PASS',
                    independent_properties='PASS',task_patch_unchanged='PASS',official_task_tests='PASS',
                    scope='final task state after physical/source probes; no performance-impact comparison'))
                checks['post_scan_correctness']='PASS'
    finally:
        errors=[]
        for sandbox in list(ctl.live.values()):
            try:
                await asyncio.wait_for(ctl.delete(sandbox),timeout=45)
            except BaseException as exc:
                errors.append(repr(exc))
        checks['sdk_cleanup']='PASS' if not errors else 'FAIL'
        dump(out/'sdk-cleanup.json',dict(errors=errors))
        if errors:
            raise RuntimeError(f'sandbox cleanup failed: {errors}')
