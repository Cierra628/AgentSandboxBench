"""Offline assessment only: saved runner text, argument guards and result consumers."""
import argparse
import ast
from contextlib import ExitStack
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import host_persistent_input as persistent
import platform_tasks as platform
import prettier_no_proxy as local
import run_prettier6604_grpo as grpo
import validate_isolated_agentenv as native
from sandbox_runtime_monitor import boundary_runner
from run_grpo_local_no_proxy_controls import comparisons

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'.artifacts/prettier-grpo-local-no-proxy-controls-20261101/live-3/result.json'
CONFIG=ROOT/'configs/platform-prettier6604-grpo-host-current.json'


def read(path):return json.loads(path.read_text())
def tree(name):return ast.parse((ROOT/'scripts'/name).read_text())
def execute(node,values):exec(compile(ast.Module(body=[node],type_ignores=[]),'<actual guard>','exec'),values)


def parser():
    entry=next(n for n in tree('validate_isolated_agentenv.py').body if isinstance(n,ast.If) and ast.unparse(n.test)=="__name__ == '__main__'")
    nodes=[]
    for node in entry.body:
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='args' for t in node.targets):break
        nodes.append(node)
    values=dict(native.__dict__)
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'<actual argument parser>','exec'),values)
    return values['parser']


def assess(out):
    source=read(SOURCE);assert source['status']=='PASS' and len(source['runs'])==4
    baseline_text=CONFIG.read_text();base=platform.configuration(CONFIG)
    candidate=dict(base,light_skip_boundary_vmstat=True)
    checks=[]
    def passed(name,**evidence):checks.append(dict(name=name,status='PASS',**evidence))
    def reject(name,action):
        try:action()
        except (ValueError,AssertionError,StopIteration) as error:
            passed(name,currently_rejected=True,error_type=type(error).__name__,message=str(error));return
        raise AssertionError('Expected current rejection: '+name)
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ,{'ASB_LOCAL_NO_PROXY':'on'},clear=True))
        processes=[]
        for name in ('Popen','run','check_output','check_call'):
            processes.append(stack.enter_context(patch('subprocess.'+name,side_effect=AssertionError('No external process in assessment'))))
        local.require_mode(base,True);persistent.single_config(base,local_no_proxy=True)
        passed('current_local_sampled_config_accepted')
        raw_candidate=dict(json.loads(baseline_text),light_skip_boundary_vmstat=True)
        (out/'candidate-config-preview.json').write_text(json.dumps(raw_candidate,indent=2)+'\n')
        assert platform.configuration(out/'candidate-config-preview.json')==candidate
        passed('platform_configuration_accepts_candidate')
        assert persistent.local_grpo_config(candidate)
        passed('persistent_config_predicate_accepts_candidate')
        persistent.single_config(candidate,local_no_proxy=True);passed('persistent_input_guard_accepts_candidate')
        local.require_mode(candidate,True);passed('local_no_proxy_guard_accepts_candidate')
        # Only argument construction is exercised; no preparation or native main.
        check0,live0=platform.commands(base,Path('/not-created-seed'))
        check1,live1=platform.commands(candidate,Path('/not-created-seed'))
        assert check1==check0[:-1]+['--light-skip-boundary-vmstat']+check0[-1:]
        assert live1==live0+['--light-skip-boundary-vmstat']
        passed('existing_argument_builder_already_forwards_one_flag',scope='Pure commands builder, persistent_input=False; not acceptance of persistent runtime')
        _,actual=platform.commands(base,Path('/not-created-seed'),persistent_input=True)
        actual_args=parser().parse_args(actual[2:]);assert persistent.native_option(actual_args)==Path('/not-created-seed')
        _,proposed_command=platform.commands(candidate,Path('/not-created-seed'),persistent_input=True)
        proposed=parser().parse_args(proposed_command[2:])
        assert {k for k in vars(proposed) if vars(proposed)[k]!=vars(actual_args)[k]}=={'light_skip_boundary_vmstat'}
        assert native.ordinary_current_only(proposed);passed('native_current_predicate_accepts_candidate')
        assert persistent.native_option(proposed)==Path('/not-created-seed');passed('native_persistent_guard_accepts_candidate')
        assert grpo.skip_boundary_vmstat_options(proposed);passed('native_skip_option_guard_accepts_candidate')
        replay=SimpleNamespace(schedule='grpo',monitor_mode='continuous',light_monitor=True,light_skip_boundary_vmstat=True)
        assert grpo.skip_boundary_vmstat_options(replay);passed('replay_skip_option_guard_accepts_candidate')
        assert grpo.skip_boundary_vmstat_options(SimpleNamespace(**(vars(replay)|dict(grpo_create_before_setup=True))))
        passed('legacy_create_all_skip_still_accepted')
        cfg0=Path(base['frozen_export'])/'workload/guest-runner.sh';original=cfg0.read_text()
        keep=boundary_runner(original,guest_phases=True);skip=boundary_runner(original,guest_phases=True,skip_vmstat=True)
        begin=keep.index('sample_vmstat() {\n');end=keep.index('\n}\n',begin)+len('\n}')
        assert skip==keep[:begin]+'sample_vmstat() { :; }'+keep[end:]
        assert '/proc/vmstat' in keep and '/proc/vmstat' not in skip
        assert keep.count('sample_vmstat ')==skip.count('sample_vmstat ')
        (out/'keep-runner-preview.sh').write_text(keep);(out/'skip-runner-preview.sh').write_text(skip)
        sampled=[r for r in source['runs'] if r['mode']=='on'];assert len(sampled)==2
        for run in sampled:
            assert (Path(run['raw'])/'frozen-input/guest-runner.sh').read_text()==keep
        passed('only_vmstat_function_body_changes',saved_normal_runs_matched=2,other_script_text_unchanged=True)
        paths=read(ROOT/'results/prettier-grpo-local-no-proxy-breakdown-20261101/summary.json')
        for pair in paths['pairs']:
            for run in pair['runs']:
                expected=448 if run['mode']=='on' else 0
                assert run['boundary_memory_rows']==run['boundary_vmstat_rows']==expected
                critical=next(p for p in run['paths'] if p['role']=='late_1')
                assert critical['boundary_memory_rows']==critical['boundary_vmstat_rows']==(76 if run['mode']=='on' else 0)
        passed('counts_reuse_saved_four_runs',normal_memory_rows=448,normal_vmstat_rows=448,critical_each_rows=76,
            expected_candidate_memory_rows=448,expected_candidate_vmstat_data_rows=0,
            avoided_vmstat_reads_per_run=448,avoided_date_awk_invocations_per_run=896)
        task=read(Path(sampled[0]['raw'])/'result.json')
        guard=next(n for n in ast.walk(tree('report_prettier6604_grpo.py')) if isinstance(n,ast.If) and ast.unparse(n.test)=='skip_boundary_vmstat')
        execute(guard,dict(skip_boundary_vmstat=True,light=True,off=False,schedule='grpo',result=task,
            local_current_grpo_record=grpo.local_current_grpo_record))
        passed('task_report_scope_guard_accepts_saved_local_context',scope='Scope assertion only; not a candidate task audit')
        metadata=dict(status='NOT_MEASURED',reason='explicitly skipped boundary vmstat',row_count=0,boundary_memory_rows=448)
        simulated=dict(task,boundary_vmstat=metadata)
        guard=next(n for n in ast.walk(tree('run_grpo_light_pair.py')) if isinstance(n,ast.If) and ast.unparse(n.test)=='skip_boundary_vmstat')
        execute(guard,dict(skip_boundary_vmstat=True,mode='on',setup_schedule='per-guest-setup',task=simulated,
            summary={'boundary_vmstat':metadata},variant={}))
        passed('shared_pair_inspector_scope_guard_accepts_saved_local_context',scope='Scope and metadata assertion only; not candidate measurements')
        boundary_guard=next(n for n in ast.walk(tree('run_grpo_local_no_proxy_controls.py')) if isinstance(n,ast.Assert) and
            'boundary ==' in ast.unparse(n.test) and '448' in ast.unparse(n.test))
        execute(boundary_guard,dict(boundary={'guest-memory.tsv':448,'guest-vmstat.tsv':0},skip_vmstat=True))
        passed('current_driver_accepts_explicit_448_memory_zero_vmstat_rows')
        fake=[dict(pair=p,mode='on') for p in (1,1,2,2)]
        reject('current_comparison_does_not_accept_two_sampled_variants',lambda:comparisons(fake))
        guard=next(n for n in ast.walk(tree('analyze_grpo_path_costs.py')) if isinstance(n,ast.Assert) and
            ast.unparse(n.test)=='len(mem) == len(vmstat) == expected')
        reject('existing_deep_path_analysis_rejects_missing_vmstat',lambda:execute(guard,dict(mem=[1]*4,vmstat=[],expected=4)))
        forwarding=[n for n in ast.walk(tree('validate_isolated_agentenv.py')) if isinstance(n,ast.If) and
            ast.unparse(n.test)=='skip_boundary_vmstat' and len(n.body)==1 and isinstance(n.body[0],ast.AugAssign) and
            '--light-skip-boundary-vmstat' in ast.unparse(n.body[0])]
        assert {ast.unparse(n.body[0].target) for n in forwarding}=={'check_args','task_args'} and len(forwarding)==2
        passed('native_check_and_live_forwarding_already_present',native_forwarding_sites=2)
        assert all(not p.called for p in processes)
        passed('no_external_process_invoked')
    assert CONFIG.read_text()==baseline_text
    return dict(status='PASS_OFFLINE_ASSESSMENT',candidate_runtime_status='PARAMETERS_SUPPORTED_NOT_LIVE_VALIDATED',runtime_changes=False,runtime_settings_changed=False,
        new_live_guests=0,new_live_actions=0,checks=checks,check_count=len(checks),source=str(SOURCE.relative_to(ROOT)),
        preferred_comparison='Both sides sampled; retain versus skip boundary vmstat, keep/skip then skip/keep; four fresh runs, 756 actions',
        expected_counts=dict(memory_rows=448,vmstat_rows=0,vmstat='NOT_MEASURED',periodic_interval_seconds=.1),
        performance_effect='NOT_MEASURED_FOR_THIS_CANDIDATE')


def main():
    parser0=argparse.ArgumentParser(description=__doc__);parser0.add_argument('--output',type=Path,required=True)
    args=parser0.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    result=assess(args.output);(args.output/'assessment.json').write_text(json.dumps(result,indent=2)+'\n')
    print('status='+result['status']+' checks='+str(result['check_count'])+' result_dir='+str(args.output))


if __name__=='__main__':main()
