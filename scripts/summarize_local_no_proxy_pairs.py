"""Summarize three fixed saved paired batches, using public JSON/TSV only."""
import argparse
import csv
import json
import math
import os
from pathlib import Path
from summarize_local_no_proxy import ROOT,rows

TASKS=(('prettier','prettier__prettier-6604',29,'Prettier #6604','20261031',105,68),
       ('sphinx','sphinx-doc__sphinx-7590',35,'Sphinx #7590','20261031',116,80),
       ('xarray','pydata__xarray-6992',35,'Xarray #6992','20261101',116,80))
TIMINGS=('tool_seconds','frozen_replay_seconds','controller_seconds','cold_create_seconds','official_tests_seconds',
         'grouped_workflow_seconds','archive_preparation_seconds','input_import_seconds','query_seconds')


def close(a,b):assert math.isclose(a,b,rel_tol=1e-9,abs_tol=1e-8),(a,b)
def read(path):return json.loads(path.read_text())


def collect(root=ROOT):
    tasks=[]
    for name,task_id,actions,label,date,files,boundary in TASKS:
        directory=root/'results'/f'{name}-local-no-proxy-sampling-controls-{date}';source=read(directory/'summary.json')
        assert source['status']=='PASS' and source['task_id']==task_id
        assert '4 GiB/2 vCPU' in source['scope'] and 'local-no-proxy' in source['scope'] and 'default reclaim' in source['scope']
        assert source['correctness']['actions']==actions*4 and source['correctness']['all_cleanup']=='PASS'
        assert [(r['pair'],r['mode']) for r in source['runs']]==[(1,'off'),(1,'on'),(2,'on'),(2,'off')]
        assert len({g for r in source['runs'] for g in r['guest_ids']})==4
        recorded=rows(directory/'metrics.tsv');assert len(recorded)==4
        runs=[]
        for original,saved in zip(source['runs'],recorded):
            index=original['index'];mode=original['mode'];sampling=original['sampling'];prefix=f'run-{index}-{mode}'
            assert original['status']=='PASS' and original['owned_resources_removed'] and original['cleanup']=='PASS'
            assert (int(saved['index']),saved['mode'])==(index,mode)
            for key in TIMINGS:close(float(saved[key]),original[key])
            tools=rows(directory/(prefix+'-tools.tsv'));assert len(tools)==actions==original['tool_count']
            close(sum(int(t['duration_ns']) for t in tools)/1e9,original['tool_seconds'])
            assert original['input_files']==files and original['ownership']=='NOT_QUERIED'
            shortfall=max(0,original['input_resident_bytes']-original['input_group_file_bytes']);assert shortfall==0
            assert original['registry_commands']==['version','manifest'] and sampling['pss']=='NOT_REQUESTED'
            row=dict(task_id=task_id,task=label,index=index,pair=original['pair'],mode=mode,actions=actions,
                **{k:original[k] for k in TIMINGS},query_cpu_seconds=original['query_cpu_seconds'],
                input_files=files,input_resident_mib=original['input_resident_bytes']/2**20,
                compared_group_file_mib=original['input_group_file_bytes']/2**20,input_shortfall_mib=shortfall/2**20,
                ownership=original['ownership'],host_samples=0,guest_samples=0,host_cpu_seconds=None,guest_cpu_seconds=None,
                host_cpu_percent=None,guest_cpu_percent=None,management_seconds=None,host_span_seconds=None,guest_span_seconds=None,
                host_max_gap_ms=None,guest_max_gap_ms=None,host_current_before_guest_mib=None,host_current_peak_mib=None,
                host_current_peak_increase_mib=None,host_file_peak_mib=None,guest_memory_peak_mib=None,guest_cached_peak_mib=None,
                boundary_memory_rows=sampling['boundary_rows']['guest-memory.tsv'],boundary_vmstat_rows=sampling['boundary_rows']['guest-vmstat.tsv'],
                source_summary=str((directory/'summary.json').relative_to(root)))
            if mode=='off':
                assert original['sample_count']==0 and original['collector_cpu_seconds'] is None
                assert original['memory_peak_mib'] is None and original['cached_peak_mib'] is None
                assert sampling['memory']=='NOT_MEASURED' and sampling['host_cpu_seconds'] is sampling['guest_cpu_seconds'] is None
                assert sampling['management_seconds'] is None and row['boundary_memory_rows']==row['boundary_vmstat_rows']==0
                assert not (directory/(prefix+'-guest-memory.tsv')).exists() and not (directory/(prefix+'-host-memory.tsv')).exists()
            else:
                guest=rows(directory/(prefix+'-guest-memory.tsv'));host=rows(directory/(prefix+'-host-memory.tsv'))
                assert len(guest)==original['sample_count'] and len(host)==sampling['host_samples']
                assert sampling['interval_seconds']==.1 and sampling['coverage']=='All five original segments and official evaluation observed'
                assert row['boundary_memory_rows']==row['boundary_vmstat_rows']==boundary
                assert len({r['sandbox_id'] for r in tools+guest})==1
                hspan=(int(host[-1]['monotonic_ns'])-int(host[0]['monotonic_ns']))/1e9
                hgap=max((int(b['monotonic_ns'])-int(a['monotonic_ns']))/1e9 for a,b in zip(host,host[1:]))
                ggap=max(float(b['elapsed_seconds'])-float(a['elapsed_seconds']) for a,b in zip(guest,guest[1:]))
                close(hspan,sampling['host_span_seconds']);close(hgap,sampling['host_max_gap_seconds']);close(ggap,original['max_sample_gap_seconds'])
                hcpu=100*sampling['host_cpu_seconds']/hspan;gcpu=100*sampling['guest_cpu_seconds']/sampling['guest_span_seconds']
                close(hcpu,sampling['host_cpu_percent']);close(gcpu,sampling['guest_cpu_percent'])
                close(sampling['guest_cpu_seconds'],original['collector_cpu_seconds'])
                assert len(sampling['management_events'])==5 and all(e['exit_code']==0 for e in sampling['management_events'])
                close(sum(e['duration_ns'] for e in sampling['management_events'])/1e9,sampling['management_seconds'])
                memory=original['host_memory'];peak=max(int(r['current']) for r in host);assert peak==memory['current_peak_bytes']
                gpeak=max(int(r['memory_used_bytes']) for r in guest)/2**20;cpeak=max(int(r['cached_bytes']) for r in guest)/2**20
                close(gpeak,original['memory_peak_mib']);close(cpeak,original['cached_peak_mib'])
                row.update(host_samples=len(host),guest_samples=len(guest),host_cpu_seconds=sampling['host_cpu_seconds'],guest_cpu_seconds=sampling['guest_cpu_seconds'],
                    host_cpu_percent=hcpu,guest_cpu_percent=gcpu,management_seconds=sampling['management_seconds'],host_span_seconds=hspan,guest_span_seconds=sampling['guest_span_seconds'],
                    host_max_gap_ms=hgap*1000,guest_max_gap_ms=ggap*1000,host_current_before_guest_mib=memory['current_baseline_bytes']/2**20,
                    host_current_peak_mib=peak/2**20,host_current_peak_increase_mib=memory['current_peak_increase_bytes']/2**20,
                    host_file_peak_mib=max(int(r['file']) for r in host)/2**20,guest_memory_peak_mib=gpeak,guest_cached_peak_mib=cpeak)
            if name=='prettier':
                assert original['original_tests']=={'passed':0,'failed':2}
            else:
                assert original['strict_replay_status']=='FAIL' and original['diagnostic_status']=='PASS'
                if name=='sphinx':
                    assert original['pipeline_policy_version']==2 and original['replay_exit_differences']==[] and original['observed_exit_codes_status']=='PASS'
                    assert original['original_tests']=={'passed':24,'failed':1,'skipped':0}
                else:
                    assert original['original_tests']=={'passed':945,'failed':12,'skipped':0} and original['observed_exit_codes_status']=='FAIL'
                    d=original['replay_exit_differences'];assert len(d)==1 and (d[0]['segment'],d[0]['step'],d[0]['expected'],d[0]['actual'])==('trace_index_state','002',0,141)
            runs.append(row)
        pairs=[]
        for number in (1,2):
            off=next(r for r in runs if r['pair']==number and r['mode']=='off');on=next(r for r in runs if r['pair']==number and r['mode']=='on')
            recorded_pair=next(p for p in source['pairs'] if p['pair']==number)
            order=['off','on'] if number==1 else ['on','off'];assert recorded_pair['order']==order
            pair=dict(task_id=task_id,task=label,pair=number,order=order,metrics={})
            for key in TIMINGS:
                metric=dict(off=off[key],on=on[key],on_minus_off=on[key]-off[key],change_percent=100*(on[key]/off[key]-1))
                for field,value in metric.items():close(value,recorded_pair['metrics'][key][field])
                pair['metrics'][key]=metric
            pairs.append(pair)
        curves={str(i):{kind:str((directory/f'run-{i}-on-{kind}.png').relative_to(root)) for kind in ('guest','host','host-increase')} for i in (2,3)}
        for curve in [directory/'timings.png',*[root/p for c in curves.values() for p in c.values()]]:assert curve.is_file()
        tasks.append(dict(task_id=task_id,label=label,source_summary=str((directory/'summary.json').relative_to(root)),
            source_report=str((directory/'REPORT.md').relative_to(root)),timings_curve=str((directory/'timings.png').relative_to(root)),
            runs=runs,pairs=pairs,correctness=source['correctness'],curves=curves))
    return tasks


def report(tasks,output,root=ROOT):
    link=lambda path:os.path.relpath(root/path,output)
    signed=lambda m:f"{m['on_minus_off']:+.3f}秒（{m['change_percent']:+.2f}%）"
    on=[r for t in tasks for r in t['runs'] if r['mode']=='on']
    text='# 三任务本地无代理：轻量采样配对汇总\n\n'
    text+='Prettier、Sphinx、Xarray各两对四次，共12次新单沙箱／396个原动作。每个任务固定关→开、开→关，每次新统计组、导入输入和私有服务，4 GiB／2 vCPU guest、默认回收。只汇总这三批已完成结果，不插入原单轮或旧版本对照。\n\n'
    text+=f"六次开启的直接CPU约占一个核：host {min(r['host_cpu_percent'] for r in on):.3f}%～{max(r['host_cpu_percent'] for r in on):.3f}%，guest {min(r['guest_cpu_percent'] for r in on):.3f}%～{max(r['guest_cpu_percent'] for r in on):.3f}%。任务用时另按下表逐对使用；直接CPU不含Shell边界操作，不能替代整套开关影响。\n\n## 先看各任务的两对用时\n\n"
    text+='| 任务 | 顺序 | 工具关闭／开启（秒） | 工具变化 | 控制程序关闭／开启（秒） | 整轮变化 |\n| --- | --- | ---: | ---: | ---: | ---: |\n'
    for t in tasks:
        for p in t['pairs']:
            a=p['metrics']['tool_seconds'];b=p['metrics']['controller_seconds']
            text+=f"| {t['label']} | {'关→开' if p['pair']==1 else '开→关'} | {a['off']:.3f}／{a['on']:.3f} | {signed(a)} | {b['off']:.3f}／{b['on']:.3f} | {signed(b)} |\n"
    text+='\nPrettier与Sphinx两对工具变化均为正；Xarray一负一正。全部运行和慢值保留，两对结果按任务分别展示，不合算一个跨任务减速率，也不把负差解释为采样固定加速。冻结重放、创建和评测的逐对差值在[pairs.tsv](pairs.tsv)，逐轮完整数据在[runs.tsv](runs.tsv)。\n\n## 计时窗口：包含关系一致，数值不要相加\n\n'
    text+='- 工具累计：原逐条命令在guest中的执行窗口之和。\n- 冻结重放：原动作路径，还含分派和结果传回。\n- 控制程序整轮：创建、准备、重放、额外检查／评测、采样收尾及guest删除。创建和额外评测已计入其中。\n- 统计组流程：再含输入导入、私有卷／服务、最终查询及报告等。导入和查询已计入其中。\n- 组外备份准备：归档恢复与来源完整性处理，单列，不叫任务时间。\n\n'
    text+='| 任务／轮次 | 工具 | 重放 | 控制程序 | 创建 | 额外评测 | 组内流程 | 组外准备 | 导入 | 最终查询 |\n| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |\n'
    for t in tasks:
        for r in t['runs']:
            text+=f"| {t['label']}／{r['index']}{'开' if r['mode']=='on' else '关'} | "+' | '.join(f'{r[k]:.3f}' for k in TIMINGS)+' |\n'
    text+='\n单位均为秒。控制程序减去创建仍包含准备、重放、评测和收尾，不当纯采样成本。任务镜像、动作和评测集合不同，不作任务间性能排名。\n\n## 开启组直接成本与覆盖\n\n'
    text+='| 任务／轮次 | host／guest点数 | host CPU秒（单核%） | guest CPU秒（单核%） | 五次同步调用／秒 | host／guest最大间隔／毫秒 |\n| --- | ---: | ---: | ---: | ---: | ---: |\n'
    for r in on:
        text+=f"| {r['task']}／{r['index']} | {r['host_samples']}／{r['guest_samples']} | {r['host_cpu_seconds']:.6f}（{r['host_cpu_percent']:.3f}%） | {r['guest_cpu_seconds']:.6f}（{r['guest_cpu_percent']:.3f}%） | {r['management_seconds']:.6f} | {r['host_max_gap_ms']:.3f}／{r['guest_max_gap_ms']:.3f} |\n"
    text+='\n开启目标间隔0.1秒，原五段及额外评测的覆盖沿用保存的验收。CPU比例为100×直接CPU秒数÷各自窗口秒数，两端百分比不相加。五次同步调用是上传、就绪、阶段标记、停止和下载；后台采集与任务重叠的存活时间不当额外等待。\n\n本次开关同时切换周期采样、边界读取及guest采样阶段标记。Prettier每次开启68条内存和68条vmstat边界记录；Sphinx／Xarray各80条＋80条。这些Shell操作不计入采样线程／进程CPU。PSS不请求；关闭组不运行采样，内存及采样CPU是未测，不填零。TSV中空字段表示未测，关闭点数0仅表示确实没有采集点。\n\n## 内存量与图入口：两端不是同一层\n\n'
    text+='host主指标是整组memory.current，含新导入输入、控制程序、服务及guest的组内记账；仍漏组外记账和未记账分配。file含文件缓存和共享内存（Shmem），是组成量，不是guest Cached的物理副本。guest内存为MemTotal−MemAvailable，Cached含Shmem，均为guest全局逻辑量，不与host相加。\n\n'
    text+='| 任务／开启轮次 | host创建前基线 | host current峰 | 创建后峰值增量 | host file峰 | guest内存峰 | guest Cached峰 |\n| --- | ---: | ---: | ---: | ---: | ---: | ---: |\n'
    for r in on:
        text+=f"| {r['task']}／{r['index']} | "+' | '.join(f'{r[k]:.3f}' for k in ('host_current_before_guest_mib','host_current_peak_mib','host_current_peak_increase_mib','host_file_peak_mib','guest_memory_peak_mib','guest_cached_peak_mib'))+' |\n'
    text+='\n单位均为MiB。基线已含输入和服务，增量仍含后续控制／服务活动，不是guest独占量。增量沿用原创建后窗口定义，不用全流程任意峰值重新定义；所有峰值都是采样观测峰值。关闭组没有曲线，默认回收下开启组曲线可波动，不为具体下降补造回收原因。\n\n| 任务 | 用时图与报告 | 第2轮开启曲线 | 第3轮开启曲线 |\n| --- | --- | --- | --- |\n'
    for t in tasks:
        text+=f"| {t['label']} | [用时]({link(t['timings_curve'])}) · [原报告]({link(t['source_report'])}) | "+' | '.join(' · '.join(f'[{label}]({link(t["curves"][str(i)][key])})' for label,key in [('guest','guest'),('host','host'),('增量','host-increase')]) for i in (2,3))+' |\n'
    text+='\n以上21张图直接链接results中的既有成图，不重复复制或重新选轮次。\n\n## 最终驻留查询两组都保留\n\n'
    text+='| 任务 | 每次唯一输入文件 | 驻留／MiB | 核对组file范围／MiB | 四次查询墙钟范围／秒 | 查询CPU范围／秒 |\n| --- | ---: | ---: | ---: | ---: | ---: |\n'
    for t in tasks:
        rr=t['runs'];resident={r['input_resident_mib'] for r in rr};assert len(resident)==1
        text+=f"| {t['label']} | {rr[0]['input_files']} | {next(iter(resident)):.3f} | {min(r['compared_group_file_mib'] for r in rr):.3f}～{max(r['compared_group_file_mib'] for r in rr):.3f} | {min(r['query_seconds'] for r in rr):.3f}～{max(r['query_seconds'] for r in rr):.3f} | {min(r['query_cpu_seconds'] for r in rr):.3f}～{max(r['query_cpu_seconds'] for r in rr):.3f} |\n"
    text+='\n每轮均在隔离运行及自有资源清理后、输入释放前，对导入文件做一次mincore驻留查询，只问文件页是否在宿主缓存中，按设备号／inode去重，不读取数据页。核对组file沿用原收据的查询前后较大者；容量不足均0，逐页归属仍NOT_QUERIED。它不代表运行中的完整缓存，也不证明每页都记在组内，不加入current或用于计算共享率。\n\n## 原任务状态分别保留\n\n'
    text+='- Prettier：每轮29步，缺rg的原127保持；独立检查和最终32组／66项通过，额外原1组／2项失败签名保持。\n- Sphinx：每轮35步、七例独立检查、原末步25通过，额外24通过／1失败；pipeline-v2原范围保持，四次获准退出差异0项。诊断PASS、历史严格FAIL。\n- Xarray：每轮35步、负向及修复后检查，末两次3通过1失败及129通过1失败，额外945通过／12失败名单保持。四次都只有trace_index_state/002原0→141的获准差异，满足原命令及955字节输出条件；实际退出完全匹配FAIL、诊断PASS、历史严格FAIL分别记录，未扩大政策。\n\n服务／guest代理检查、实际regctl仅version／manifest、原存储边界和自有清理复用各批有效证据，不在汇总时重做。没有17891回退或镜像／依赖下载日志，默认DNS例外保留，运行请求量未测。\n\n## 离线复算\n\n```bash\nbash scripts/summarize-local-no-proxy-pairs.sh --output .artifacts/local-no-proxy-pairs-recheck\n```\n\n只读三个results目录中的JSON／TSV和图路径，复算工具总时延、六对差值、点数、峰值、间隔、CPU比例及管理调用和，再生成本表。输出目录必须不存在；无需镜像、服务、原始运行目录或网络。[完整摘要与来源](summary.json) · [逐轮指标](runs.tsv) · [逐对指标](pairs.tsv)。\n\n现有单沙箱批次已齐，停止追加。若后续要回答并发沙箱的采样成本，先讨论任务、调度和规模，再安排新对照。\n'
    return text


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,default=ROOT/'results/local-no-proxy-paired-summary-20261101')
    args=parser.parse_args();tasks=collect();out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    run_rows=[r for t in tasks for r in t['runs']];pair_rows=[]
    for t in tasks:
        for p in t['pairs']:
            row=dict(task_id=p['task_id'],task=p['task'],pair=p['pair'],order=' -> '.join(p['order']))
            for metric,values in p['metrics'].items():
                for key,value in values.items():row[metric+'_'+key]=value
            pair_rows.append(row)
    for name,selected in [('runs.tsv',run_rows),('pairs.tsv',pair_rows)]:
        with (out/name).open('w') as f:
            writer=csv.DictWriter(f,fieldnames=list(selected[0]),delimiter='\t');writer.writeheader();writer.writerows(selected)
    result=dict(date='2026-11-01',status='PASS',scope='Offline summary of fixed per-task on/off pairs; public results only; no live work',
        runs=12,pairs=6,actions=sum(r['actions'] for r in run_rows),guest_memory_mib=4096,guest_vcpu=2,default_reclaim=True,pss='NOT_REQUESTED',
        host_samples=sum(r['host_samples'] for r in run_rows),guest_samples=sum(r['guest_samples'] for r in run_rows),source_path_base='repository root',tasks=tasks,
        outside_group_charges='NOT_COVERED',input_page_ownership='NOT_QUERIED',runtime_file_physical_union='NOT_MEASURED',sharing_savings='NOT_MEASURED',cross_task_slowdown='NOT_COMBINED')
    (out/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');(out/'REPORT.md').write_text(report(tasks,out))
    print(f"status=PASS result_dir={out} runs=12 pairs=6 actions={result['actions']} host_points={result['host_samples']} guest_points={result['guest_samples']}")


if __name__=='__main__':main()
