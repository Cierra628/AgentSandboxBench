"""Summarize the three fixed 2026-10-31 saved runs; read-only, standard library only."""
import argparse
import csv
import json
import math
import os
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
TASKS=(('prettier','prettier__prettier-6604',29,'Prettier #6604'),
       ('sphinx','sphinx-doc__sphinx-7590',35,'Sphinx #7590'),
       ('xarray','pydata__xarray-6992',35,'Xarray #6992'))


def rows(path):
    with path.open() as stream:return list(csv.DictReader(stream,delimiter='\t'))


def collect(root):
    selected=[]
    for name,task_id,count,label in TASKS:
        directory=root/'results'/(name+'-local-no-proxy-20261031')
        source=json.loads((directory/'summary.json').read_text())
        assert source['status']=='PASS' and source['task_id']==task_id
        timing=source['timing_seconds'];memory=source['memory_bytes'];sampling=source['sampling'];query=source['final_input_residency'];correctness=source['correctness']
        assert correctness['original_actions']==count and correctness['cleanup']=='PASS'
        assert sampling['interval_seconds']==.1 and sampling['pss']=='NOT_REQUESTED' and sampling['slowdown_control']=='NOT_RUN_THIS_ROUND'
        assert 'local-no-proxy' in source['mode'] and 'default reclaim' in source['mode']
        tools=rows(directory/'tools.tsv');guest=rows(directory/'guest-memory.tsv');host=rows(directory/'host-memory.tsv')
        assert len(tools)==count and len(guest)==sampling['guest_samples'] and len(host)==sampling['host_samples']
        assert len({r['sandbox_id'] for r in tools+guest})==1
        tool_field='original_29_tool_sum' if name=='prettier' else 'original_35_tool_sum'
        tool_seconds=sum(int(r['duration_ns']) for r in tools)/1e9
        assert tool_seconds==timing[tool_field]
        assert max(int(r['memory_used_bytes']) for r in guest)==memory['guest_memory_peak']
        assert max(int(r['cached_bytes']) for r in guest)==memory['guest_cached_peak']
        assert max(int(r['current']) for r in host)==memory['host_current_peak']
        assert max(int(r['file']) for r in host)==memory['host_file_peak']
        # In these three fixed runs the global and after-creation peaks coincide.
        assert memory['host_current_peak']-memory['host_current_before_guest']==memory['host_peak_increase_from_before_guest']
        host_span=(int(host[-1]['monotonic_ns'])-int(host[0]['monotonic_ns']))/1e9
        host_gap=max((int(b['monotonic_ns'])-int(a['monotonic_ns']))/1e9 for a,b in zip(host,host[1:]))
        guest_gap=max(float(b['elapsed_seconds'])-float(a['elapsed_seconds']) for a,b in zip(guest,guest[1:]))
        assert math.isclose(host_span,sampling['host_sample_window_seconds'],abs_tol=1e-8)
        assert math.isclose(host_gap,sampling['host_maximum_sample_gap_seconds'],abs_tol=1e-8)
        if 'guest_maximum_sample_gap_seconds' in sampling:
            assert math.isclose(guest_gap,sampling['guest_maximum_sample_gap_seconds'],abs_tol=1e-8)
        host_cpu=100*sampling['host_direct_thread_cpu_seconds']/host_span
        guest_cpu=100*sampling['guest_direct_process_cpu_seconds']/sampling['guest_collector_elapsed_seconds']
        assert math.isclose(host_cpu,sampling['host_single_cpu_percent']) and math.isclose(guest_cpu,sampling['guest_single_cpu_percent'])
        assert sampling['blocking_management_calls']==len(sampling['blocking_management_events'])==5
        assert math.isclose(sum(r['seconds'] for r in sampling['blocking_management_events']),sampling['blocking_management_total_seconds'])
        if name=='prettier':
            compared_file=query['group_file_bytes'];file_source='final_input_residency/group_file_bytes (saved after-query reading)'
            assert correctness['official_task_status']=='FAIL' and correctness['official_failure_matches_reference']
            official_label='1组／2项失败，原签名保持'
            assert '1组／2项失败' in (directory/'REPORT.md').read_text()
        else:
            compared_file=max(query['group_file_before_bytes'],query['group_file_after_bytes'])
            file_source='max(final_input_residency/group_file_before_bytes, group_file_after_bytes)'
            official=correctness['official'];assert official['status']=='FAIL'
            assert correctness['diagnostic_status']=='PASS' and correctness['strict_replay_status']=='FAIL'
            official_label=f"{official['passed']}通过／{official['failed']}失败／{official['skipped']}跳过"
        assert query['status']=='PASS' and query['ownership']=='NOT_QUERIED'
        assert max(0,query['resident_bytes']-compared_file)==query['shortfall_bytes']==0
        assert source['proxy']['guest_check']=='PASS' and source['proxy']['registry_commands']==['version','manifest']
        row=dict(task_id=task_id,label=label,actions=count,source_summary=str((directory/'summary.json').relative_to(root)),
            source_report=str((directory/'REPORT.md').relative_to(root)),official_result=official_label,
            tool_seconds=tool_seconds,**{k+'_seconds':timing[k] for k in ('frozen_replay','task_controller','grouped_workflow','archive_preparation','input_import','guest_creation')},
            **{k+'_mib':v/2**20 for k,v in memory.items()},
            host_samples=len(host),guest_samples=len(guest),host_span_seconds=host_span,guest_span_seconds=sampling['guest_collector_elapsed_seconds'],
            host_cpu_seconds=sampling['host_direct_thread_cpu_seconds'],guest_cpu_seconds=sampling['guest_direct_process_cpu_seconds'],
            host_cpu_percent=host_cpu,guest_cpu_percent=guest_cpu,host_max_gap_ms=host_gap*1000,guest_max_gap_ms=guest_gap*1000,
            guest_sampler_maxrss_kib=sampling['guest_collector_maxrss_kib'],management_seconds=sampling['blocking_management_total_seconds'],
            input_files=query['files'],input_resident_mib=query['resident_bytes']/2**20,compared_group_file_mib=compared_file/2**20,
            input_shortfall_mib=query['shortfall_bytes']/2**20,query_seconds=query['query_elapsed_seconds'],query_cpu_seconds=query['query_thread_cpu_seconds'])
        selected.append(dict(metrics=row,correctness=correctness,
            source_fields=dict(tool_seconds='timing_seconds/'+tool_field,compared_group_file_mib=file_source,
                guest_max_gap_ms='max adjacent elapsed_seconds differences in published guest-memory.tsv, multiplied by 1000'),
            curves={k:str((directory/(k+'.png')).relative_to(root)) for k in ('guest','host','host-increase')}))
    return selected


def report(data,output):
    def link(path):return os.path.relpath(ROOT/path,output)
    def table(fields):
        body='| 指标 | '+ ' | '.join(item['metrics']['label'] for item in data)+' |\n| --- | ---: | ---: | ---: |\n'
        for label,key in fields:
            values=[item['metrics'][key] for item in data]
            body+='| '+label+' | '+' | '.join(f'{v:.3f}' if isinstance(v,float) else str(v) for v in values)+' |\n'
        return body
    text='# 三任务本地无代理轻量测量汇总\n\n2026-10-31的三次完整测量，各一轮新4 GiB／2 vCPU单沙箱，默认回收、0.1秒轻量采样、持久化输入。这里统一现有结果的指标和图入口，没有重新运行任务或查询驻留。\n\n## 先选要看的曲线\n\n| 任务 | 工具时延与guest内存／Cached | host总记账及分项 | host相对创建前基线 | 原报告与数据 |\n| --- | --- | --- | --- | --- |\n'
    for item in data:
        r=item['metrics'];curves=item['curves']
        text+=f"| {r['label']} | [guest]({link(curves['guest'])}) | [host]({link(curves['host'])}) | [增量]({link(curves['host-increase'])}) | [报告]({link(r['source_report'])}) · [摘要]({link(r['source_summary'])}) |\n"
    text+='\n## 用时：同名窗口一致，但彼此包含\n\n'
    text+=table([('原工具次数','actions'),('工具累计／秒','tool_seconds'),('冻结重放／秒','frozen_replay_seconds'),('任务控制程序整轮／秒','task_controller_seconds'),('统计组完整流程／秒','grouped_workflow_seconds'),('其中：组内输入导入／秒','input_import_seconds'),('组外备份恢复准备／秒','archive_preparation_seconds')])
    text+='\n工具累计来自逐条命令的guest执行时间；冻结重放还包含分派和结果传回。任务控制程序包含创建、准备、任务、额外检查／评测、采样收尾与guest删除；统计组流程再包含输入导入、私有卷／服务、最终查询和报告等。导入已计入组流程，评测已计入控制程序，不重复相加。组外恢复另列，也不能把它与组流程之和改称纯任务耗时。\n\n## 内存：同时保留整组基线与运行增量\n\n'
    text+=table([('创建前host current基线／MiB','host_current_before_guest_mib'),('全流程host current观测峰值／MiB','host_current_peak_mib'),('创建后峰值相对基线增量／MiB','host_peak_increase_from_before_guest_mib'),('host file观测峰值／MiB','host_file_peak_mib'),('guest内存观测峰值／MiB','guest_memory_peak_mib'),('guest Cached观测峰值／MiB','guest_cached_peak_mib')])
    text+='\nhost主指标是统计组及后代的`memory.current`，包含新导入输入、控制程序、私有服务和guest的组内记账。创建前基线已有输入和服务；峰值增量仍包含后续服务／控制活动，不是任务进程独占量。三任务准备的文件集合不同，这些行展示各自成本，不作共享收益或部署密度比较。current仍遗漏组外记账及未记账分配。\n\nguest内存是`MemTotal−MemAvailable`，Cached包含Shmem；它们是guest全局逻辑量，与host current／file不是同一层，不相加。所有峰值均是采样观测峰值，图尾负增量可能来自清理。没有为具体Cached下降重新采集回收事件。\n\n## 采样直接CPU很小，任务减速率本批未测\n\n'
    text+=table([('host点数','host_samples'),('guest点数','guest_samples'),('host首点至末点／秒','host_span_seconds'),('guest采集进程存活窗口／秒','guest_span_seconds'),('host线程CPU／秒','host_cpu_seconds'),('guest采集进程CPU／秒','guest_cpu_seconds'),('host占一个CPU核／%','host_cpu_percent'),('guest占一个CPU核／%','guest_cpu_percent'),('五次同步采样管理调用／秒','management_seconds'),('host最大采样间隔／毫秒','host_max_gap_ms'),('guest最大采样间隔／毫秒','guest_max_gap_ms'),('guest采样器最大RSS／KiB','guest_sampler_maxrss_kib')])
    text+='\nCPU比例按各自窗口计算：`100×采样CPU秒数÷对应窗口秒数`，不能把两端百分比相加。五次同步调用是上传、就绪、阶段标记、停止和下载；后台采集进程与任务重叠的存活时间不是阻塞时间。最大RSS是该采集进程的观测值，不是独占内存增量。guest最大间隔统一从保存的逐点表的相邻elapsed_seconds复算；Prettier该项由本次汇总从原点表补算。\n\n三次直接CPU约占单核host 0.297%～0.303%、guest 0.478%～0.776%；这些CPU只计host采样线程和guest采集进程，不包含任务runner的Shell边界读取和guest采样阶段标记。当前Prettier另有68条内存和68条vmstat边界读数，拟对照覆盖这整套采样链。三项均没有本批关闭采样配对，不能据此给出任务减速率，也不把旧guest-only、GRPO／BPO、旧代理或旧输入的数据混入。\n\n## 驻留核对只覆盖结束后的导入输入\n\n'
    text+=table([('输入唯一文件数','input_files'),('mincore驻留／MiB','input_resident_mib'),('核对用组file／MiB','compared_group_file_mib'),('驻留超过组file的容量／MiB','input_shortfall_mib'),('查询墙钟／秒','query_seconds'),('查询线程CPU／秒','query_cpu_seconds')])
    text+='\n三次都是隔离运行及自有运行资源清理结束后、输入删除前的一次mincore，只问已有文件页是否驻留，不读取数据页。路径按设备号和inode去重；比较值取查询前后组file较大者，本批三次前后读数相同。容量不足为`max(0, 驻留−比较值)`，三次为0；逐页归属仍是`NOT_QUERIED`。它既不是运行中完整文件集合，也不证明每一页都记在组内，不加到current上。\n\n## 功能、原业务结果和网络限制分开保留\n\n'
    text+='| 任务 | 原动作／补丁及独立检查 | 额外官方评测结果 |\n| --- | --- | --- |\n'
    for item in data:text+=f"| {item['metrics']['label']} | 按各自原政策通过 | {item['metrics']['official_result']} |\n"
    text+='\nSphinx和Xarray的诊断PASS与历史严格重放FAIL分别保留；官方失败未改写为任务成功。两者的退出政策和独立检查不同，不合并测试通过率。三项采集与清理均已在各轮验收，本表不重做这些动作。\n\n三次服务／guest代理检查通过，实际regctl均只有version、manifest，无17891回退。`allowInternetAccess=false`仍保留默认DNS例外，网络请求量未测。\n\n## 离线复算\n\n```bash\nbash scripts/summarize-local-no-proxy.sh --output .artifacts/local-no-proxy-summary-recheck\n```\n\n只读results中三个结果目录的摘要和TSV，复核工具总时延、样本数、内存峰值、采样间隔、CPU比例及管理调用和，再输出这份表。输出目录必须不存在，不覆盖原结果；不依赖镜像、运行中服务或网络。[机器可读指标](metrics.tsv)和[完整汇总及来源字段](summary.json)在同目录。\n\n本阶段完成。若下一步需要量化当前无代理方案的任务减速，先离线确定同任务、同输入和同准备状态的开关对照范围；已有开关结果只按原版本引用，不自动启动或追加轮次。\n'
    return text


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'results/local-no-proxy-three-tasks-20261031')
    args=parser.parse_args();data=collect(ROOT);out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    fields=list(data[0]['metrics'])
    assert all(list(item['metrics'])==fields for item in data)
    with (out/'metrics.tsv').open('w') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields,delimiter='\t');writer.writeheader();writer.writerows(item['metrics'] for item in data)
    value=dict(date='2026-10-31',status='PASS',scope='Three separate single-guest runs; fixed inputs; default reclaim; light 0.1 s; local-no-proxy; no new live measurement',
        guest_memory_mib=4096,guest_vcpu=2,runs_per_task=1,source_path_base='repository root',tasks=data,slowdown='NOT_MEASURED_THIS_BATCH',sharing_savings='NOT_MEASURED_THIS_BATCH')
    (out/'summary.json').write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');(out/'REPORT.md').write_text(report(data,out))
    print('status=PASS result_dir='+str(out))


if __name__=='__main__':main()
