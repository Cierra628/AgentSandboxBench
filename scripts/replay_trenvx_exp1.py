"""Frozen Exp1 replay on the isolated service, reusing the existing runner."""
import asyncio
import csv
import json
import os
from pathlib import Path
import shutil
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


async def exercise_exp1(out, checks):
    config=json.loads((out/'config.json').read_text())
    metrics=DiagnosticMetrics(out,config.get('diagnostic_metrics',False))
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
    env=dict(ACTIONS_DIR=GUEST+'/actions',OUTPUT_DIR=OUTPUT,ACTION_TIMEOUT_SECONDS='60',
             MEMORY_SAMPLE_INTERVAL_SECONDS='0.1',GUEST_MEMORY_SAMPLING='0',DROP_GUEST_CACHES='0')
    dump(out/'replay-config.json',dict(action_manifest_sha256=sha(inputs/'actions.tsv'),
        runner_sha256=sha(inputs/'runner.sh'),independent_check_sha256=sha(inputs/'check.cjs'),
        cwd='/testbed',session_semantics='one bash per frozen action',environment=env,
        checkpoint_after=config['checkpoint_after'],reference=str(reference) if reference else None,
        cache_policy='preserve; no cache drop',measurement='correctness only; guest sampling disabled'))
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

    try:
        with metrics.phase('create-to-first-tool-success'):
            with metrics.phase('sdk-create'):
                sb=await ctl.sdk.create(template='asb-task-base',target_addr='127.0.0.1',timeout=30)
            ctl.live[sb.id]=sb
            dump(out/'parent.json',dict(id=sb.id,url=sb.get_sbx_url()))
            await tool(sb,'mkdir-inputs',f'mkdir -p {GUEST}/actions')
        for p in sorted(inputs.rglob('*')):
            if p.is_file():
                await sb.filesystem.write_bytes(GUEST+'/'+str(p.relative_to(inputs)),p.read_bytes(),timeout=30)
        hashes=await tool(sb,'guest-input-hashes',f'sha256sum {GUEST}/actions/*.sh')
        assert [line.split()[0] for line in hashes.splitlines()]==[r[1] for r in rows]
        await tool(sb,'guest-environment','uname -r; node --version; git -C /testbed rev-parse HEAD; git -C /testbed status --porcelain; printf "%s\\n" "PATH=$PATH" "HOME=$HOME" "SHELL=$SHELL" "LANG=${LANG-}"; cat /proc/mounts')
        before=json.loads(await tool(sb,'independent-before',f'node {GUEST}/check.cjs',expected=1))
        assert not before['svg']['javascript_formatted']
        checks['unfixed_seed_oracle']='PASS'
        if metrics.enabled:
            await asyncio.to_thread(metrics.snapshot,'before-replay',sb.id,config['cgroup'],CH)
        boundary=config['checkpoint_after']
        await tool(sb,'replay-prefix' if boundary else 'replay-all',f'bash {GUEST}/runner.sh',
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
            if metrics.enabled:
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
        if metrics.enabled:
            await asyncio.to_thread(metrics.snapshot,'after-replay',sb.id,config['cgroup'],CH)
            checks['vmm_membership_and_pss']='PASS'
        await collect(sb,'replay')
        steps=list(csv.DictReader((out/'replay/steps.tsv').open(),delimiter='\t'))
        assert [r['step'] for r in steps]==[r[0] for r in rows]
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
