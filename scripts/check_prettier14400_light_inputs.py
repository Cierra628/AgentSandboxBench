"""Offline input check for adding the original Exp1 task to light host sampling."""
import argparse
import ast
import json
from pathlib import Path
import re

from use_platform import ROOT,configuration
from exp1_official_task import HEAD
from exp1_physical_workload import verify_replay


def check(output):
    output=Path(output).resolve()
    if output==ROOT/'.artifacts' or not output.is_relative_to(ROOT/'.artifacts'):
        raise ValueError('output must be a new subdirectory of project .artifacts')
    output.mkdir(parents=True,exist_ok=False)
    original=ROOT/'configs/platform-exp1-isolated.json'
    raw=json.loads(original.read_text())
    raw.update(backends=['agentenv'],checkpoint_after_step=None,
               output_root='.artifacts/host-current-prettier14400')
    derived=output/'legacy-full-replay-inputs.json'
    derived.write_text(json.dumps(raw,indent=2)+'\n')
    # This existing checker only reads frozen inputs. Do not call preflight/run.
    cfg=configuration(derived)
    task=json.loads((Path(cfg['official_task'])/'task.json').read_text())
    assert task['instance_id']==cfg['task_id']=='prettier__prettier-14400'
    assert task['base_commit']==HEAD
    image_config=ROOT/'IncrementalDAX_moti/motivation/experiments/exp1-single-app-smoke/systems/agentenv/config.env'
    image=re.search(r"WORKLOAD_IMAGE=\$\{WORKLOAD_IMAGE:-'([^']+)'\}",image_config.read_text()).group(1)
    assert re.fullmatch(r'ghcr\.io/timesler/swe-polybench\.eval\.x86_64\.prettier__prettier-14400@sha256:[0-9a-f]{64}',image)
    assert image==json.loads((ROOT/'configs/exp1-monitor-calibration-smoke.json').read_text())['workload_image']
    reference=ROOT/'.artifacts/agentenv-whole-cache/20261007T033805Z-3397540'
    assert json.loads((reference/'result.json').read_text())['status']=='PASS'
    replay=verify_replay(reference/'replay')
    assert (reference/'replay/original-head.txt').read_text().strip()==HEAD
    assert replay['expected_failures']==cfg['trajectory']['expected_failures']
    assert replay['patch_sha256']==cfg['trajectory']['expected_patch_sha256']
    key_outputs=['    document.addEventListener("DOMContentLoaded", () => {',
                 '      const node = document.getElementById("lastStroke");']
    assert all(line in (reference/'replay/stdout/024.log').read_text() for line in key_outputs)
    official=json.loads((reference/'official-before.json').read_text())
    assert official['status']=='PASS' and official['exit_code']==0
    assert official['manifest']==json.loads((Path(cfg['official_task'])/'manifest.json').read_text())
    assert official['parser_results']['num_tests_passed']==4 and official['parser_results']['num_tests_failed']==0
    properties=json.loads((reference/'exp1-independent-after.stdout').read_text())
    assert set(properties)=={'svg','nested_svg','html_control'}
    assert all(row['javascript_formatted'] and row['idempotent'] for row in properties.values())
    value=dict(status='INPUTS_VALIDATED_ADAPTER_REQUIRED',task_id=cfg['task_id'],base_commit=HEAD,
        image=image,trajectory=raw['trajectory_dir'],official_task=raw['official_task'],
        cpu=cfg['sandbox_cpu'],memory_mib=cfg['sandbox_memory_mib'],cwd='/testbed',
        checkpoint_after_step=None,action_count=replay['action_count'],
        session_semantics=cfg['trajectory']['session_semantics'],action_timeout_seconds=60,
        expected_failures=replay['expected_failures'],expected_patch_sha256=replay['patch_sha256'],
        manifest_sha256=cfg['trajectory']['manifest_sha256'],
        key_outputs_024=key_outputs,
        official=dict(command=task['test_command'],F2P=ast.literal_eval(task['F2P']),P2P=ast.literal_eval(task['P2P']),
                      expected_passed_assertions=4,expected_failed_assertions=0),
        functional_reference=str(reference.relative_to(ROOT)),
        proposed_measurement=dict(guest_interval_seconds=.1,host_interval_seconds=.1,
            host_primary='memory.current',host_components=['anon','file','kernel'],pss=False,mincore=False,
            reclaim='AgentENV defaults; no project cache drop, DAMON changes, pre-read or page locking'),
        remaining_implementation=['Explicit light option in isolated Exp1 controller and launcher; preserve old behavior',
            'Disable old sampler when RuntimeMonitor is selected; retain all task correctness checks',
            'Monotonic tool timing without changing action files; adapt report from the Exp1 steps layout',
            'Fresh cgroup before preparation, private service creation evidence, current baseline, costs and final writable-file audit'],
        execution_status='NOT_RUN',new_sandboxes=0,new_model_requests=0,
        warning='legacy-full-replay-inputs.json validates the old full-replay contract; it does not enable light/current sampling. Existing new-task v2 and frozen-CSV entries do not support this task.')
    (output/'summary.json').write_text(json.dumps(value,indent=2)+'\n')
    return value


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=check(args.output)
    print('status='+result['status']+' result_dir='+str(args.output))
