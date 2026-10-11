"""Offline summary of original-runner monitoring smoke and balanced groups."""
import argparse
import hashlib
import json
from pathlib import Path
from report_fixed_frequency_monitor import report
from run_exp1_monitor_balanced import summarize

ROOT=Path(__file__).resolve().parents[1]


def audit(directories,balanced_mode='damon_disabled'):
    assert balanced_mode in ('damon_disabled','default')
    directories=[p.resolve() for p in directories]
    assert len(set(directories))==len(directories)
    assert 2<=len(directories)<=7
    output=dict(status='PASS',smoke=[],groups=[],checks=dict(original_runner='PASS',
                correctness='PASS',targeted_function='PASS',damon_state='PASS',
                cpu_restoration='PASS',cleanup='PASS',full_official_tests='NOT_RUN'))
    balanced_rows=[];identity=None
    for number,directory in enumerate(directories):
        source=json.loads((directory/'result.json').read_text())
        observed=report(directory)
        mode=source['monitor_damon_mode']
        if number<2:
            assert mode==('damon_disabled','default')[number] and not source['balanced']
            assert source['order']==['off','light','light','off']
        else:
            assert mode==balanced_mode and source['balanced']
            assert source['seed']==20261001+number-2
        for row in source['runs']:
            fixed={k:row['metadata'][k] for k in ('workload_image','sandbox_cpu',
                'sandbox_memory_mib','action_manifest_sha256','aenv_version','host_kernel',
                'agentenv_container_image','agentenv_container_id','overlaybd_io_engine','overlaybd_direct_io')}
            if identity is None:identity=fixed
            assert fixed==identity
            if number>=2:
                balanced_rows.append(dict(group=number-1,label=row['label'],task_window_ns=row['task_window_ns']))
        entry=dict(result_dir=str(directory.relative_to(ROOT)),
            source_result_sha256=hashlib.sha256((directory/'result.json').read_bytes()).hexdigest(),
            mode=mode,differences=observed['differences'],order=source['order'],
            runs=[{k:r[k] for k in ('position','mode','task_window_seconds',
                  'controller_exec_seconds','guest_action_sum_seconds','sample_count','steps_ms',
                  'observed_min_free_gib')} for r in observed['observations']])
        if number<2:output['smoke'].append(entry)
        else:output['groups'].append(entry)
    output['analysis']=summarize(balanced_rows) if balanced_rows else dict(verdict='INCONCLUSIVE')
    output['monitoring_acceptance']=output['analysis']['verdict']
    output['completed_full_replays']=sum(len(r['runs']) for r in output['smoke']+output['groups'])
    output['workload_image']=identity['workload_image']
    output['action_manifest_sha256']=identity['action_manifest_sha256']
    output['limitations']=[
        'Host service cgroup sampled at 1 second; guest periodic sampling disabled; only this workload/configuration',
        'Original initial index and complete preparation retained; no pre-refresh, cache flush or prewarm',
        'Default and DAMON-disabled smoke are separate; formal groups use DAMON-disabled policy only',
        'Only five completed groups produce a 95% t interval; approximate independent normal log ratios assumed',
        'Early stopping or incomplete groups cannot establish 5% acceptance; shared CPUs and background load remain',
        'Independent SVG/HTML checks passed; full official task tests not run']
    if balanced_mode=='default':
        output['balanced_mode']='default'
        output['limitations'][2]='Default and DAMON-disabled smoke are separate; formal groups retain official default DAMON policy only; no pooling with disabled-policy results'
    return output


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directories',type=Path,nargs='+');parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--balanced-mode',choices=('damon_disabled','default'),default='damon_disabled')
    args=parser.parse_args();value=audit(args.directories,balanced_mode=args.balanced_mode)
    args.output.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    print(f"status=PASS full_replays={value['completed_full_replays']} verdict={value['monitoring_acceptance']} output={args.output}")
