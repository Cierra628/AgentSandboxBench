"""Read saved cold GRPO results: two new pairs plus the validated first pair."""
import argparse
import json
import os
from pathlib import Path
import statistics

from host_full_rootfs_control import validate_observation

ROOT=Path(__file__).resolve().parents[1]
STATUS='PASS_COMPLETE_GRPO_COLD_READ_PAIR'


def read(path):return json.loads(Path(path).read_text())
def relative(path):return str(Path(path).resolve().relative_to(ROOT))


def audit_pair(directory):
    directory=Path(directory).resolve();pair=read(directory/'result.json')
    assert pair['status']=='PASS' and pair['actual_actions']==378 and pair['source_returned_to_stopped']
    assert len(pair['groups'])==2
    order=[g['mode'] for g in pair['groups']]
    assert order==(['private','shared'] if pair['order']=='private-first' else ['shared','private'])
    groups=[]
    for saved in pair['groups']:
        group=Path(saved['result_dir']);v=read(group/'result.json');storage=Path(v['storage']);raw=storage/'prettier6604'
        outer=read(storage/'result.json');task=read(raw/'result.json');c=read(raw/'host-full-rootfs.json')
        cold=read(raw/'host-full-cold-read.json');monitor=read(raw/'runtime-monitor.json')
        report=read(storage/'report/runtime-metrics.json')
        assert v['status']==outer['status']==task['status']=='PASS' and v['seed_released']
        assert task['actual_action_count']==189 and task['segment_count']==35 and len(task['roles'])==7
        assert report['schedule_audit']['status']=='PASS' and report['schedule_audit']['actions']==189
        assert all(p['official_passed']==0 and p['official_failed']==2 for p in task['paths'].values())
        validate_observation(c);assert c['cold_read_requested'] and c['cold_read']==cold
        assert cold['worker_reaped'] and cold['stop']['reaped'] and cold['interval_seconds']==5
        mode=v['mode'];count=19 if mode=='shared' else 133;users=7 if mode=='shared' else 1
        assert len(c['roots']['objects'])==count and all(len(o['users'])==users for o in c['roots']['objects'])
        wanted={(o['identity']['device'],o['identity']['inode'],o['identity']['size'],o['identity']['mtime_ns']) for o in c['P0']['objects']}
        assert len(wanted)==count
        points=[]
        for p in cold['points']:
            rows=p['objects'];assert len(rows)==count
            assert wanted=={(o['identity']['device'],o['identity']['inode'],o['identity']['size'],o['identity']['mtime_ns']) for o in rows}
            assert all(o['residency']['stable'] and len(o['users'])==users for o in rows)
            assert p['resident_bytes']==sum(o['residency']['resident_bytes'] for o in rows)
            assert p['elapsed_ns']==p['end_monotonic_ns']-p['start_monotonic_ns']
            points.append({k:p[k] for k in ['label','phase','start_monotonic_ns','end_monotonic_ns','elapsed_ns','cpu_ns','resident_bytes']})
        assert points[0]['resident_bytes']==0
        for a,b in zip(points,points[1:]):
            assert a['end_monotonic_ns']<=b['start_monotonic_ns']
            if b['label']=='periodic':assert b['start_monotonic_ns']-a['end_monotonic_ns']>=5_000_000_000
        assert cold['query_total_ns']==sum(p['elapsed_ns'] for p in points)
        assert cold['query_cpu_ns']==sum(p['cpu_ns'] for p in points)
        events=read(raw/'host-full-cold-events.json')['events'];assert events==cold['events']
        assert sum(e['label']=='segment-begin' for e in events)==35
        assert sum(e['label']=='segment-end' for e in events)==35
        boundaries={e['label']:e['monotonic_ns'] for e in events if e['label'] in ['guest-creation-begin','guests-and-setup-ready','paths-finished']}
        assert boundaries['guest-creation-begin']==task['rollout_start_monotonic_ns']
        assert points[0]['end_monotonic_ns']<boundaries['guest-creation-begin']<=boundaries['guests-and-setup-ready']<=boundaries['paths-finished']
        assert boundaries['paths-finished']==c['paths_end_monotonic_ns']<=cold['stop']['start_monotonic_ns']
        assert cold['worker_timing']['end_monotonic_ns']<=cold['stop']['end_monotonic_ns']<=c['P1_metadata']['start_monotonic_ns']
        assert c['P1_metadata']['end_monotonic_ns']<=points[-1]['start_monotonic_ns']<task['rollout_end_monotonic_ns']
        assert monitor['profile']=='light' and not monitor['host_vmm_requested'] and len(monitor['sessions'])==7
        assert all(s['status']=='PASS' and s['collector_reaped'] and s['receipt']['interval_seconds']==.1 for s in monitor['sessions'])
        assert task['cleanup']['cold_reader_reaped'] and task['cleanup']['service_sandbox_list_empty']
        assert all(outer['cleanup'][k] for k in ['owned_container_removed','mount_and_loop_removed','successful_fixture_removed',
            'original_source_firecracker_identities_unchanged','original_service_unchanged'])
        assert outer['checks']['actual_writable_files_inside_volume']=='PASS'
        code={name:(raw/'code'/name).read_bytes()==(ROOT/'scripts'/name).read_bytes() for name in
            ['host_grpo_cold_read.py','host_full_rootfs_control.py','run_prettier6604_grpo.py']}
        assert all(code.values())
        assert read(group/'config.json')==read(ROOT/'configs/platform-prettier6604-host-file.json')
        during=[p for p in points if p['phase']=='original-paths-and-downloads'];assert during
        groups.append(dict(mode=mode,group=relative(group),storage=relative(storage),report=relative(storage/'report'),
            actual_actions=189,objects=count,users_per_object=users,points=points,boundaries=boundaries,
            endpoint_resident_bytes=c['P1']['resident_bytes'],
            first_task_observation_bytes=during[0]['resident_bytes'],last_task_observation_bytes=during[-1]['resident_bytes'],
            query_total_ns=cold['query_total_ns'],query_cpu_ns=cold['query_cpu_ns'],
            query_median_ns=statistics.median(p['elapsed_ns'] for p in points),query_max_ns=max(p['elapsed_ns'] for p in points),
            worker_timing=cold['worker_timing'],stop=cold['stop'],write_cost=cold['write_cost'],
            initial_eviction=cold['eviction'],preparation_sync=outer['host_shared_preparation_sync'],
            guest_samples=sum(s['receipt']['samples'] for s in monitor['sessions']),
            executed_source_matches_current=code,configuration_matches=True,
            timings_ns=dict(controller=task['controller_elapsed_ns'],rollout=task['rollout_end_monotonic_ns']-task['rollout_start_monotonic_ns'],
                setup=boundaries['guests-and-setup-ready']-boundaries['guest-creation-begin'],
                original_paths_and_downloads=boundaries['paths-finished']-boundaries['guests-and-setup-ready'],
                tool_sum=sum(p['guest_action_sum_ns'] for p in task['paths'].values())),
            observed_host_available_min_bytes=outer['observed_host_available_min_bytes'],available_after_cleanup_bytes=outer['host_available_after_cleanup_bytes'],
            checks=dict(original_replay='PASS',official_original_0_pass_2_fail='PRESERVED',real_targets='PASS',p0_zero='PASS',
                samplers_and_query_reaped='PASS',cleanup='PASS')))
    groups.sort(key=lambda g:g['mode']!='shared');s,p=groups;difference=p['endpoint_resident_bytes']-s['endpoint_resident_bytes']
    assert difference==pair['comparison']['endpoint_difference_bytes']
    return dict(status=STATUS,actual_actions=378,pair=relative(directory),execution_order=order,groups=groups,
        endpoint_difference_bytes=difference,endpoint_reduction_fraction=difference/p['endpoint_resident_bytes'],source_returned_to_stopped=True)


def summarize(pairs):
    if len(pairs)!=3:raise ValueError('exactly three completed pairs required')
    expected=[['shared','private'],['private','shared'],['shared','private']];rows=[]
    if len({p['pair'] for p in pairs})!=3:raise ValueError('duplicate pair cannot count as a repeat')
    for i,p in enumerate(pairs):
        if p['status']!=STATUS or p['actual_actions']!=378 or not p['source_returned_to_stopped']:
            raise ValueError('incomplete pair cannot enter statistics')
        order=p.get('execution_order',[g['mode'] for g in p['groups']])
        if order!=expected[i]:raise ValueError('expected alternating pair order')
        by_mode={g['mode']:g for g in p['groups']}
        if len(p['groups'])!=2 or set(by_mode)!={'shared','private'}:raise ValueError('one group of each mode required')
        for mode,count,users in [('shared',19,7),('private',133,1)]:
            g=by_mode[mode]
            if g['objects']!=count or g['users_per_object']!=users or g['points'][0]['resident_bytes']!=0:
                raise ValueError('target count/users or zero initial residency differs')
        s,u=(by_mode[k]['endpoint_resident_bytes'] for k in ['shared','private'])
        difference=u-s;fraction=difference/u
        if p['endpoint_difference_bytes']!=difference or p['endpoint_reduction_fraction']!=fraction:
            raise ValueError('saved result does not match observed endpoints')
        rows.append(dict(pair_index=i+1,pair=p['pair'],execution_order=order,shared_bytes=s,private_bytes=u,
            difference_bytes=difference,reduction_fraction=fraction,reused=i==0))
    def stats(key):
        values=[r[key] for r in rows]
        return dict(mean=statistics.mean(values),minimum=min(values),maximum=max(values))
    return dict(status='PASS_THREE_COLD_GRPO_PAIRS',scope='Initial lower-file cache only, not total host RAM',
        actual_actions=1134,new_actions=756,reused_actions=378,pair_rows=rows,pairs=pairs,
        statistics={k:stats(k) for k in ['shared_bytes','private_bytes','difference_bytes','reduction_fraction']},
        mean_definition='Arithmetic mean of three per-pair reductions; not reduction computed after pooling endpoints',
        guest_samples=sum(g['guest_samples'] for p in pairs for g in p['groups']))


def plot(value,out):
    os.environ.setdefault('MPLCONFIGDIR',str(ROOT/'.artifacts/matplotlib'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    colors=['#2472ad','#e88a19'];rows=value['pair_rows'];x=list(range(3))
    fig,axes=plt.subplots(1,2,figsize=(13,5))
    for i,key in enumerate(['shared_bytes','private_bytes']):
        bars=axes[0].bar([n+(-.18 if i==0 else .18) for n in x],[r[key]/2**20 for r in rows],width=.34,
            color=colors[i],label='Shared' if i==0 else 'Independent')
        for b,r in zip(bars,rows):axes[0].text(b.get_x()+b.get_width()/2,b.get_height()+20,f'{r[key]/2**20:.1f}',ha='center',fontsize=9)
    labels=['Pair 1\nShared first','Pair 2\nIndependent first','Pair 3\nShared first']
    axes[0].set(xticks=x,xticklabels=labels,ylabel='Resident MiB',title='Same completed stage: actual P1 residency')
    axes[0].set_ylim(0,max(r['private_bytes']/2**20 for r in rows)*1.2)
    percentages=[100*r['reduction_fraction'] for r in rows];mean=100*value['statistics']['reduction_fraction']['mean']
    axes[1].plot(x,percentages,'o-',color=colors[0]);axes[1].axhline(mean,color=colors[1],linestyle='--',label=f'Mean {mean:.4f}%')
    margin=max(.03,(max(percentages)-min(percentages))*.3)
    axes[1].set(xticks=x,xticklabels=['Pair 1','Pair 2','Pair 3'],ylabel='Reduction (%)',title='Per-pair reduction (detail scale)',
        ylim=(min(percentages)-margin,max(percentages)+margin),xlim=(-.25,2.25))
    for n,y in zip(x,percentages):axes[1].annotate(f'{y:.4f}%',(n,y),xytext=(0,8),textcoords='offset points',ha='center',fontsize=10)
    axes[1].legend(loc='lower left')
    for ax in axes:ax.grid(axis='y',alpha=.25);ax.set_axisbelow(True)
    fig.suptitle('Cold GRPO: 3 pairs, 7 guests per group, normal default reclaim')
    fig.legend(*axes[0].get_legend_handles_labels(),loc='upper center',bbox_to_anchor=(.5,.94),ncol=2,frameon=False)
    fig.tight_layout(rect=(0,0,1,.88));fig.savefig(out/'summary.png',dpi=140);fig.savefig(out/'summary.svg',metadata={'Date':None});plt.close(fig)
    fig,axes=plt.subplots(1,3,figsize=(16,4.6),sharey=True)
    for index,(ax,pair) in enumerate(zip(axes,value['pairs'])):
        for color,mode in zip(colors,['shared','private']):
            g=next(g for g in pair['groups'] if g['mode']==mode);origin=g['points'][0]['end_monotonic_ns']
            ax.plot([(p['end_monotonic_ns']-origin)/1e9 for p in g['points']],
                [p['resident_bytes']/2**20 for p in g['points']],'o-',color=color,markersize=3,label='Shared' if mode=='shared' else 'Independent')
            ax.axvline((g['boundaries']['guests-and-setup-ready']-origin)/1e9,color=color,linestyle='--',alpha=.5)
        ax.set(title=f'Pair {index+1}: '+('independent first' if index==1 else 'shared first'),xlabel='Seconds from own P0')
        ax.grid(axis='y',alpha=.25)
    axes[0].set_ylabel('Target-file resident MiB')
    fig.suptitle('Actual sparse residency observations; dashed lines mark setup ready')
    fig.legend(*axes[0].get_legend_handles_labels(),loc='upper center',bbox_to_anchor=(.5,.94),ncol=2,frameon=False)
    fig.text(.025,.015,'Separate runs: equal seconds are not equal task progress. Compare completed-stage endpoints for savings.',fontsize=10)
    fig.tight_layout(rect=(0,.06,1,.86));fig.savefig(out/'curves.png',dpi=140);fig.savefig(out/'curves.svg',metadata={'Date':None});plt.close(fig)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--first',type=Path,required=True)
    parser.add_argument('--pair',type=Path,action='append',required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if len(args.pair)!=2:parser.error('supply exactly two new pairs')
    first=read(args.first);new=[audit_pair(p) for p in args.pair];value=summarize([first,*new])
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'summary.json').write_text(json.dumps(value,indent=2)+'\n');plot(value,args.output)
    print(json.dumps(dict(status=value['status'],statistics=value['statistics'],actual_actions=value['actual_actions'],new_actions=value['new_actions'],result_dir=str(args.output)),indent=2))


if __name__=='__main__':main()
