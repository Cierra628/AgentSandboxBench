"""Short frozen Sphinx prefix and matching sampling variants; no model or task repair."""
import asyncio
import csv
import importlib.util
import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

from sphinx7590_inputs import ROOT,validate,verify_image_background,check_pipeline_exit
from xarray6992_inputs import frozen_shell
from xarray6992_persistent_sampling import instrument
from prettier6604_inputs import sha


def without_sampling(text):
    begin=text.index('sample_memory() {');end=text.index('sample_memory baseline 000',begin)
    start='  monitor_step "$step" & monitor_pid=$!'
    stop='  kill "$monitor_pid" 2>/dev/null || true\n  wait "$monitor_pid" 2>/dev/null || true\n'
    assert text.count(start)==text.count(stop)==1
    return (text[:begin]+'sample_memory() { :; }\nsample_vmstat() { :; }\n'+text[end:]).replace(start,'').replace(stop,'')


def fingerprint(aenv,sid,label):
    files=['sphinx/domains/cpp.py','sphinx/util/cfamily.py','setup.py','tox.ini']
    code="cd /testbed && python3 - <<'EOF'\nimport json,hashlib,subprocess\nfrom pathlib import Path\nprint(json.dumps(dict(head=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),files={p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in "+repr(files)+"},status=subprocess.check_output(['git','status','--porcelain'],text=True),patch=subprocess.check_output(['git','diff','--binary'],text=True))))\nEOF"
    return json.loads(aenv(label,'exec',sid,'bash','-lc',code).decode())


def check_prefix(out,cfg,reference,sampling,pipeline_diagnostic=False):
    directory=out/'rollouts/backbone/read_repo'
    reference=reference/'rollouts/backbone/read_repo'
    assert sha(directory/'actions.tsv')==cfg['input_sha256']['segments/read_repo/actions.tsv']==sha(reference/'actions.tsv')
    with (directory/'steps.tsv').open() as stream:steps=list(csv.DictReader(stream,delimiter='\t'))
    assert [r['step'] for r in steps]==[f'{i:03}' for i in range(1,7)]
    actual=[int(r['exit_code']) for r in steps]
    if pipeline_diagnostic:
        assert not sampling
        with (directory/'actions.tsv').open() as stream:first=next(csv.DictReader(stream,delimiter='\t'))
        check_pipeline_exit('read_repo','001',cfg['expected_exit_codes']['read_repo'][0],actual[0],first['command'],
            (directory/'stdout/001.log').read_bytes(),(directory/'stderr/001.log').read_bytes(),(directory/'patch.diff').read_bytes())
        assert actual[1:]==cfg['expected_exit_codes']['read_repo'][1:]
    else:assert actual==cfg['expected_exit_codes']['read_repo']
    assert all(r['branch']=='backbone' and r['round']=='read_repo' for r in steps)
    for name in ('stdout','stderr'):
        for row in steps:
            key=name+'/'+row['step']+'.log'
            assert sha(directory/key)==sha(reference/key),key
    assert sha(directory/'patch.diff')==sha(reference/'patch.diff')
    with (directory/'guest-memory.tsv').open() as stream:memory=list(csv.DictReader(stream,delimiter='\t'))
    if sampling == 'boundary':
        assert len(memory)==14 and not (directory/'collector-status.json').exists()
    elif sampling:
        collector=json.loads((directory/'collector-status.json').read_text())
        assert collector['status']=='PASS' and collector['samples']==len(memory)>=2
    else:assert not memory and not (directory/'collector-status.json').exists()
    result=dict(status='PASS',action_count=6,guest_action_sum_ns=sum(int(r['duration_ns']) for r in steps),
                output_matches_archived_full_replay=True,guest_sample_count=len(memory),
                guest_observed_peak_bytes=max((int(r['MemTotal_kib'])-int(r['MemFree_kib']))*1024 for r in memory) if memory else None,
                scope='first six original actions only; exact order, exits, stdout/stderr and patch; not full trajectory or task grading')
    if pipeline_diagnostic:result.update(strict_prefix_status='PASS' if actual==cfg['expected_exit_codes']['read_repo'] else 'FAIL',scope='six-step diagnostic prefix; approved first pipeline only, all outputs and patch matched; not full replay')
    return result


def controller_module(workload):
    vendor=ROOT/'IncrementalDAX_moti/motivation/experiments/exp3-RL-fork'
    sys.path.insert(0,str(vendor))
    spec=importlib.util.spec_from_file_location('metrics_prefix_controller',vendor/'controller.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);module.WORKLOAD=workload
    return module


def run_prefix(aenv,sid,out,task,sampling,pipeline_diagnostic=False):
    cfg,workload,_,_=validate(task['inputs'])
    reference=task['reference']
    reference_cfg=json.loads((reference/'prepared-input/config.json').read_text())
    assert cfg==reference_cfg and json.loads((reference/'result.json').read_text())['checks']['actions_hash_order_expected_failures']=='PASS'
    shutil.copytree(task['inputs'],out/'prepared-input')
    shutil.copytree(workload,out/'frozen-input')
    runner=out/'frozen-input/guest-runner.sh';original=runner.read_text()
    shutil.copy2(runner,runner.with_name('guest-runner.original.sh'))
    runner.write_text(instrument(original) if sampling else without_sampling(original))
    (out/'sampling-variant.json').write_text(json.dumps(dict(mode='periodic 100ms' if sampling else 'off',original_runner_sha256=sha(runner.with_name('guest-runner.original.sh')),executed_runner_sha256=sha(runner)),indent=2)+'\n')
    before=fingerprint(aenv,sid,'task-source-before')
    assert before['head']==cfg['base_commit'] and before['status']==' M setup.py\n M tox.ini\n'
    assert {k:before['files'][k] for k in cfg['base_file_sha256']}==cfg['base_file_sha256']
    verify_image_background(dict(files={k:before['files'][k] for k in ('setup.py','tox.ini')},patch=before['patch']))
    (out/'task-before.json').write_text(json.dumps(before,indent=2)+'\n')
    aenv('upload-frozen','upload',sid,str(out/'frozen-input'),'/workspace/mixfs-workload')
    for name,target in [('xarray6992_persistent_sampling.py','xarray6992-persistent-sampling.py'),('guest_memory_collector.py','guest_memory_collector.py'),('sphinx7590_check.py','sphinx7590_check.py')]:
        aenv('upload-'+name,'upload',sid,str(ROOT/'scripts'/name),'/workspace/'+target)
    oracle='cd /testbed && /opt/miniconda3/envs/testbed/bin/python /workspace/sphinx7590_check.py --baseline'
    baseline=json.loads(aenv('task-oracle-before','exec',sid,'bash','-lc',oracle).decode());assert baseline['status']=='PASS'
    (out/'task-oracle-before.json').write_text(json.dumps(baseline,indent=2)+'\n')
    module=controller_module(out/'frozen-input')
    def shell(*argv,capture=True):
        assert argv[0]=='aenv';argv=list(argv[1:])
        if argv[0]=='exec' and argv[-1].endswith('bash /workspace/mixfs-workload/guest-runner.sh'):argv[-1]=frozen_shell(argv[-1])
        return aenv('prefix-'+argv[0],*argv,timeout=90).decode().strip()
    module.shell=shell
    class Controller(module.Controller):
        async def physical_memory(self,label):return None
    controller=Controller(SimpleNamespace(raw_dir=out,system='agentenv',method='backbone',timeout=90))
    asyncio.run(controller.run_path(sid,'backbone',['read_repo']))
    replay=check_prefix(out,cfg,reference,sampling,pipeline_diagnostic)
    (out/'prefix-result.json').write_text(json.dumps(replay,indent=2)+'\n')
    assert before==fingerprint(aenv,sid,'task-prefix-state')
    def verify_after():
        after=fingerprint(aenv,sid,'task-source-after');assert before==after
        (out/'task-after.json').write_text(json.dumps(after,indent=2)+'\n')
        checked=json.loads(aenv('task-oracle-after','exec',sid,'bash','-lc',oracle).decode());assert checked==baseline
        (out/'task-oracle-after.json').write_text(json.dumps(checked,indent=2)+'\n')
        assert check_prefix(out,cfg,reference,sampling,pipeline_diagnostic)==replay
    return verify_after,replay
