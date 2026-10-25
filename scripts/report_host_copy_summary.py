"""Summarize accepted GRPO/BPO file-copy controls from saved JSON only."""
import argparse
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SOURCES={
    'grpo':ROOT/'docs/records/evidence/host-full-grpo-private-20261025.json',
    'bpo':ROOT/'docs/records/evidence/host-bpo-copy-live-20261025.json',
}


def read(path):return json.loads(Path(path).read_text())


def collect_pair(path,*,root=ROOT):
    value=read(path);schedule=value['schedule']
    assert schedule in ('grpo','bpo')
    assert value['status']==('PASS_SAVED_RESULT_AUDIT' if schedule=='grpo' else 'PASS_BPO_COPY_PAIR_AUDIT')
    groups={};action_sum=0;primary_keys=[];page_sizes=set()
    for mode,count,users in [('shared',19,7),('private',133,1)]:
        saved=value['groups'][mode];raw=root/saved['storage']/'prettier6604'
        task=read(raw/'result.json');obs=read(raw/'host-full-rootfs.json')
        assert task['status']=='PASS' and obs['status']=='OBSERVED' and obs['schedule']==schedule
        assert task['actual_action_count']==(189 if schedule=='grpo' else 84)
        action_sum+=task['actual_action_count'];points={}
        for phase in ('P0','P1'):
            data=obs[phase];rows=data['objects']
            assert len(rows)==count and len({(r['identity']['device'],r['identity']['inode']) for r in rows})==count
            assert all(len(r['users'])==users and r['residency']['stable'] for r in rows)
            assert data['resident_bytes']==saved[phase+'_resident_bytes']==sum(r['residency']['resident_bytes'] for r in rows)
            for row in rows:
                n=row['residency']['resident_pages']
                if n:page_sizes.add(row['residency']['resident_bytes']//n)
            points[phase]=data['resident_bytes']
        primary_keys.append({r['relative'] for r in obs['P1']['objects']})
        generated=None
        if schedule=='bpo':
            assert obs['generated_layers']['P0']['status']=='NOT_CREATED' and obs['generated_layers']['P0']['resident_bytes'] is None
            gen=obs['generated_layers']['P1'];rows=gen['objects']
            assert len(rows)==2 and [len(r['users']) for r in rows]==[7,4]
            assert gen['resident_bytes']==sum(r['residency']['resident_bytes'] for r in rows)
            generated=dict(query_ns=gen['elapsed_ns'],layers=[dict(step=r['step'],size=r['identity']['size'],resident_bytes=r['residency']['resident_bytes'],users=len(r['users'])) for r in rows])
        query_ns=sum(obs[p]['elapsed_ns'] for p in ('P0','P1'))
        groups[mode]=dict(**points,objects=count,users_per_object=users,runtime_change_bytes=points['P1']-points['P0'],
            main_query_ns=query_ns,total_query_ns=query_ns+(generated['query_ns'] if generated else 0),
            generated_layers=generated,storage=saved['storage'],task_actions=task['actual_action_count'])
    assert primary_keys[0]==primary_keys[1] and len(primary_keys[0])==19 and len(page_sizes)<=1
    shared,private=groups['shared'],groups['private']
    d0=private['P0']-shared['P0'];d1=private['P1']-shared['P1']
    fraction=d1/private['P1'] if private['P1'] else None
    old=value['comparison']
    assert (d0,d1,d1-d0,fraction)==(old['preparation_difference_bytes'],old['endpoint_difference_bytes'],old['difference_change_bytes'],old['endpoint_reduction_fraction'])
    assert action_sum==value['actual_actions']
    return dict(schedule=schedule,source=str(path),pair=value['pair'],actual_actions=action_sum,page_size_bytes=next(iter(page_sizes),None),
        groups=groups,P0_difference_bytes=d0,P1_difference_bytes=d1,runtime_difference_change_bytes=d1-d0,reduction_fraction=fraction,
        accepted_pairs=1,standard_deviation=None)


def summarize(grpo,bpo,*,root=ROOT):
    pairs=[collect_pair(grpo,root=root),collect_pair(bpo,root=root)]
    assert [p['schedule'] for p in pairs]==['grpo','bpo']
    return dict(status='PASS_OFFLINE_COPY_SUMMARY',scope='Initial 19 lower-file cache only; actual shared/private comparison within each schedule; preparation included',
        total_accepted_actions=sum(p['actual_actions'] for p in pairs),schedules=pairs,
        excluded=['Whole host RAM','Guest Cached physical attribution','BPO generated layers from main savings','GRPO versus BPO causal performance or memory advantage'],
        new_live_actions=0)


def markdown(value):
    lines=['# GRPO／BPO指定文件共享对照', '',
        '只汇总已通过的原始结果；每种调度各一对，不能由此给出重复统计。单位GiB，1 GiB＝2^30字节。', '',
        '| 调度 | 结束共享驻留 | 结束独立驻留 | 实际减少 | 减少比例 | 运行期间差值再增/MiB |',
        '| --- | ---: | ---: | ---: | ---: | ---: |']
    for p in value['schedules']:
        s,t=p['groups']['shared'],p['groups']['private'];rate=p['reduction_fraction']
        percent=f'{rate*100:.6f}%' if rate is not None else '未定义'
        lines.append(f"| {p['schedule'].upper()} | {s['P1']/2**30:.6f} | {t['P1']/2**30:.6f} | {p['P1_difference_bytes']/2**30:.6f} | {percent} | {p['runtime_difference_change_bytes']/2**20:.6f} |")
    lines+=['', '收益包含准备缓存，仅覆盖初始19层文件，不代表整体RAM或guest Cached的物理页；两种调度分别内部比较。', '',
        '| 调度 | 共享全部驻留查询/秒 | 独立全部驻留查询/秒 | 实际动作/对 |', '| --- | ---: | ---: | ---: |']
    for p in value['schedules']:
        lines.append(f"| {p['schedule'].upper()} | {p['groups']['shared']['total_query_ns']/1e9:.6f} | {p['groups']['private']['total_query_ns']/1e9:.6f} | {p['actual_actions']} |")
    lines+=['', 'BPO总查询时间包含两生成层的独立查询，生成层容量不加入主收益。', '',
        '指标解释、两阶段完整表、图和复现命令：见根目录README中的结果报告入口。',
        '每组原始任务、驻留记录和生成层数值的位置保存在`summary.json`中。']
    return '\n'.join(lines)+'\n'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--grpo',type=Path);parser.add_argument('--bpo',type=Path)
    parser.add_argument('--example',action='store_true',help='Use bundled public metadata excerpts; no local experiment artifacts required')
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    if args.example and (args.grpo or args.bpo):parser.error('example cannot combine with explicit grpo/bpo inputs')
    base=ROOT/'examples/host-copy-summary'
    grpo=base/'grpo.json' if args.example else args.grpo or SOURCES['grpo']
    bpo=base/'bpo.json' if args.example else args.bpo or SOURCES['bpo']
    value=summarize(grpo,bpo);args.output.mkdir(parents=True,exist_ok=False)
    (args.output/'summary.json').write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')
    (args.output/'SUMMARY.md').write_text(markdown(value))
    print('status='+value['status']+' result_dir='+str(args.output.resolve()))


if __name__=='__main__':main()
