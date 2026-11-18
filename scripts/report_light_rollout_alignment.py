"""Offline timeline for saved light BPO rounds. No proc, service or guest access."""
import argparse
import csv
import json
import math
import re
from pathlib import Path

ROLES=('backbone','early_1','early_2','early_3','late_1','late_2','late_3')


def read(path):return json.loads(path.read_text())


def extract_rounds(rows):
    """Keep measured read windows; never fill a missing role or shift its clock."""
    if not rows:raise ValueError('empty light series')
    origin=rows[0]['start_monotonic_ns'];parent=rows[0]['parent'];measurements=[];rounds=[]
    for i,r in enumerate(rows):
        if r['round']!=i or r['parent']!=parent:raise ValueError('round or parent changed')
        left=r['start_monotonic_ns'];right=r['end_monotonic_ns']
        if i and left<=rows[i-1]['start_monotonic_ns']:raise ValueError('non-increasing round clock')
        windows={}
        for item in r['parent_reads']:
            key=Path(item['path']).name
            if key in windows or item['status']!='READ':raise ValueError('invalid parent read')
            windows[key]=(item['start_monotonic_ns'],item['end_monotonic_ns'])
        if set(windows)!={'memory.current','memory.stat'}:raise ValueError('missing parent read window')
        values=[]
        for key in ('current','file','anon','kernel','shmem'):
            a,b=windows['memory.current' if key=='current' else 'memory.stat']
            values.append(('parent',key,r['parent_counters'][key]/2**20,a,b))
        for role,v in r['vmms'].items():
            c=v['counters'];values.append((role,'pss',c['pss_kib']/1024,c['pss_read_start_monotonic_ns'],c['pss_read_end_monotonic_ns']))
        for role,metric,mib,a,b in values:
            if not left<=a<=b<=right:raise ValueError('read window outside round')
            measurements.append(dict(round=i,role=role,metric=metric,value_mib=mib,start_ns=a,end_ns=b,
                start_seconds=(a-origin)/1e9,end_seconds=(b-origin)/1e9,mid_seconds=((a-origin)+(b-origin))/2e9))
        if set(r['vmms']) & set(r['transition_roles']) or set(r['vmms'])|set(r['transition_roles'])!=set(r['expected_roles']):
            raise ValueError('active and pending roles do not partition expected roles')
        full=not r['transition_roles']
        actual=sum(v['counters']['pss_kib'] for v in r['vmms'].values())
        if r['denominator']!=len(r['vmms']) or r['pss_sum_kib']!=actual:raise ValueError('partial sum or denominator differs')
        if (full and r['all_roles_pss_sum_kib']!=actual) or (not full and r['all_roles_pss_sum_kib'] is not None):
            raise ValueError('incomplete round claims full total')
        union_start=min(v[3] for v in values);union_end=max(v[4] for v in values)
        rounds.append(dict(round=i,start_ns=left,end_ns=right,start_seconds=(left-origin)/1e9,end_seconds=(right-origin)/1e9,
            duration_ms=(right-left)/1e6,union_start_ns=union_start,union_end_ns=union_end,union_ms=(union_end-union_start)/1e6,
            active_count=r['denominator'],full_pss_mib=actual/1024 if full else None,
            pending_roles=','.join(sorted(k for k,v in r['transition_roles'].items() if v['state']=='PENDING')),
            saving_roles=','.join(sorted(k for k,v in r['transition_roles'].items() if v['state']=='SAVING'))))
    return origin,measurements,rounds


def extract_events(raw,task,origin):
    events=[]
    for e in read(raw/'events.json'):
        stage=re.sub(r'^\d+-','',e['stage'])
        if stage=='cold-backbone':kind,role='create','backbone'
        elif stage.startswith('restore-') and stage[8:] in ROLES:kind,role='restore',stage[8:]
        elif stage=='snapshot-call':kind,role='save','backbone'
        else:continue
        if e['exit_code']!=0:raise ValueError('failed lifecycle command')
        a=e['start_monotonic_ns'];b=a+e['duration_ns']
        events.append(dict(kind=kind,role=role,start_ns=a,end_ns=b,source=str(raw/'events.json'),field=f"stage={e['stage']}; start_monotonic_ns,duration_ns"))
    counts={k:sum(e['kind']==k for e in events) for k in ('create','save','restore')}
    if counts!=dict(create=1,save=2,restore=6):raise ValueError('expected one creation, two saves and six restores')
    for c in read(raw/'identity-events.json'):
        if c['state'] not in ('ACTIVE','SAVING','RESUMED_SOURCE'):raise ValueError('unexpected identity state')
        events.append(dict(kind=c['state'],role=c['role'],start_ns=c['monotonic_ns'],end_ns=c['monotonic_ns'],
            source=str(raw/'identity-events.json'),field=f"generation={c['generation']}; monotonic_ns"))
    for key in ('rollout_start_monotonic_ns','first_tool_start_monotonic_ns','original_paths_end_monotonic_ns','rollout_end_monotonic_ns'):
        events.append(dict(kind=key,role='all',start_ns=task[key],end_ns=task[key],source=str(raw/'result.json'),field=key))
    events.sort(key=lambda e:e['start_ns'])
    for e in events:e.update(start_seconds=(e['start_ns']-origin)/1e9,end_seconds=(e['end_ns']-origin)/1e9,duration_seconds=(e['end_ns']-e['start_ns'])/1e9)
    return events


def nearest_rounds(events,rounds):
    """Report brackets without inventing a measurement at the event timestamp."""
    result=[]
    for e in events:
        for boundary in ('start','end') if e['start_ns']!=e['end_ns'] else ('start',):
            t=e[boundary+'_ns'];before=[r for r in rounds if r['end_ns']<=t];after=[r for r in rounds if r['start_ns']>=t]
            overlap=[r for r in rounds if r['start_ns']<t<r['end_ns']]
            result.append(dict(kind=e['kind'],role=e['role'],boundary=boundary,time_ns=t,
                preceding_round=before[-1]['round'] if before else None,
                preceding_distance_ms=(t-before[-1]['end_ns'])/1e6 if before else None,
                following_round=after[0]['round'] if after else None,
                following_distance_ms=(after[0]['start_ns']-t)/1e6 if after else None,
                overlapping_rounds=','.join(str(r['round']) for r in overlap)))
    return result


def plots(out,rows,measurements,rounds,events):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.font_manager import FontProperties
    font=FontProperties(fname='/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
    plt.rcParams.update({'font.family':font.get_name(),'axes.unicode_minus':False,'font.size':10,
        'axes.spines.top':False,'axes.spines.right':False,'savefig.facecolor':'white'})
    colors=['#175d91','#d16a28','#238b73','#8659a5','#b84865','#7a7a28','#536679']
    end=rounds[-1]['end_seconds']
    action_end=next(e['start_seconds'] for e in events if e['kind']=='original_paths_end_monotonic_ns')
    saves=[e for e in events if e['kind']=='save'];restores=[e for e in events if e['kind']=='restore']
    def decorate(ax):
        ax.set_xlim(0,end);ax.grid(axis='y',color='#e8ecf0',lw=.6);ax.set_axisbelow(True)
        for e in saves:ax.axvspan(e['start_seconds'],e['end_seconds'],color='#edb64d',alpha=.22,lw=0)
        ax.axvline(action_end,color='#7d4d86',ls='--',lw=1)
    fig,axes=plt.subplots(4,1,figsize=(12,11),sharex=True,gridspec_kw={'height_ratios':[1.3,2,1.6,1.5]},constrained_layout=True)
    fig.suptitle('Prettier BPO｜保存恢复与实测内存曲线',fontsize=19,fontweight='bold')
    ax=axes[0]
    for i,role in enumerate(ROLES):
        active=next(e['start_seconds'] for e in events if e['kind']=='ACTIVE' and e['role']==role)
        ax.hlines(i,active,end,color=colors[i],lw=3,alpha=.55)
        ax.plot(active,i,'o',color=colors[i],ms=4)
    for e in [*saves,*restores,*[e for e in events if e['kind']=='create']]:
        i=ROLES.index(e['role']);ax.broken_barh([(e['start_seconds'],e['duration_seconds'])],(i-.3,.6),facecolors='#ce8b1b' if e['kind']=='save' else '#273d50')
    ax.set_yticks(range(7),ROLES);ax.invert_yaxis();ax.set_ylabel('实例与操作');ax.set_title('圆点：完成身份绑定；深色短条：创建／恢复 CLI；黄色：保存 CLI',loc='left',fontsize=10)
    ax=axes[1]
    for key,color in [('current','#175d91'),('file','#d16a28')]:
        points=[m for m in measurements if m['role']=='parent' and m['metric']==key]
        ax.plot([m['mid_seconds'] for m in points],[m['value_mib'] for m in points],color=color,lw=1.4,label=key)
    ax.set_ylabel('实验组 cgroup 记账量（MiB）');ax.legend(loc='upper left',ncol=2,frameon=False)
    ax=axes[2]
    ax.plot([(r['union_start_ns']-rows[0]['start_monotonic_ns']+(r['union_end_ns']-r['union_start_ns'])/2)/1e9 for r in rounds],
        [r['full_pss_mib'] if r['full_pss_mib'] is not None else math.nan for r in rounds],color='#238b73',lw=1.5)
    ax.set_ylabel('七 VMM 的 PSS 之和（MiB）');ax.set_ylim(bottom=0)
    first=next(r['start_seconds'] for r in rounds if r['active_count']==7)
    ax.axvspan(0,first,color='#adb6c0',alpha=.15,lw=0)
    ax.text(first/2,.5,'实例尚未齐备／保存期间\n不填零、不补算七份总量',transform=ax.get_xaxis_transform(),ha='center',color='#55616c')
    ax=axes[3]
    ax.plot([r['start_seconds'] for r in rounds],[r['union_ms'] for r in rounds],color='#175d91',lw=1,label='本轮父组＋有效 PSS 联合窗口')
    ax.plot([r['start_seconds'] for r in rounds],[r['duration_ms'] for r in rounds],color='#8a9097',lw=.8,label='含身份检查的整轮')
    ax.axhline(100,color='#b74949',ls=':',lw=1,label='名义周期 100 ms')
    ax.set_ylabel('实际读取／轮次耗时（ms）');ax.legend(loc='upper left',frameon=False,fontsize=9);ax.set_xlabel('距第一轮采样起点（秒，host 单调时钟）')
    for ax in axes:decorate(ax)
    axes[-1].annotate('动作及下载结束',xy=(action_end,0),xytext=(action_end+1,12),color='#7d4d86',fontsize=9)
    fig.savefig(out/'overview.png',dpi=160);fig.savefig(out/'overview.svg');plt.close(fig)
    fig,axes=plt.subplots(7,1,figsize=(12,14),sharex=True,constrained_layout=True)
    fig.suptitle('逐沙箱 VMM 的 PSS｜只画已绑定身份的实测值',fontsize=18,fontweight='bold')
    for i,(role,ax) in enumerate(zip(ROLES,axes)):
        series={m['round']:m for m in measurements if m['role']==role and m['metric']=='pss'}
        xs=[series[r['round']]['mid_seconds'] if r['round'] in series else (r['start_seconds']+r['end_seconds'])/2 for r in rounds]
        ys=[series[r['round']]['value_mib'] if r['round'] in series else math.nan for r in rounds]
        ax.plot(xs,ys,color=colors[i],lw=1.2);ax.set_ylabel(role+'\nPSS（MiB）');ax.set_ylim(bottom=0);ax.set_xlim(0,end);ax.grid(axis='y',color='#e8ecf0')
        active=next(e['start_seconds'] for e in events if e['kind']=='ACTIVE' and e['role']==role)
        ax.axvspan(0,active,color='#adb6c0',alpha=.16,lw=0)
        if role=='backbone':
            for begin in [e for e in events if e['kind']=='SAVING']:
                finish=next(e for e in events if e['kind']=='RESUMED_SOURCE' and e['start_ns']>begin['start_ns'])
                ax.axvspan(begin['start_seconds'],finish['start_seconds'],color='#edb64d',alpha=.25,lw=0)
        ax.axvline(action_end,color='#7d4d86',ls='--',lw=1)
    axes[0].set_title('灰色：尚未绑定；黄色：主干保存期间的查询缺口；虚线：动作及下载结束',loc='left',fontsize=10)
    axes[-1].set_xlabel('距第一轮采样起点（秒，host 单调时钟）')
    fig.savefig(out/'per-vmm-pss.png',dpi=160);fig.savefig(out/'per-vmm-pss.svg');plt.close(fig)
    slow=max((r for r in rounds if r['active_count']==7),key=lambda r:r['union_ms'])
    ms=[m for m in measurements if m['round']==slow['round'] and (m['role']!='parent' or m['metric'] in ('current','file'))]
    fig,ax=plt.subplots(figsize=(11,5.5),constrained_layout=True)
    for i,m in enumerate(ms):
        start=(m['start_ns']-slow['start_ns'])/1e6;duration=(m['end_ns']-m['start_ns'])/1e6
        ax.broken_barh([(start,duration)],(i-.3,.6),facecolors='#175d91' if m['role']=='parent' else '#238b73')
    ax.set_yticks(range(len(ms)),[('父组 memory.current' if m['metric']=='current' else '父组 memory.stat:file') if m['role']=='parent' else m['role']+' PSS' for m in ms]);ax.invert_yaxis()
    ax.axvline(100,color='#b74949',ls=':',label='名义周期 100 ms');ax.set_xlim(0,slow['duration_ms']+3)
    ax.set_xlabel('距该轮起点（ms）');ax.set_ylabel('实际读取对象');ax.grid(axis='x',color='#e8ecf0');ax.legend(frameon=False)
    ax.set_title(f"最慢完整轮次 #{slow['round']}｜联合窗口 {slow['union_ms']:.3f} ms；整轮 {slow['duration_ms']:.3f} ms",fontsize=14)
    fig.savefig(out/'slowest-read-windows.png',dpi=160);fig.savefig(out/'slowest-read-windows.svg');plt.close(fig)


def report(source,out):
    task=read(source/'result.json');rows=[json.loads(l) for l in (source/'light-memory.jsonl').read_text().splitlines()]
    if task['status']!='PASS' or task['schedule']!='bpo':raise ValueError('expected accepted BPO run')
    origin,measurements,rounds=extract_rounds(rows);events=extract_events(source,task,origin);brackets=nearest_rounds(events,rounds)
    out.mkdir(parents=True,exist_ok=False)
    for name,data in [('measurements.csv',measurements),('rounds.csv',rounds),('events.csv',events),('event-brackets.csv',brackets)]:
        with (out/name).open('x') as f:
            w=csv.DictWriter(f,fieldnames=list(data[0]));w.writeheader();w.writerows(data)
    plots(out,rows,measurements,rounds,events)
    value=dict(status='PASS',source=str(source),origin_monotonic_ns=origin,rounds=len(rows),measurements=len(measurements),events=len(events),
        complete_seven_rounds=sum(r['active_count']==7 for r in rounds),rounds_over_nominal_100ms=[r['round'] for r in rounds if r['duration_ms']>100],
        atomic_snapshot=False,interpolated_values=0,business_runs=0,
        timestamp_method='one host monotonic clock; curves use each measured read-window midpoint; CSV retains both boundaries; no wall-clock conversion',
        input_files=['result.json','light-memory.jsonl','events.json','identity-events.json'])
    (out/'summary.json').write_text(json.dumps(value,indent=2)+'\n');return value


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();print(json.dumps(report(a.source.resolve(),a.output.resolve()),indent=2))
