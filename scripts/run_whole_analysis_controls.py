"""Five matched pairs; periodic sampling stays on, only whole-cache analysis changes."""
import argparse
import csv
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
import traceback

from archive_trenvx_run_data import sha256,allocated_bytes
from experiment_disk_guard import guarded_run
from exp1_physical_workload import verify_replay
from run_exp1_monitor_balanced import estimate
from run_trenvx_combined_controls import ORDERS
from run_trenvx_fixed_identity import cpu_policies
from run_trenvx_file_origins import ROOT,SOURCES,post_scan_proof


def inspect(raw,backend,label):
    load=lambda name:json.loads((raw/name).read_text())
    cfg=load('config.json');result=load('result.json')
    assert result['status']=='PASS'
    steps=list(csv.DictReader((raw/'replay/steps.tsv').open(),delimiter='\t'))
    assert len(steps)==26
    if backend=='agentenv':
        assert cfg['whole_cache_analysis']==(label=='on') and cfg['exp1']
        assert all(v=='PASS' for k,v in result['checks'].items() if k!='whole_cache')
        assert result['checks']['whole_cache']==('PASS' if label=='on' else 'DISABLED')
        verify_replay(raw/'replay')
        assert load('official-before.json')['status']==load('official-after.json')['status']=='PASS'
        assert (raw/'source-before.stdout').read_bytes()==(raw/'source-after.stdout').read_bytes()
        events={r['phase']:r for r in map(json.loads,(raw/'events.jsonl').read_text().splitlines())}
        assert all(row['exit_code']==0 for row in events.values())
        runner=events['exp1-frozen-replay']['end_monotonic_ns']-events['exp1-frozen-replay']['start_monotonic_ns']
        end=events['official-post-scan']['end_monotonic_ns'];begin=events['exp1-frozen-replay']['start_monotonic_ns']
        key='host-vmm.tsv';host_count=len(list(csv.DictReader((raw/key).open(),delimiter='\t')))
        fixed={k:v for k,v in cfg.items() if k!='whole_cache_analysis'}
        fixed['runner_sha256']=sha256(raw/'exp1-inputs/runner.sh')
        if cfg.get('calibration_scope')=='frozen':
            launch=load('vmm-launch-config.json')
            fixed['machine_config']=launch['machine-config']
            fixed['balloon_config']=launch.get('balloon')
            fixed['guest_boot_policy']=[word for word in launch['boot-source']['boot_args'].split() if not word.startswith('ip=')]

        physical={}
        if label=='on':
            from agentenv_whole_cache import recount
            physical=recount(raw/'whole-cache')
            saved=load('whole-cache/result.json')
            assert all(saved[k]==v for k,v in physical.items())
            if cfg.get('calibration_scope')=='frozen':
                from report_file_inventory import kernel_audit,audit_frozen_fixture
                physical=kernel_audit(raw/'file-inventory',load('whole-cache/ram-attestation.json'))
                assert physical['cache_scope_status']==physical['frozen_cache_physical_status']==physical['classification_status']=='PASS'
                audit_frozen_fixture(raw/'file-inventory')
            else:assert physical['guest_unknown_pages']==dict(before=0,after=0)
        cleanup=dict(status='PASS',sdk_delete='PASS')
    else:
        assert cfg['whole_cache_diagnostic']==(label=='on') and cfg['guest_memory_sampling'] and cfg['host_sample_interval_seconds']==.1
        assert all(v=='PASS' for v in result['checks'].values())
        from audit_exp1_metrics import audit_run
        audited=audit_run(dict(backend='trenvx',mode='full-light',raw=str(raw)))
        assert post_scan_proof(raw)['status']=='PASS'
        assert load('official-task-result.json')['parser_results']['num_tests_passed']==4
        events={r['phase']:r for r in map(json.loads,(raw/'diagnostic-events.jsonl').read_text().splitlines())}
        assert all(r['status']=='PASS' for r in events.values())
        runner=audited['timing_ns']['controller_runner_call_ns']
        begin=events['tool:replay-all']['start_monotonic_ns'];end=events['tool:official-task-post-scan']['end_monotonic_ns']
        host_count=audited['host_memory']['sample_count'];cleanup=audited['cleanup']
        fixed={k:cfg[k] for k in ('seed_sha256','ch_sha256','kernel_sha256','envd_sha256','binaries','patches','source_revisions','host_kernel','cpu','memory_mib','isolation')}
        fixed['runner_sha256']=load('replay-config.json')['runner_sha256']
        physical={}
        if label=='on':
            from trenvx_whole_cache import recount
            physical=recount(raw/'whole-cache');saved=load('whole-cache/result.json')
            assert all(saved[k]==v for k,v in physical.items())
            if cfg.get('kernel_cache_calibration'):
                from report_file_inventory import kernel_audit
                physical=kernel_audit(raw/'whole-cache-files',load('whole-cache/ram-attestation.json'),backend='trenvx')
                assert physical['cache_scope_status']==physical['frozen_cache_physical_status']==physical['classification_status']=='PASS'
            else:assert physical['guest_file_cache_end_unknown_pages']==0
    if cfg.get('kernel_cache_calibration'):
        from report_file_inventory import kernel_audit
        calibration=kernel_audit(raw/'kernel-cache-calibration-files',load('kernel-cache-calibration/ram-attestation.json'),backend='trenvx')
        assert calibration['cache_scope_status']==calibration['frozen_cache_physical_status']==calibration['classification_status']=='PASS'
        assert load('kernel-cache-calibration-files/fixture-result.json')['status']=='PASS'
        fixed['common_kernel_calibration']=True
        launch=load('kernel-cache-calibration-files/vmm-config.json')['config']
        fixed['guest_boot_policy']=launch['payload']['cmdline'].split()
        fixed['vmm_balloon_config']=launch.get('balloon')
        fixed['vmm_memory_config']={k:v for k,v in launch.get('memory',{}).items() if k not in ('hotplug_size','zones')}

    guest_count=len(list(csv.DictReader((raw/'replay/guest-memory.tsv').open(),delimiter='\t')))
    assert guest_count>=2 and host_count>=2
    fixed.update(action_sha256=sha256(raw/'replay/actions.tsv'),patch_sha256=sha256(raw/'replay/patch.diff'))
    row=dict(label=label,raw_dir=str(raw),run_id=raw.name,guest_tools_ns=sum(int(r['duration_ns']) for r in steps),
        runner_calls_ns=runner,task_and_final_checks_ns=end-begin,full_run_ns=result['controller_elapsed_ns'],
        guest_samples=guest_count,host_samples=host_count,physical=physical,cleanup=cleanup,retained_bytes=allocated_bytes(raw))
    return row,fixed


def analysis(pairs):
    return {name:estimate([100*(next(r for r in pair if r['label']=='on')[name]/next(r for r in pair if r['label']=='off')[name]-1) for pair in pairs])
            for name in ('guest_tools_ns','runner_calls_ns','task_and_final_checks_ns','full_run_ns')}


def verify_recount(actual,saved,fixed,identity,budget=None):
    if budget is None:assert actual==saved
    else:
        # Allocated blocks can change after writes without changing evidence.
        # They are storage observations, not immutable experiment contents.
        assert {k:v for k,v in actual.items() if k!='retained_bytes'}=={k:v for k,v in saved.items() if k!='retained_bytes'}
        assert 0<=actual['retained_bytes']<budget
    assert fixed==identity


def audit(out):
    saved=json.loads((out/'result.json').read_text());assert saved['status']=='PASS' and len(saved['pairs'])==5
    for pair,order in zip(saved['pairs'],ORDERS):
        assert [r['label'] for r in pair]==list(order)
        for row in pair:
            actual,fixed=inspect(Path(row['raw_dir']),saved['backend'],row['label'])
            verify_recount(actual,row,fixed,saved['identity'],saved['protocol'].get('raw_case_budget_bytes'))
    assert saved['analysis']==analysis(saved['pairs'])
    assert saved['protocol']==json.loads((out/'protocol.json').read_text())
    hashes=json.loads((out/'code/sha256.json').read_text())
    assert all(sha256(out/'code'/name)==digest for name,digest in hashes.items())
    return dict(status='PASS',run_count=10,paired_comparison=saved['analysis'])


def main(backend,kernel_protocol=False):
    os.umask(0o077)
    out=ROOT/'.artifacts/whole-analysis-controls'/f'{time.strftime("%Y%m%dT%H%M%SZ",time.gmtime())}-{os.getpid()}-{backend}'
    out.mkdir(parents=True);(out/'code').mkdir()
    names=list(dict.fromkeys(list(SOURCES)+['run_whole_analysis_controls.py','smoke_agentenv_whole_cache.py','agentenv_whole_cache.py','guest_cache_marker.py','exp1_physical_workload.py','exp1_official_task.py']))
    if kernel_protocol:names=list(dict.fromkeys(names+['kernel_file_cache.py','trenvx_kernel_cache.py','cached_file_inventory.py','guest_cached_file_inventory.py','paused_agentenv_boundary.py','report_file_inventory.py']))
    hashes={name:sha256(ROOT/'scripts'/name) for name in names}
    for name in names:shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    (out/'code/sha256.json').write_text(json.dumps(hashes,indent=2)+'\n')
    policies=cpu_policies();(out/'cpu-before.json').write_text(json.dumps(policies,indent=2)+'\n')
    protocol=dict(backend=backend,pair_count=5,orders=ORDERS,
        treatment='whole-cache analysis on/off; same periodic guest/host collectors and functional checks in both',
        cache_policy='fresh private cold VM; existing host cache; no global cache drop',threshold=None,
        limitation='five pairs, order count 3/2; uncontrolled host cache/load/dynamic frequency; not cross-backend speed comparison')
    if kernel_protocol:
        protocol.update(version='exact-kernel-frozen-v1',
            treatment='final paused exact-kernel whole-analysis on/off; periodic 100ms guest/host sampling stays on',
            preparation='TrEnv-X exact-kernel pre-replay calibration in both conditions; AgentENV inputs and preparation identical',
            calibration='AgentENV paused point plus post-measurement content validation; interval cache changes separately retained',
            raw_case_budget_bytes=(256 if backend=='agentenv' else 384)*2**20)
        gate=(32 if backend=='agentenv' else 40.125)*2**30
        assert shutil.disk_usage(out).free>gate+10*protocol['raw_case_budget_bytes'],'insufficient batch retention headroom'
    (out/'protocol.json').write_text(json.dumps(protocol,indent=2)+'\n')
    result=dict(status='FAIL',backend=backend,protocol=protocol,pairs=[])
    print(f'status=RUNNING result_dir={out}',flush=True)
    try:
        for number,order in enumerate(ORDERS,1):
            pair=[];result['pairs'].append(pair)
            for label in order:
                assert cpu_policies()==policies and hashes=={name:sha256(ROOT/'scripts'/name) for name in names}
                case=out/f'pair-{number}-{label}';case.mkdir()
                gate=(32 if backend=='agentenv' else 40.125)*2**30
                assert shutil.disk_usage(out).free>gate
                if backend=='agentenv':
                    argv=[sys.executable,str(ROOT/'scripts/smoke_agentenv_whole_cache.py'),'--exp1','--analysis',label]
                    if kernel_protocol:argv+=['--paused-boundary','--file-inventory','fixture','--kernel-file-types','--calibration-scope','frozen']
                else:
                    argv=['bash',str(ROOT/'scripts/45-replay-trenvx-exp1.sh'),'--record-phases','--fixed-git-identity',
                        '--guest-collector','persistent','--guest-memory-sampling','--guest-sample-interval','.1','--host-sample-interval','.1',
                        '--file-origin-diagnostic','--guest-file-physical-diagnostic','--base-file-physical-diagnostic','--monitoring-acceptance',
                        '--reference',str(ROOT/'.artifacts/trenvx-service-smoke/20261001T134743Z-3005780'),
                        '--official-task',str(ROOT/'.artifacts/v1-closeout-inputs/official-task'),'--discard-success-images']
                    if kernel_protocol:argv+=['--kernel-cache-calibration','--paused-cache-boundary']
                    if label=='on':argv+=['--whole-cache-diagnostic']
                    if label=='on' and kernel_protocol:argv+=['--kernel-file-types']
                with (case/'driver.log').open('w') as stream:guarded_run(argv,directory=case,log=stream,timeout=600)
                dirs=re.findall(r'result_dir=(\S+)',(case/'driver.log').read_text());assert dirs and len(set(dirs))==1
                raw=Path(dirs[-1]).resolve(strict=True);assert raw.parent==ROOT/'.artifacts'/('agentenv-whole-cache' if backend=='agentenv' else 'trenvx-service-smoke')
                row,fixed=inspect(raw,backend,label)
                result.setdefault('identity',fixed);assert result['identity']==fixed,'effective common inputs changed'
                assert row['retained_bytes']<protocol.get('raw_case_budget_bytes',128*2**20),'raw result budget exceeded'
                pair.append(row);(out/'progress.json').write_text(json.dumps(result,indent=2)+'\n')
                print(f'status=PASS pair={number}/5 case={label} result_dir={out}',flush=True)
        result.update(status='PASS',analysis=analysis(result['pairs']))
    except BaseException as exc:result['error']=repr(exc);(out/'failure.txt').write_text(traceback.format_exc())
    (out/'cpu-after.json').write_text(json.dumps(cpu_policies(),indent=2)+'\n')
    (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    if result['status']=='PASS':(out/'offline-audit.json').write_text(json.dumps(audit(out),indent=2)+'\n')
    print(f'status={result["status"]} result_dir={out}',flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--backend',choices=('agentenv','trenvx'),default='agentenv')
    parser.add_argument('--kernel-protocol',action='store_true');parser.add_argument('--audit',type=Path);args=parser.parse_args()
    if args.audit:print(json.dumps(audit(args.audit)))
    else:raise SystemExit(main(args.backend,args.kernel_protocol))
