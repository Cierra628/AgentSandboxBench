"""Thirteen original Sphinx actions, then isolated repeats of the slow command."""
import argparse
import asyncio
import csv
import copy
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

from sphinx7590_inputs import ROOT,validate,sha,check_pipeline_exit
from task_metrics_prefix import run_prefix,controller_module
from run_prettier6604_grpo import verify_path
from xarray6992_inputs import frozen_shell


def check_trace(raw,reference,cfg,samples,names,find_diagnostic=False):
    observed=copy.deepcopy(samples);differences=[]
    if find_diagnostic:
        for segment in cfg['path'][:2]:
            directory=raw/'rollouts/backbone'/segment
            with (directory/'actions.tsv').open() as f:actions=list(csv.DictReader(f,delimiter='\t'))
            with (directory/'steps.tsv').open() as f:rows=list(csv.DictReader(f,delimiter='\t'))
            assert len(actions)==len(rows)==len(cfg['expected_exit_codes'][segment])
            sample=next(s for s in observed if s['role']=='backbone' and s['segment']==segment)
            for action,row,expected in zip(actions,rows,cfg['expected_exit_codes'][segment]):
                actual=int(row['exit_code'])
                if (segment,row['step'])==('trace_cpp_parser','005'):
                    assert expected==0 and actual in (0,1)
                    assert action['sha256']=='1f6e4d89a09212e18cf773594c96e46f5aaab4bf0d55b3f27d0e17086cc800a5'
                else:
                    check_pipeline_exit(segment,row['step'],expected,actual,action['command'],
                        (directory/'stdout'/f"{row['step']}.log").read_bytes(),(directory/'stderr'/f"{row['step']}.log").read_bytes(),(directory/'patch.diff').read_bytes())
                if actual!=expected:
                    differences.append(dict(segment=segment,step=row['step'],expected=expected,actual=actual))
                    sample['actions'][int(row['step'])-1]['exit_code']=actual
    replay=verify_path(raw,'backbone',cfg['path'][:2],raw/'frozen-input',observed,names,sampling=False)
    assert replay['action_count']==13
    for segment in cfg['path'][:2]:
        actual=raw/'rollouts/backbone'/segment;expected=reference/'rollouts/backbone'/segment
        assert sha(actual/'actions.tsv')==sha(expected/'actions.tsv')==cfg['input_sha256']['segments/'+segment+'/actions.tsv']
        for kind in ('stdout','stderr'):
            for f in (expected/kind).glob('*.log'):assert sha(actual/kind/f.name)==sha(f),str(f)
        assert sha(actual/'patch.diff')==sha(expected/'patch.diff')
    if find_diagnostic:replay.update(observed_exit_differences=differences,strict_prefix_status='FAIL' if differences else 'PASS',scope='diagnostic observations only; find exit 1 remains invalid for trajectory acceptance')
    return replay


def run(args):
    cfg,_,samples,names=validate(args.inputs)
    reference=args.reference.resolve()
    assert json.loads((reference/'result.json').read_text())['checks']['actions_hash_order_expected_failures']=='PASS'
    assert json.loads((reference/'prepared-input/config.json').read_text())==cfg
    find_diagnostic=getattr(args,'find_diagnostic',False)
    if args.check_only:print('status=VALIDATED task=sphinx7590 '+('find-diagnostic' if find_diagnostic else 'command-diagnostic'));return 0
    out=args.output.resolve();assert out.parent.name=='controller' and out.parent.parent.name=='mount'
    assert out.parents[3]==ROOT/'.artifacts/storage-isolated-service'
    out.mkdir(exist_ok=False)
    env=dict(os.environ,PATH=str(ROOT/'runtime/bin')+':'+os.environ['PATH'],XDG_CONFIG_HOME=str(args.config_home))
    events=[];sid=None;success=False;stage='setup';diagnostics_ready=False
    result=dict(status='FAIL',command_diagnostic=True,checks={},cpu=2,memory_mib=4096,
        scope='13 unchanged prefix actions, 6 same-VM repeats and 4 pip-version-check controls; warm-cache diagnostic, not complete replay or monitoring overhead study')
    if find_diagnostic:result.update(find_diagnostic=True,monitor_mode='off',scope='13 original actions then 8 find captures; suppressed errors and process identities only, not full replay or monitoring overhead')
    def call(label,argv,timeout=90):
        index=len(events);begin=time.monotonic_ns();code=None
        try:
            with (out/f'{index:04}-{label}.stderr').open('xb') as log:
                p=subprocess.run(list(map(str,argv)),cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=log,timeout=timeout)
            code=p.returncode;(out/f'{index:04}-{label}.stdout').write_bytes(p.stdout)
            if code:raise RuntimeError(label+': exit '+str(code))
            return p.stdout
        finally:
            events.append(dict(stage=label,start_monotonic_ns=begin,duration_ns=time.monotonic_ns()-begin,exit_code=code))
            (out/'events.json').write_text(json.dumps(events,indent=2)+'\n')
    def cli(label,*argv,timeout=90):return call(label,[ROOT/'runtime/bin/aenv',*argv],timeout)
    def cancel(signum,frame):raise RuntimeError('owned command diagnostic interrupted')
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,cancel)
    try:
        labels=json.loads(call('service-labels',['docker','inspect','--format','{{json .Config.Labels}}',args.container]))
        assert labels.get('agentsandboxbench.run_id')==out.parents[2].name and labels.get('agentenv.experiment')=='isolated-storage-check'
        shutil.copy2(Path(__file__),out/'driver-source.py')
        shutil.copy2(ROOT/'scripts/guest_command_diagnostic.py',out/'guest-probe-source.py')
        if find_diagnostic:shutil.copy2(ROOT/'scripts/guest_find_diagnostic.py',out/'guest-find-source.py')
        sid=call('cold-start',[sys.executable,ROOT/'scripts/agentenv_cold_start.py','--config-home',args.config_home,
            '--service-url',args.service_url,'--image',cfg['source_image'],'--memory-mib','4096'],610).decode().strip()
        result['sandbox_id']=sid
        verify_after,_=run_prefix(cli,sid,out,dict(inputs=args.inputs,reference=reference),False,pipeline_diagnostic=find_diagnostic)
        cli('upload-probe','upload',sid,ROOT/'scripts/guest_command_diagnostic.py','/workspace/asb-command-diagnostic.py')
        cli('diagnostic-directory','exec',sid,'mkdir','-p','/workspace/asb-command');diagnostics_ready=True
        module=controller_module(out/'frozen-input')
        def shell(*argv,capture=True):
            argv=list(argv[1:])
            if argv[0]=='exec' and argv[-1].endswith('bash /workspace/mixfs-workload/guest-runner.sh'):
                command=frozen_shell(argv[-1])
                argv[-1]=command if find_diagnostic else 'python3 /workspace/asb-command-diagnostic.py --profile --timeout 90 --output /workspace/asb-command/segment --command '+shlex.quote(command)
            return cli('trace-'+argv[0],*argv).decode().strip()
        module.shell=shell
        class Controller(module.Controller):
            async def physical_memory(self,label):return None
        controller=Controller(SimpleNamespace(raw_dir=out,system='agentenv',method='backbone',timeout=90))
        stage='original-prefix';asyncio.run(controller.run_path(sid,'backbone',['trace_cpp_parser']))
        result['replay']=check_trace(out,reference,cfg,samples,names,find_diagnostic)
        result['checks']['thirteen_original_actions_outputs_and_patch']='PASS'
        if find_diagnostic:
            stage='find-captures'
            with (out/'frozen-input/segments/trace_cpp_parser/actions.tsv').open() as f:action=list(csv.DictReader(f,delimiter='\t'))[4]
            command_file=out/'find-command.txt';command_file.write_text(action['command'])
            assert sha(command_file)==action['sha256']
            cli('upload-find','upload',sid,ROOT/'scripts/guest_find_diagnostic.py','/workspace/asb-find-diagnostic.py')
            cli('upload-find-command','upload',sid,command_file,'/workspace/asb-find-command.txt')
            result['find_captures']=[]
            for i in range(8):
                value=json.loads(cli(f'find-capture-{i+1:02}','exec',sid,'bash','-lc',frozen_shell('python3 /workspace/asb-find-diagnostic.py /workspace/asb-find-command.txt'),timeout=40))
                (out/f'find-capture-{i+1:02}.json').write_text(json.dumps(value,indent=2)+'\n')
                assert value['status']=='CAPTURED' and value['command_sha256']==action['sha256']
                result['find_captures'].append(value)
            stage='find-transient-control'
            value=json.loads(cli('find-transient-control','exec',sid,'bash','-lc',frozen_shell('python3 /workspace/asb-find-diagnostic.py /workspace/asb-find-command.txt --transient-control'),timeout=60))
            assert value['status']=='CAPTURED' and value['command_sha256']==action['sha256'] and value['owned_children_reaped']
            (out/'find-transient-control.json').write_text(json.dumps(value,indent=2)+'\n')
            result['find_transient_control']=value
            verify_after();result['checks']['find_captures_and_source_unchanged']='PASS'
            result['strict_prefix_status']=result['replay']['strict_prefix_status']
            success=True
        else:
            stage='command-repeats'
            run_command_repeats(cli,sid,out,reference,cfg,result,verify_after)
            diagnostics_ready=False
            success=True
    except BaseException as exc:
        import traceback
        result.update(error=repr(exc),failed_stage=stage);(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,signal.SIG_IGN)
        cleanup={}
        try:
            if sid and diagnostics_ready:cli('download-failed-diagnostics','download',sid,'/workspace/asb-command',out)
        except Exception as exc:cleanup['diagnostic_preservation_error']=repr(exc);success=False
        try:
            if sid:cli('delete-owned','delete',sid)
            assert json.loads(cli('list-after','list','--output','json'))==[]
            cleanup['service_sandbox_list_empty']=True
        except Exception as exc:cleanup['error']=repr(exc);success=False
        result.update(status='PASS' if success else 'FAIL',cleanup=cleanup)
        (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print('status='+result['status']+' result_dir='+str(out))
    return 0 if success else 1


def run_command_repeats(cli,sid,out,reference,cfg,result,verify_after):
    with (out/'frozen-input/segments/trace_cpp_parser/actions.tsv').open() as f:rows=list(csv.DictReader(f,delimiter='\t'))
    row=rows[5];command=row['command'];assert row['step']=='006'
    import hashlib
    assert hashlib.sha256(command.encode()).hexdigest()==row['sha256']
    result['frozen_command_sha256']=row['sha256'];result['repetitions']=[]
    for i in range(6):
        label=f'repeat-{i+1:02}';profile=i%2==1
        value=json.loads(cli(label,'exec',sid,'bash','-lc',frozen_shell(
            'python3 /workspace/asb-command-diagnostic.py --output /workspace/asb-command/'+label+
            (' --profile' if profile else '')+' --command '+shlex.quote(command))))
        assert value['status']=='CAPTURED' and value['command_sha256']==row['sha256']
        assert value['exit_code']==cfg['expected_exit_codes']['trace_cpp_parser'][5]
        for kind in ('stdout','stderr'):assert value[kind+'_sha256']==sha(reference/'rollouts/backbone/trace_cpp_parser'/kind/'006.log')
        result['repetitions'].append(value)
    result['version_check_controls']=[]
    for i,disabled in enumerate((False,True,True,False),1):
        label=f'version-control-{i:02}'
        value=json.loads(cli(label,'exec',sid,'bash','-lc',frozen_shell(
            'python3 /workspace/asb-command-diagnostic.py --profile --output /workspace/asb-command/'+label+
            (' --disable-pip-version-check' if disabled else '')+' --command '+shlex.quote(command))))
        assert value['status']=='CAPTURED' and value['command_sha256']==row['sha256']
        assert value['exit_code']==cfg['expected_exit_codes']['trace_cpp_parser'][5]
        assert value['inherited_pip_disable_version_check'] is None,'default pip environment is already changed'
        for kind in ('stdout','stderr'):assert value[kind+'_sha256']==sha(reference/'rollouts/backbone/trace_cpp_parser'/kind/'006.log')
        result['version_check_controls'].append(value)
    metadata="python3 - <<'EOF'\nimport inspect,json,pip\nfrom pip._internal.cli import base_command\nfrom pip._internal import self_outdated_check\nprint(json.dumps(dict(version=pip.__version__,base_command_source=inspect.getsource(base_command),self_version_check_source=inspect.getsource(self_outdated_check))))\nEOF"
    value=cli('pip-local-code','exec',sid,'bash','-lc',frozen_shell(metadata))
    (out/'pip-local-code.json').write_bytes(value)
    cli('download-diagnostics','download',sid,'/workspace/asb-command',out)
    verify_after();result['checks']['repeats_outputs_exits_and_source_unchanged']='PASS'

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('inputs','reference','output','config-home'):p.add_argument('--'+name,type=Path,required=name in ('inputs','reference'))
    for name in ('container','service-url'):p.add_argument('--'+name)
    p.add_argument('--find-diagnostic',action='store_true')
    p.add_argument('--check-only',action='store_true');raise SystemExit(run(p.parse_args()))
