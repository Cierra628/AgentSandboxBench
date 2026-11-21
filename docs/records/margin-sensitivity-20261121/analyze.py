"""Run with python3 analyze.py. Inputs and outputs are relative to this file."""
import csv
import json
from pathlib import Path
from statistics import quantiles

out = Path(__file__).resolve().parent
src = out.parent / 'repaired-five-speed-20261121'


def load(name):
    with (src / name).open() as f:
        return list(csv.DictReader(f))


def write(name, rows):
    with (out / name).open('w') as f:
        writer = csv.DictWriter(f, rows[0], lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def table(header, rows):
    return '\n'.join('| ' + ' | '.join(map(str, r)) + ' |' for r in
                     [header, ['---'] * len(header), *rows])


rounds, workers = load('rounds.csv'), load('workers.csv')
summary = json.loads((src / 'summary.json').read_text())
peak = summary['first_build_ms']['max']
assert len(rounds) == 5 and len(workers) == 35
detail, scenarios, grid = [], [], []
pages_table, frame_table, delta_table, grid_table, switch_table = [], [], [], [], []
for row in rounds:
    n = row['attempt']
    ws = [w for w in workers if w['attempt'] == n]
    assert len(ws) == 7 and row['status'] == row['cleanup'] == 'PASS'
    roles = [w['role'] for w in ws]
    ready, frame, sweep, cost = {}, {}, {}, {}
    last = row['last_ready']
    total, merge, base = (float(row[k]) for k in ('total_ms', 'merge_ms', 'ready_ms'))
    gap = total - base - merge
    for w in ws:
        r = w['role']
        ready[r], frame[r] = float(w['ready_ms']), float(w['first_build_ms'])
        sweep[r] = float(w['sweep_done_ms']) - float(w['start_ms']) - frame[r]
        cost[r] = 1000 * sweep[r] / int(w['confirmed_pages'])
        detail.append(dict(attempt=n, role=r, confirmed_pages=int(w['confirmed_pages']),
                           ready_ms=ready[r], first_build_ms=frame[r], sweep_ex_first_ms=sweep[r],
                           us_per_page=cost[r], first_build_to_tie_ms=frame[r]+base-ready[r]))
    assert base == max(ready.values()) == ready[last] and gap >= 0
    assert row['sweep_critical'] == max(ws,key=lambda w:float(w['sweep_done_ms']))['role']

    def calc(g=0, delta=0, target=None, only=False):
        values = {r: ready[r]+g*sweep[r]+((delta if target is None else target-frame[r])
                  if not only or r == last else 0) for r in roles}
        who = max(values, key=values.get)
        return values[who]+merge+gap, who

    assert abs(calc()[0]-total) < 1e-8
    limit, limiting = min(((1000-total+base-ready[r])/sweep[r],r) for r in roles)
    page_line = [n, f'{total:.3f}']
    for g in [.1,.2,.5]:
        t,r = calc(g)
        page_line.append(f'{t:.3f} / {1000-t:+.3f} / {r.split("-")[-1]}')
        scenarios.append(dict(attempt=n,growth=g,total_ms=t,margin_ms=1000-t,under_1s=t<1000,
                              last_ready=r,threshold_growth=limit,threshold_role=limiting,gap_ms=gap))
    pages_table.append(page_line+[f'{100*limit:.4f}% / {limiting[-1]}'])
    for dest,absolute in [(frame_table,True),(delta_table,False)]:
        line = [n, row['sweep_critical'][-1]+' / '+last[-1]]
        for target in [250,300]:
            for only in [False,True]:
                t,r = calc(target=target if absolute else None,delta=target-peak,only=only)
                line.append(f'{t:.3f} / {r[-1]}')
        dest.append(line)
    take = min((base-ready[r],r) for r in roles if r != last)
    switch_table.append([n,last[-1],take[1][-1],f'{frame[take[1]]:.3f}',
                         f'{take[0]:.3f}',f'{frame[take[1]]+take[0]:.3f}'])
    line=[n]
    for g in [0,.05,.1,.15,.2,.5]:
        t,r = calc(g)
        # With all frames shifted equally the maximum shifts by exactly delta.
        # With only the original last branch shifted, check the others first.
        other=max(ready[i]+g*sweep[i]+merge+gap for i in roles if i!=last)
        solo=1000-(ready[last]+g*sweep[last]+merge+gap) if other<1000 else None
        grid.append(dict(attempt=n,growth=g,page_only_last=r,uniform_delta_boundary_ms=1000-t,
                         original_last_only_delta_boundary_ms=solo,other_already_over=other>=1000))
        line.append(f'{1000-t:+.3f}')
    grid_table.append(line)

write('worker-costs.csv', detail)
write('page-scenarios.csv', scenarios)
write('joint-boundaries.csv', grid)
q1,med,q3=quantiles([v['us_per_page'] for v in detail],n=4,method='inclusive')
s=f"""# 七进程查询：1 秒余量对页数和首帧耗时有多敏感？

基线 `cad1058`。[五批输入](../repaired-five-speed-20261121/)：rounds.csv、workers.csv、summary.json。只用已存记录，所有时间均为毫秒（另标单位除外）。

**实测**：五批派发至批级可用为 919.763–959.253 ms。**换算**：若各支确认页数同比例增加、其余条件固定，五批各自触线的增幅在 {100*min(v['threshold_growth'] for v in scenarios):.3f}%–{100*max(v['threshold_growth'] for v in scenarios):.3f}% 之间。最先触线的是第 4 批。预算只覆盖派发至批级结果可用；探针挂载（实测 9.98–10.25 秒）、恢复和导出在派发前，均不计入。

## 页数：按支计算，再找最后就绪者

按指定口径，用每支的（暂停查询结束时刻 − 查询开始时刻 − 首帧构造耗时）除以确认页数。35 支的经验成本**换算**中位数为 **{med:.3f} µs/页**，四分位区间 **{q1:.3f}–{q3:.3f} µs/页**（线性插值）。这是每支历史读窗的平均成本，用作增加页数的近似，不是控制其他变量后实测的导数。无需拟合固定截距。[35 支成本及接管门槛](worker-costs.csv)。

设某支原就绪时间为 R、扣去首帧的查询时间为 S，页数增幅为 g，首帧增加 d。该支换算就绪时间为 R + gS + d。整轮取七支就绪时间的**最大值**，再加本批实测合并时间和调度间隔（原 total − 原 ready − 原 merge）。各支交替成为最慢者时，曲线连续、斜率可能改变；不是七份延迟相加。

下表情景列依次为 **整轮 / 余量 / 最后就绪支号**。余量为正表示严格低于 1 秒，零或负即不通过；阈值列是首次等于 1 秒的增幅及触线支号，判定用未取整值。

"""
s+=table(['批（实测编号）','原整轮（实测）','页 +10%（换算）','页 +20%（换算）','页 +50%（换算）','临界增幅 / 支（换算）'],pages_table)
s+="""

实测页数仅 36,102–37,918 页。+50% 把每支放大到自身原值的 1.5 倍，最高为 56,877 页，已远离观测区间。表中是按指定比例的**换算**；将其解释为真实未来耗时则是**推算**。超过观测最大值 1.5 倍的输入不作数值预测。核算、输出耗时未随页数建模，如它们也增长，表中余量偏乐观。合并保持各批原值：第 3 批 169,538 页合并 10.426 ms，第 4 批 168,832 页却用 37.560 ms，不能从页数拟合。固定合并耗时的偏差方向不确定：实际变慢则乐观，变快则保守。调度间隔及恢复运行后再取清单的等待也保持原值，未模拟共同等待时间随负载改变。

## 首帧：指定绝对耗时，与统一增加同一段时间不同

主表把被选中的首帧从**该支实测值**提高到 250 或 300 ms；“仅最后支”固定选择原批最后 ready 的一支。每个情景格为 **整轮 / 换算后最后支号**；角色列先列原 sweep 最晚，再列原 ready 最晚。
"""
head=['批（实测编号）','sweep / ready 最晚支（实测）','250 全部（换算）','250 仅最后支（换算）','300 全部（换算）','300 仅最后支（换算）']
s+=table(head,frame_table)
s+="""

若“七支同时增加”指**统一加上历史最大值到目标的差额**，则每个被选中者加 48.691506 或 98.691506 ms（实测最大值 201.308494 ms）。这使全体最大首帧达到目标，并非每支都达到目标。两种施加范围此时得到相同整轮：原最后就绪者都增加了完整差额，没有其他支超过它。

"""
s+=table(head,delta_table)
s+="""

**哪支会接管？** 当只有某一支变慢、页数不变，它的首帧需达到“本支原首帧 + 原最后支就绪时刻 − 本支原就绪时刻”才能追平，严格超过才独自接管。下表列出每批只需增加最少时间的挑战者；其余各支的绝对门槛见 worker-costs.csv。页数也变化时，用各支 R + gS 更新就绪时刻后再比较。

"""
s+=table(['批（实测编号）','原最后支（实测）','最近挑战支（换算）','挑战支原首帧（实测）','追平需增加（换算）','追平时首帧（换算）'],switch_table)
s+="""

## 联合边界：页数增长后，首帧还能增加多少？

下表为七支**均增加相同毫秒数**时的首帧增量上限（换算，ms）。以未取整值为准：增量严格小于格中数值才低于 1 秒；负值表示仅页数增长就已超时。任意页数增幅 g 的精确边界为 minᵢ(1000 − 合并 − 调度间隔 − Rᵢ − gSᵢ)，因此不需要假定关键支固定。

"""
s+=table(['批（实测编号）']+[f'页 +{g}%（换算）' for g in [0,5,10,15,20,50]],grid_table)
s+="""

若只有原最后支的首帧增长，先检查其余六支在该页数下是否已经触线；若没有，用 1000 减去该支按页增长后的整轮时间，即为其单独可增加的上限。[两种边界的完整精度与各页数下最后支号](joint-boundaries.csv)。若设定的是绝对首帧目标 F，则将每支的 d 替换为 F − 本支实测首帧；各支分别代入同一公式，必须七支都严格低于预算。

**使用结论：** 要求这五批全部通过时，每个情景取五批最小余量，而不是取中位数。基线五批均通过；页数增长 10% 已不能保证五批全过，增长 20% 或 50% 时五批均不通过。联合边界仅针对本次观察到的页数、首帧和固定其他阶段的换算。

**宿主负载升高：需要补测。** 最小设计是在一个事先选定的代表性繁忙时段，记录实际宿主负载，保持同一快照、七进程、14 探针、两遍读与 NumPy 合并不变，连续五批。逐批同时检查七支绑定、正常页数量级（任一支低于 30,000 则止损）及派发至批级可用严格低于 1000 ms；失败不补跑。未执行。

复算：运行同目录的 `python3 analyze.py`。原始值取自上述五批；新增 CSV 中 attempt、role、confirmed_pages、ready_ms、first_build_ms 来自实测，其余成本、阈值、情景值与角色选择均为换算。
"""
(out/'REPORT.md').write_text(s)
