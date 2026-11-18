"""Publish saved external-task sampling-pair timings and measured curves; no live queries."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil

from run_c4_sampling_controls import ROOT,read,write,rows,summarize


def host_task_plot(run,output,title='C4'):
    import matplotlib.pyplot as plt
    task=read(Path(run['raw'])/'result.json');data=rows(Path(run['workflow'])/'host-file/memory.tsv')
    start=task['replay_start_monotonic_ns'];end=task['replay_end_monotonic_ns']
    view=[r for r in data if start-500000000<=int(r['monotonic_ns'])<=end+500000000]
    assert len(view)>1
    fig,axes=plt.subplots(2,1,figsize=(10,6),sharex=True,constrained_layout=True)
    for ax,fields in [(axes[0],('current',)),(axes[1],('file','anon','kernel'))]:
        for field in fields:ax.plot([(int(r['monotonic_ns'])-start)/1e9 for r in view],[int(r[field])/2**20 for r in view],label='Host '+('memory.current' if field=='current' else field))
        ax.axvline(0,color='#64748b',linestyle=':');ax.axvline((end-start)/1e9,color='#64748b',linestyle=':');ax.grid(alpha=.2);ax.set_ylabel('MiB');ax.legend()
    axes[0].set_title(f"{title} pair {run['pair']}: host group charges during replay (preparation + service + guest + control)")
    axes[-1].set_xlabel('Host seconds from replay CLI start; dotted lines bracket the request')
    for ext in ('png','svg'):fig.savefig(output.with_suffix('.'+ext),dpi=140)
    plt.close(fig)


def timings_plot(runs,output,title='C4',actions=36):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,3,figsize=(13,4),constrained_layout=True)
    for ax,field,label in zip(axes,('tool_sum','cold_create','controller'),(f'Guest tools: sum of {actions} calls','Cold creation: image + boot','Controller: creation through cleanup')):
        for offset,mode,color in [(-.18,'off','#94a3b8'),(.18,'on','#2563eb')]:
            values=[next(r['timings_seconds'][field] for r in runs if r['pair']==p and r['mode']==mode) for p in (1,2)]
            bars=ax.bar([p+offset for p in (1,2)],values,width=.34,color=color,label='Sampling '+mode)
            ax.bar_label(bars,fmt='%.2f',padding=3,fontsize=9)
        ax.set(title=label,ylabel='Seconds',xticks=[1,2],xticklabels=['Pair 1\nOFF then ON','Pair 2\nON then OFF']);ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True);ax.margins(y=.15)
    axes[0].set_ylim(0,max(r['timings_seconds']['tool_sum'] for r in runs)*1.35)
    axes[0].legend(loc='upper left');fig.suptitle(title+': two host + guest memory-sampling pairs (0.1 s / off)')
    for ext in ('png','svg'):fig.savefig(output/('timings.'+ext),dpi=140)
    plt.close(fig)


def report(source,output):
    state=read(source/'result.json');assert state['status']=='PASS' and not state['source_after']['running']
    comparison=summarize(source);runs=read(source/'runs.json')
    title={'reshard-c4-data':'C4','build-cython-ext':'Cython extensions','django__django-16819':'Django #16819'}[comparison['task_id']]
    output.mkdir(parents=True,exist_ok=False)
    timings_plot(runs,output,title,runs[0]['actions'])
    public_runs=[]
    for run in runs:
        prefix=f"pair-{run['pair']}-{run['mode']}";raw=Path(run['raw'])
        shutil.copy2(raw/'tools.tsv',output/(prefix+'-tools.tsv'))
        if run['mode']=='on':
            work=read(Path(run['workflow'])/'result.json');folder=Path(work['report'])
            for ext in ('png','svg'):
                shutil.copy2(folder/('guest.'+ext),output/(prefix+'-guest.'+ext))
                shutil.copy2(folder/'host'/('overview.'+ext),output/(prefix+'-host.'+ext))
            host_task_plot(run,output/(prefix+'-host-task'),title)
        public_runs.append({k:run[k] for k in ('index','pair','mode','actions','timings_seconds','sampling_cost','memory','minimum_available_gib','cleanup')})
        if 'call_timing' in run:public_runs[-1]['call_timing']=run['call_timing']
        if comparison['task_id']=='build-cython-ext':
            public_runs[-1].update(source_identity=run['source_identity'],network_preflight_seconds=run['network_preflight_seconds'])
    first=Path(runs[0]['workflow']);date=datetime.strptime(first.name.split('T',1)[0],'%Y%m%d').date().isoformat()
    value=dict(**comparison,date=date,source_revision=read(first/'config.json')['revision'],run_details=public_runs,
        raw_result_directory=str(source.relative_to(ROOT)),public_result_directory=str(output.relative_to(ROOT)),
        scope_notes=['Both host and guest memory sampling change together; clocks, original validation, cgroup accounting and disk guards remain.',
        'Four independent fresh services and guests; original task and validation retained, default reclaim, no cache reset, extra pre-read, freeze, PSS or mincore.',
        'Host memory is whole measurement-group accounting, including preparation, service and control; not task-only memory or sharing savings.',
        'Off runs have no measured memory or sampler CPU, represented by NOT_MEASURED / null, not zero.',
        'Two pairs give the observed changes and range, not a population confidence bound; cold creation includes image retrieval and conversion.'])
    if comparison['task_id']=='build-cython-ext':value['scope_notes'].append('Cython retains original network/install/build operations. Only pairs matching recorded source/version fields enter summary; drift gives null changes, not replacement runs. Unrecorded dependencies and history-repository changes remain uncertain.')
    write(output/'summary.json',value);write(ROOT/'docs/records/evidence'/(output.name+'.json'),value)
    for p in output.iterdir():p.chmod(0o644)
    return value


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('source',type=Path);parser.add_argument('output',type=Path);args=parser.parse_args()
    report(args.source.resolve(),args.output.resolve());print('status=PASS result_dir='+str(args.output))
