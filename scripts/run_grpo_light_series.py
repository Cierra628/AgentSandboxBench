"""Ten additional GRPO sampling pairs, alternating order; reuse the proven pair runner."""
import argparse
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import traceback

from isolated_storage import available,capacity_budget,GIB
from run_grpo_light_pair import ROOT,SOURCE,SOURCE_ID,read,write
from report_grpo_light_repeats import summarize

ORDERS=['off-on' if i%2==0 else 'on-off' for i in range(10)]


def distribution(values):
    return dict(mean=statistics.mean(values),median=statistics.median(values),
        sample_stddev=statistics.stdev(values),minimum=min(values),maximum=max(values))


def report(out):
    plan=read(out/'plan.json');assert plan['orders']==ORDERS
    pairs=[out/f'pair-{i:02}' for i in range(1,11)]
    result=summarize(pairs,expected_pairs=10)
    result.pop('new_live_actions')
    result['additional_actions_vs_old_three']=3780
    result['prior_three']='docs/records/evidence/grpo-light-repeats-20261026.json'
    for key,value in result['statistics'].items():
        rows=[p['comparison'][key] for p in result['pairs']]
        value['off_distribution_ns']=distribution([r['off_ns'] for r in rows])
        value['on_distribution_ns']=distribution([r['on_ns'] for r in rows])
        value['paired_difference_distribution_ns']=distribution([r['difference_ns'] for r in rows])
        changes=[r['change_percent'] for r in rows]
        value['paired_change_distribution_percent']=distribution(changes) if all(v is not None for v in changes) else None
    result['definition']+='; sample standard deviation uses n-1; ten new pairs only, no outlier removal or pooling with old three'
    write(out/'summary.json',result)
    return result



def plot(summary,out):
    import csv
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    if summary['pair_count']!=10:raise ValueError('ten complete pairs required')
    fields=[('controller','Controller total (s)'),('frozen_tools_and_downloads_span','Actions + downloads (s)'),('cold_request_span','Cold request batch (s)')]
    fig,axes=plt.subplots(3,1,figsize=(11,9),sharex=True,constrained_layout=True)
    x=list(range(1,11))
    for ax,(key,label) in zip(axes,fields):
        for mode,color,marker in [('off','#2563a6','o'),('on','#d76b1c','s')]:
            values=[p['comparison'][key][mode+'_ns']/1e9 for p in summary['pairs']]
            ax.plot(x,values,color=color,marker=marker,label='Sampling '+mode,linewidth=1.5)
        ax.set_ylabel(label);ax.set_ylim(0,1.1*max(p['comparison'][key][m+'_ns']/1e9 for p in summary['pairs'] for m in ('off','on')));ax.grid(alpha=.25)
    axes[0].legend(loc='upper left')
    axes[0].set_title('Prettier GRPO: 10 additional paired runs, 7 guests per run')
    axes[-1].set_xlabel('Pair number (off/on order alternates)');axes[-1].set_xticks(x)
    for suffix in ('png','svg'):fig.savefig(out/('grpo-ten-pairs.'+suffix),dpi=160)
    plt.close(fig)
    with (out/'timings.tsv').open('w') as stream:
        writer=csv.writer(stream,delimiter='\t');writer.writerow(['pair','order','mode','tool_sum_s','actions_downloads_s','cold_batch_s','controller_s'])
        for pair in summary['pairs']:
            for run in pair['runs']:
                t=run['timings_ns'];writer.writerow([pair['pair'],'-'.join(pair['order']),run['mode'],*[t[k]/1e9 for k in ['tool_sum','frozen_tools_and_downloads_span','cold_request_span','controller']]])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--report-only',action='store_true')
    parser.add_argument('--plot-output',type=Path,help='New plot directory; only with --report-only')
    args=parser.parse_args();out=args.output.resolve()
    if args.plot_output and not args.report_only:parser.error('--plot-output requires --report-only')
    if args.report_only:
        result=report(out)
        if args.plot_output:plot(result,args.plot_output)
        print('status='+result['status']+' result_dir='+str(out));return
    assert out.is_relative_to(ROOT/'.artifacts') and not out.exists()
    os.umask(0o077);out.mkdir(parents=True);(out/'code').mkdir()
    for name in ('run_grpo_light_series.py','run_grpo_light_pair.py','report_grpo_light_repeats.py',
        'run_prettier6604_grpo.py','sandbox_runtime_monitor.py','report_prettier6604_grpo.py','report_task_overview.py'):
        shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    source=json.loads(subprocess.check_output(['docker','inspect',SOURCE],timeout=20))[0]
    assert source['Id']==SOURCE_ID and not source['State']['Running'] and source['HostConfig']['RestartPolicy']['Name']=='no'
    write(out/'plan.json',dict(orders=ORDERS,planned_pairs=10,planned_runs=20,planned_guests=140,planned_actions=3780,
        scope='Additional guest-only GRPO pairs, .1 s vs off, seven fresh 2 CPU/4096 MiB guests per run, 16 GiB volume, default reclaim, no host/PSS/metrics/cache control',
        old_three='Kept separately; no rerun or replacement',load_average=os.getloadavg(),available_bytes=available(ROOT)))
    state=dict(status='RUNNING',completed_pairs=0,stage='precheck');write(out/'result.json',state)
    print('status=RUNNING result_dir='+str(out),flush=True)
    try:
        for number,order in enumerate(ORDERS,1):
            assert capacity_budget(available(ROOT),16*GIB)['ready']
            pair=out/f'pair-{number:02}';state['stage']=pair.name;write(out/'result.json',state)
            command=['timeout','-k','120s','3600s','bash',str(ROOT/'scripts/run-grpo-light-pair.sh'),
                '--order',order,'--output',str(pair)]
            write(out/(pair.name+'-command.json'),command)
            with (out/(pair.name+'.log')).open('x') as log:
                process=subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
            write(out/(pair.name+'-exit.json'),dict(exit_code=process.returncode,load_average=os.getloadavg(),available_bytes=available(ROOT)))
            assert process.returncode==0,'stop after first failure; do not rerun successful groups'
            receipt=read(pair/'result.json');assert receipt['status']=='PASS' and receipt['source_returned_to_stopped'] and receipt['completed']==2
            state['completed_pairs']=number;write(out/'result.json',state)
            comparison=read(pair/'comparison.json')['comparison']['controller']
            print(f'status=PASS pair={number}/10 order={order} controller_off_s={comparison["off_ns"]/1e9:.6f} controller_on_s={comparison["on_ns"]/1e9:.6f}',flush=True)
        result=report(out)
        state.update(status='PASS',stage='complete',actual_actions=result['actual_actions'],sample_count=result['sample_count'])
    except BaseException as error:
        state.update(status='FAIL',error=repr(error));(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        # Pair runner normally returns the fixed source to stopped, including failures.
        after=json.loads(subprocess.check_output(['docker','inspect',SOURCE],timeout=20))[0]
        assert after['Id']==SOURCE_ID
        if after['State']['Running']:
            subprocess.run(['docker','stop','-t','30',SOURCE],capture_output=True,check=True,timeout=60)
            after=json.loads(subprocess.check_output(['docker','inspect',SOURCE],timeout=20))[0]
        state['source_returned_to_stopped']=not after['State']['Running']
        if after['State']['Running']:state['status']='FAIL'
        write(out/'result.json',state)
    print('status='+state['status']+' result_dir='+str(out),flush=True)
    if state['status']!='PASS':raise SystemExit(1)


if __name__=='__main__':main()
