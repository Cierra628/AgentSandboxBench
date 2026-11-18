"""Approved Django original-path prefix reuse: three schedules, three repeats."""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import traceback
import urllib.request

from django_schedule_inputs import ROOT,EXPECTED,task_plan,volume_gib
from isolated_storage import GIB
from prettier_no_proxy import clean_environment
from prepare_python_schedule_matrix import ORDERS
from report_python_schedule_matrix import collect,plot_run,repeat_statistics,REPEAT_METRICS
from run_python_schedule_matrix import capacity_after_cleanup,continuation
from run_light_monitor_controls import SOURCE,SOURCE_ID


def read(path):return json.loads(Path(path).read_text())
def write(path,value):Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def planned_runs(profile_name, rounds=3, schedules=None):
    if rounds not in (1,2,3):raise ValueError('rounds must be 1, 2 or 3')
    if schedules is not None and (not schedules or len(set(schedules))!=len(schedules) or not set(schedules)<=set(ORDERS[0])):
        raise ValueError('schedules must be distinct known schedules')
    return [dict(task=profile_name,round=n,schedule=s,status='NOT_RUN')
            for n,order in enumerate(ORDERS[:rounds],1) for s in order if schedules is None or s in schedules]


def accepted_measurement(row):
    return row['status']=='PASS' or (row.get('task')=='matplotlib24177' and
        row['status']=='ACCEPTED_SEGMENTS_V3' and row.get('original_status')=='FAIL' and
        row.get('experiment_scope_validation')=='PASS') or (row.get('task')=='django16819' and
        row.get('round')==1 and row.get('schedule')=='grpo' and
        row['status']=='ACCEPTED_SEGMENTS_V3' and row.get('original_status')=='FAIL' and
        row.get('experiment_scope_validation')=='PASS' and row.get('closure_policy')=='segments_v3') or (row.get('task')=='c4' and
        row['status']=='ACCEPTED_CLEANUP_TIMEOUT' and row.get('original_status')=='FAIL')


def resume_runs(proposed,previous,*,retry_failed=False,accept_c4_cleanup_timeout=False):
    prior=dict(previous,runs=[dict(r,status='PASS') if accepted_measurement(r) else dict(r) for r in previous['runs']])
    completed,pending,failed=continuation(proposed,prior,retry_failed=retry_failed or accept_c4_cleanup_timeout)
    old={(r['task'],r['round'],r['schedule']):r for r in previous['runs']}
    completed=[dict(old[(r['task'],r['round'],r['schedule'])]) for r in completed]
    if accept_c4_cleanup_timeout:
        assert proposed['task']['profile']=='c4'
        candidates=[r for r in previous['runs'] if r['status']=='FAIL']
        assert len(candidates)==1
        candidate=candidates[0]
        assert (candidate['task'],candidate['round'],candidate['schedule'])==('c4',1,'grpo')
        row=collect(Path(candidate['storage']),dict(task='c4',round=1,schedule='grpo'),accept_c4_cleanup_timeout=True)
        row['reused_from']=str(candidate['raw']);completed.append(row)
        pending=[r for r in pending if (r['task'],r['round'],r['schedule'])!=('c4',1,'grpo')]
    return completed,pending,failed


def reuse_v3_prefix(proposed, source):
    """Retain the approved first Matplotlib slot; never rerun or relabel its raw FAIL."""
    assert proposed['task']['profile']=='matplotlib24177'
    assert proposed['runs']==planned_runs('matplotlib24177')
    saved=read(source)
    assert saved['policy']=='segments_v3'
    candidates=[r for r in saved['runs'] if (r['task'],r['round'],r['schedule'])==('matplotlib24177',1,'grpo')]
    assert len(candidates)==1
    previous=candidates[0];checked=previous['recomputed']
    assert checked['experiment_scope_validation']=='PASS'
    assert read(Path(previous['source_summary']))['metadata']==proposed['task']
    storage=Path(previous['source_storage'])
    row=collect(storage,dict(task='matplotlib24177',round=1,schedule='grpo'),experiment_revalidation=source)
    row.update(checked,reused_from=str(previous['source_summary']))
    assert accepted_measurement(row)
    pending=[dict(r) for r in proposed['runs'] if (r['round'],r['schedule'])!=(1,'grpo')]
    return [row],pending


def reuse_django_first_round(proposed, source, revalidation):
    """Reuse exactly the three approved r1 measurements, preserving raw verdicts."""
    assert proposed['task']['profile']=='django16819'
    assert proposed['runs']==planned_runs('django16819')
    saved=read(source);evidence=read(revalidation)
    assert saved['metadata']==proposed['task'] and evidence['policy']=='segments_v3'
    assert len(saved['runs'])==3
    assert [(r['round'],r['schedule']) for r in saved['runs']]==[(1,s) for s in ORDERS[0]]
    completed=[]
    for original in saved['runs']:
        assert original['task']=='django16819'
        storage=Path(original['storage'])
        matches=[r for r in evidence['runs'] if Path(r['source_storage']).resolve()==storage.resolve()]
        assert len(matches)==1
        item=matches[0];checked=item['recomputed']
        assert (item['task'],item['round'],item['schedule'])==('django16819',1,original['schedule'])
        assert checked['experiment_scope_validation']=='PASS' and checked['closure_policy']=='segments_v3'
        assert checked['experiment_parent']==original['experiment_parent']
        task=read(Path(original['raw'])/'result.json')
        assert task['metadata']==proposed['task'] and task['status']=='PASS'
        assert task['schedule']==original['schedule'] and all(v=='PASS' for v in task['checks'].values())
        assert original['status']==('FAIL' if original['schedule']=='grpo' else 'PASS')
        row=dict(original,**checked)
        row.update(status='ACCEPTED_SEGMENTS_V3' if original['schedule']=='grpo' else 'PASS',
            original_status=original['status'],original_error=original.get('error'),
            original_failed_stage='historical matrix experiment-memory-acceptance' if original['status']=='FAIL' else None,
            revalidation_summary=str(revalidation),reused_from=str(source),
            measurement_origin='existing_first_round_v3')
        assert accepted_measurement(row)
        completed.append(row)
    return completed,[dict(r) for r in proposed['runs'] if r['round'] in (2,3)]


def reuse_django_first_two_rounds(proposed, source, revalidation):
    """Retry only r3 after the approved stop failure; keep all six valid slots."""
    assert proposed['task']['profile']=='django16819' and proposed['runs']==planned_runs('django16819')
    saved=read(source)
    assert saved['metadata']==proposed['task'] and saved['experiment_memory_standard']
    assert saved['status']=='FAIL' and saved['source_returned_to_stopped']
    assert len(saved['runs'])==7
    assert [(r['round'],r['schedule']) for r in saved['runs']]==[(r['round'],r['schedule']) for r in proposed['runs'][:7]]
    first,_=reuse_django_first_round(proposed,Path(saved['reuse_django_first_round']),revalidation)
    for original,checked in zip(saved['runs'][:3],first):
        assert original['storage']==checked['storage'] and original['status']==checked['status']
    completed=[]
    for original in saved['runs'][:6]:
        assert accepted_measurement(original)
        assert original['experiment_scope_validation']=='PASS' and original['closure_policy']=='segments_v3'
        task=read(Path(original['raw'])/'result.json')
        assert task['status']=='PASS' and task['metadata']==proposed['task']
        assert task['schedule']==original['schedule'] and all(v=='PASS' for v in task['checks'].values())
        assert task['experiment_memory_context']['status']=='AVAILABLE'
        assert task['experiment_memory_context']['parent']==original['experiment_parent']
        row=dict(original,measurement_origin='existing_first_round_v3' if original['round']==1 else 'existing_second_round_pass',
            reused_from=str(source))
        completed.append(row)
    failure=saved['runs'][-1]
    assert (failure['round'],failure['schedule'],failure['status'])==(3,'tvcache','FAIL')
    assert 'monitor-stop' in str(read(Path(failure['raw'])/'result.json').get('cleanup_errors'))
    return completed,[dict(r) for r in proposed['runs'] if r['round']==3],[*saved.get('previous_failures',[]),dict(failure)]


def publish(state,output):
    if state.get('experiment_memory_standard'):
        from report_experiment_schedule import publish as publish_experiment
        return publish_experiment(state,output)
    output.mkdir(exist_ok=True,parents=True)
    statistical_rows=[dict(r,status='PASS',controller_seconds=None) if r['status']=='ACCEPTED_CLEANUP_TIMEOUT' else r for r in state['runs']]
    stats=repeat_statistics(statistical_rows)
    c4=state.get('metadata',{}).get('profile')=='c4' or any(r.get('task')=='c4' for r in state['runs'])
    matplotlib=state.get('metadata',{}).get('profile')=='matplotlib24177' or any(r.get('task')=='matplotlib24177' for r in state['runs'])
    text_editing=state.get('metadata',{}).get('profile')=='text_editing' or any(r.get('task')=='text_editing' for r in state['runs'])
    cython=state.get('metadata',{}).get('profile')=='cython' or any(r.get('task')=='cython' for r in state['runs'])
    layers=7 if cython else 8 if text_editing else 9 if c4 else 10
    lines=['# C4原36步：七沙箱前缀复用对照' if c4 else '# Django原28步：七沙箱前缀复用对照','',
        ('七个沙箱最终均完成相同原36步；第24／29步后保存复用，不是原生七分支。GRPO实际252／逻辑252动作；BPO与TVCache实际93／逻辑252动作，各2次保存、6次恢复。三轮按GRPO→BPO→TVCache、BPO→TVCache→GRPO、TVCache→GRPO→BPO轮换。' if c4 else '七个沙箱最终均完成相同原28步；这是明确指定第18／26步分叉的受控负载，不是原生七分支。GRPO实际196／逻辑196动作；BPO与TVCache实际64／逻辑196动作，各2次保存、6次恢复。三轮按GRPO→BPO→TVCache、BPO→TVCache→GRPO、TVCache→GRPO→BPO轮换。'),'',
        '状态：'+state['status']+'；有效'+str(sum(accepted_measurement(r) for r in state['runs']))+'／9。', '',
        '每guest 2 vCPU、8 GiB配置；普通计数0.1秒、周期PSS1秒，默认回收。current仅私有服务及VMM后代，不含外层控制与输入校验；PSS/N仅七VMM比例驻留平均，非完整沙箱成本。', '',
        f'| 轮次／调度 | 状态 | 全窗口current峰值 MiB | 调度窗口current峰值 MiB | PSS/N MiB | 原{layers}层全部活跃文件P1 MiB | 物理文件数 |',
        '| --- | --- | ---: | ---: | ---: | ---: | ---: |']
    if matplotlib:
        lines[0]='# Matplotlib原95步：七沙箱前缀复用对照'
        lines[2]='七个沙箱最终完成相同原95步；第73／87步后保存复用，不是原生七分支。GRPO实际665／逻辑665动作；BPO与TVCache各实际185／逻辑665、2保存6恢复。七实例都实际执行原88／93步各60 passed及95步最终标记。三轮按GRPO→BPO→TVCache、BPO→TVCache→GRPO、TVCache→GRPO→BPO轮换。'
    if text_editing:
        lines[0]='# 大规模文本编辑原50步：七沙箱前缀复用对照'
        lines[2]='七实例完成相同原50步，33／46步保存，不是原生七分支。GRPO实际350／逻辑350动作；BPO／TVCache各实际113／逻辑350、2保存6恢复。七实例均实际执行47—50步完整文件比较及两种求解方式验证；三轮轮换顺序。'
    if cython:
        lines[0]='# Cython原83步：七沙箱前缀复用对照'
        lines[2]='七实例完成相同原83步，60／75步保存，不是原生七分支。GRPO实际581／逻辑581动作；BPO／TVCache各实际176／逻辑581、2保存6恢复。七实例均实际执行76—83步、两种目录各18 passed、NumPy2.3.0及编译扩展检查；三轮轮换顺序。'
    for r in state['runs']:
        if not accepted_measurement(r):
            lines += [f"| {r['round']}／{r['schedule']} | {r['status']} | — | — | — | — | — |"]
            continue
        values=' | '.join(f"{r[k]:.3f}" if r.get(k) is not None else '未知' for k in ('current_peak_mib','current_schedule_peak_mib','pss_per_guest_mib','lower_p1_mib'))
        lines += [f"| {r['round']}／{r['schedule']} | {r['status']} | {values} | {r['lower_physical_files']} |"]
    lines += ['', f'调度窗口从创建前到原路径及下载完成；全窗口另含终点查询、原结果检查与清理。文件P1按原{layers}层顺序／digest／大小确认，再按设备号:inode保留全部活跃副本，不预设恢复组文件数。', '',
        '| 轮次／调度 | 已枚举普通文件窗口合计 MiB／严格状态 | 有效对象数 | 调度窗口秒 | 工具累计秒 | 整轮秒 | 终点查询墙钟／CPU秒 |',
        '| --- | --- | ---: | ---: | ---: | ---: | --- |']
    for r in state['runs']:
        if not accepted_measurement(r):continue
        times=' | '.join(f"{r[k]:.3f}" for k in ('schedule_seconds','tools_seconds','controller_seconds'))
        value=f"{r['ordinary_window_mib']:.3f}" if r.get('ordinary_window_mib') is not None else '未知'
        lines += [f"| {r['round']}／{r['schedule']} | {value}／{r['ordinary_strict_status']} | {(r.get('ordinary_window') or {}).get('observed_objects','未知')} | {times} | {r['endpoint_wall_seconds']:.6f}／{r['endpoint_cpu_seconds']:.6f} |"]
    lines += ['', '普通文件窗口合计包含原P1、活跃upper及其他已枚举普通文件；各对象顺序查询，不是同一时刻物理并集或全部host page cache，不与P1再次相加。块设备cachestat、独立RAM及loop查询、严格状态和成本见summary.json；UNKNOWN／PARTIAL保留，不拼接跨层总量。', '',
        '## 重复统计', '', '| 调度 | 指标 | 有效次数 | 中位数 | 最小～最大 | 均值 | 样本标准差 |',
        '| --- | --- | ---: | ---: | --- | ---: | ---: |']
    if any(r['status']=='ACCEPTED_CLEANUP_TIMEOUT' for r in state['runs']):
        lines[4:4]=['用户已接受首轮GRPO任务／测量复用，标记ACCEPTED_CLEANUP_TIMEOUT；原始FAIL、清理超时和整轮原值保留。该组内存与任务／工具计时进入统计，含清理超时的控制程序整轮仅在逐次表展示，不进入正常整轮重复统计。全流程PASS次数另计：'+str(sum(r['status']=='PASS' for r in state['runs']))+'。','']
    for group in stats:
        for key,label,unit in REPEAT_METRICS:
            v=group['metrics'][key]
            if not v['count']:continue
            sd='—' if v['sample_stddev'] is None else f"{v['sample_stddev']:.3f}"
            lines += [f"| {group['schedule']} | {label}，{unit} | {v['count']} | {v['median']:.3f} | {v['minimum']:.3f}～{v['maximum']:.3f} | {v['mean']:.3f} | {sd} |"]
    lines += ['', '## 原检查与来源', '',
        ('原41／57／68／72步预期退出1，第78步129，其余0；83脚本与22辅助文件逐字保持。原Git／pip动态下载、独立Bash和原管道退出语义不变。七实例均实际执行76／82／83步原结果和独立编译扩展导入，最终12文件补丁对比原79步输出。保存恢复核对源码、修补脚本、编译产物元数据、进度和原进程；无官方隐藏判题。原59步安装0.8失败但管道退出0，不能算安装成功；60步0.6的最终版本未记录。' if cython else '第19／37步预期退出1，第43步预期退出127，其余为0。原50脚本及8份辅助文件保持，/app独立Bash，无额外预读或目录扫描；七实例均实际执行原47／50步完整结果及两种solve方式，最终cmp逐字节比较、脚本和权限检查通过。保存恢复只查询大CSV元数据、小脚本、进度及原进程连续性。没有官方隐藏判题。' if text_editing else '第29／42／47／48／63／78步预期退出1，第74步退出4，其余原动作退出0。保持95个原脚本和独立exec bash -lc、原随机数据及环境；原88／93步各60 passed、95步标记、导入和完整git diff HEAD --binary补丁逐实例验证，包含原暂存的备份文件。保存恢复核对源码／索引／备份／明确列出的辅助脚本、进度和原进程连续性；没有隐藏判题。' if matplotlib else '第3步预期退出2，第11／12／16／18步预期退出1，其余原动作预期0。原脚本和9份payload保持，各动作在/app独立Bash执行，不固定原随机测试数据。七实例均实际执行第33／34步9898文件往返／结构及分片约束检查；固定最终源码与语法检查必须通过。没有官方隐藏判题。' if c4 else '第17步预期退出1，其余原动作预期0；原脚本逐字保留，以持续Bash语义source执行，不新增pipefail。两次恢复点核对进度、源码补丁、备份文件状态及原进程连续性；七实例最终均须匹配固定原补丁并通过原py_compile检查。本轮不运行完整Django测试套件，不套用Prettier的0通过／2失败。'), '']
    for r in state['runs']:
        lines += [f"- r{r['round']} {r['schedule']}：`{r.get('raw',r.get('log'))}`。"]
        if r.get('curve'):lines += ['  [逐次曲线]('+os.path.relpath(r['curve'],output)+')。']
        if r.get('error'):lines += ['  错误：`'+r['error']+'`；首次实际失败停止，不自动重跑。']
    if state.get('error'):lines += ['', '本批停止原因：`'+state['error']+'`。实际失败保留；若仅在创建前被资源检查拦截，该未运行项不算任务失败。']
    for r in state.get('previous_failures',[]):
        reused=any(v['status']=='ACCEPTED_CLEANUP_TIMEOUT' and r.get('raw') and v.get('raw')==r['raw'] for v in state['runs'])
        label='此前失败（原FAIL保留；获准测量计入，异常整轮不入正常统计）' if reused else '此前失败（不计入统计）'
        lines += ['', label+'：`'+str(r.get('raw',r.get('log')))+'`；'+str(r.get('error'))+'。']
    complete=state['status'] in ('PASS','PASS_WITH_CLEANUP_EXCEPTION') and sum(accepted_measurement(r) for r in state['runs'])==9
    lines += ['', '## 下一步', '',
        '原九份记录已齐，重复统计已完成，不再追加本任务运行。' if complete else
        '只继续原九次清单的未完成项；首份正式结果兼作新路径验证。慢组全部保留，达到九份有效记录后汇总均值、中位数和波动，不自动追加轮次。']
    (output/'REPORT.md').write_text('\n'.join(lines)+'\n')
    write(output/'summary.json',dict(state,repeat_statistics=stats))


def main(profile_name='django16819'):
    c4=profile_name=='c4'
    matplotlib=profile_name=='matplotlib24177'
    text_editing=profile_name=='text_editing'
    cython=profile_name=='cython'
    if cython:
        from cython_schedule_inputs import task_plan,volume_gib,EXPECTED
    elif text_editing:
        from text_editing_schedule_inputs import task_plan,volume_gib,EXPECTED
    elif matplotlib:
        from matplotlib_schedule_inputs import task_plan,volume_gib,EXPECTED,START_MARGIN_BYTES
    elif c4:
        from c4_schedule_inputs import task_plan,volume_gib,EXPECTED
    else:
        from django_schedule_inputs import task_plan,volume_gib,EXPECTED
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs',required=True,type=Path)
    parser.add_argument('--upstream',required=True,type=Path)
    parser.add_argument('--oci-input',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--rounds',type=int,choices=(1,2,3),default=3,help='Explicit round count; default remains three')
    parser.add_argument('--experiment-memory-standard',action='store_true',help='Require dedicated slice measurements; preserve legacy Docker columns separately')
    parser.add_argument('--schedules',nargs='+',choices=('grpo','bpo','tvcache'),help='Explicit subset, preserving round rotation; default all three')
    parser.add_argument('--resume-from',type=Path)
    parser.add_argument('--reuse-v3-summary',type=Path,help='Explicit approved first Matplotlib GRPO reuse after v3 offline acceptance; raw FAIL retained')
    parser.add_argument('--reuse-django-first-round',type=Path,help='Explicit saved Django r1 summary; requires its approved --reuse-v3-summary and runs only rounds 2/3')
    parser.add_argument('--reuse-django-first-two-rounds',type=Path,help='Explicit stopped Django summary; retain six valid measurements and retry only r3')
    parser.add_argument('--retry-failed',action='store_true')
    parser.add_argument('--accept-c4-cleanup-timeout',action='store_true',help='Only after approval: reuse r1 GRPO measurements, retaining original cleanup FAIL')
    parser.add_argument('--guest-relay-proxy',help='Explicit approved Cython Git/pip loopback proxy; never inferred from environment')
    args=parser.parse_args();metadata=task_plan(args.inputs)
    if args.guest_relay_proxy:
        if not cython:parser.error('guest relay is Cython only')
        from external_guest_proxy import upstream_port
        upstream_port(dict(HTTP_PROXY=args.guest_relay_proxy))
    if args.accept_c4_cleanup_timeout and (not c4 or not args.resume_from):parser.error('C4 cleanup exception requires explicit C4 resume')
    if args.experiment_memory_standard and args.resume_from:parser.error('new-scope runs require explicit --reuse-v3-summary, not historical --resume-from')
    if args.reuse_django_first_two_rounds and (profile_name!='django16819' or not args.experiment_memory_standard or not args.reuse_v3_summary or args.reuse_django_first_round or args.resume_from or args.rounds!=3 or args.schedules):
        parser.error('--reuse-django-first-two-rounds requires complete Django new-scope matrix and --reuse-v3-summary, without other reuse or subsets')
    if args.reuse_django_first_round and (profile_name!='django16819' or not args.experiment_memory_standard or not args.reuse_v3_summary or args.resume_from or args.rounds!=3 or args.schedules):
        parser.error('--reuse-django-first-round requires complete Django new-scope matrix and --reuse-v3-summary, without resume or subsets')
    if args.reuse_v3_summary and not (args.reuse_django_first_round or args.reuse_django_first_two_rounds) and (not matplotlib or not args.experiment_memory_standard or args.resume_from or args.rounds!=3 or args.schedules):
        parser.error('--reuse-v3-summary requires the complete Matplotlib new-scope matrix, without --resume-from or subsets')
    out=args.output.resolve();assert out.is_relative_to(ROOT/'.artifacts') and not out.exists()
    published=ROOT/'results'/out.name;assert not published.exists()
    os.umask(0o077);out.mkdir(parents=True)
    proposed=dict(task=metadata,inputs=str(args.inputs.resolve()),runs=planned_runs(profile_name,args.rounds,args.schedules),
        selected_schedules=args.schedules,
        rounds=args.rounds,experiment_memory_standard=args.experiment_memory_standard,
        approved_workload='original_path_prefix_reuse_after_60_and_75' if cython else 'original_path_prefix_reuse_after_33_and_46' if text_editing else 'original_path_prefix_reuse_after_73_and_87' if matplotlib else 'original_path_prefix_reuse_after_24_and_29' if c4 else 'original_path_prefix_reuse_after_18_and_26',
        volume_gib_by_schedule={s:volume_gib(s) for s in EXPECTED})
    completed=[];pending=proposed['runs'];previous_failures=[]
    if args.reuse_django_first_two_rounds:
        completed,pending,previous_failures=reuse_django_first_two_rounds(proposed,args.reuse_django_first_two_rounds.resolve(),args.reuse_v3_summary.resolve())
    elif args.reuse_django_first_round:
        completed,pending=reuse_django_first_round(proposed,args.reuse_django_first_round.resolve(),args.reuse_v3_summary.resolve())
    elif args.reuse_v3_summary:
        completed,pending=reuse_v3_prefix(proposed,args.reuse_v3_summary.resolve())
    if args.resume_from:
        previous=read(args.resume_from/'result.json');old=read(args.resume_from/'plan.json')
        assert old['task']==metadata and old['approved_workload']==proposed['approved_workload']
        completed,pending,previous_failures=resume_runs(proposed,previous,retry_failed=args.retry_failed,
            accept_c4_cleanup_timeout=args.accept_c4_cleanup_timeout)
    elif args.retry_failed:parser.error('--retry-failed requires --resume-from')
    proposed.update(accept_c4_cleanup_timeout=args.accept_c4_cleanup_timeout,pending_runs=pending,
        reuse_django_first_two_rounds=str(args.reuse_django_first_two_rounds.resolve()) if args.reuse_django_first_two_rounds else None,
        reuse_django_first_round=str(args.reuse_django_first_round.resolve()) if args.reuse_django_first_round else None,
        reuse_v3_summary=str(args.reuse_v3_summary.resolve()) if args.reuse_v3_summary else None,
        reused_completed_slots=len(completed),resume_from=str(args.resume_from) if args.resume_from else None)
    write(out/'plan.json',proposed)
    if not args.execute:
        print('status=PLAN_SAVED result_dir='+str(out));return 0
    env=clean_environment(dict(os.environ,ASB_AGENTENV_SOURCE_SERVICE=SOURCE,
        ASB_ISOLATED_UNUSED_NETWORK_POOLS='on',PYTHONDONTWRITEBYTECODE='1'))
    env.pop('ASB_LOCAL_NO_PROXY',None);env.pop('ASB_LOCAL_NO_PROXY_CHECK',None)
    state=dict(status='RUNNING',stage='preflight',runs=completed,expected_runs=len(proposed['runs']),
        rounds=args.rounds,experiment_memory_standard=args.experiment_memory_standard,
        reuse_v3_summary=proposed['reuse_v3_summary'],expected_new_runs=len(pending),
        reuse_django_first_round=proposed['reuse_django_first_round'],
        reuse_django_first_two_rounds=proposed['reuse_django_first_two_rounds'],
        reused_completed_slots=len(completed),previous_failures=previous_failures,
        workload_kind=metadata['workload_kind'],metadata=metadata,started_utc=datetime.now(timezone.utc).isoformat())
    code=out/'code';code.mkdir()
    for name in ('run_django_schedule_matrix.py','django_schedule_inputs.py','run_python_schedule.py',
                 'validate_isolated_agentenv.py','report_python_schedule_matrix.py','schedule_smoke_metrics.py'):
        shutil.copy2(ROOT/'scripts'/name,code/name)
    if args.experiment_memory_standard:
        for name in ('report_experiment_schedule.py','experiment_memory.py','host_file_monitor.py','host_sandbox_monitor.py'):
            shutil.copy2(ROOT/'scripts'/name,code/name)
    if c4:
        for name in ('run_c4_schedule_matrix.py','c4_schedule_inputs.py','c4_density_input.py','isolated_storage.py'):
            shutil.copy2(ROOT/'scripts'/name,code/name)
    if matplotlib:
        for name in ('run_matplotlib_schedule_matrix.py','matplotlib_schedule_inputs.py','external_workload.py','external_oci.py','isolated_storage.py'):
            shutil.copy2(ROOT/'scripts'/name,code/name)
    if text_editing:
        for name in ('run_text_editing_schedule_matrix.py','text_editing_schedule_inputs.py','external_workload.py','external_oci.py','isolated_storage.py'):
            shutil.copy2(ROOT/'scripts'/name,code/name)
    if cython:
        for name in ('run_cython_schedule_matrix.py','cython_schedule_inputs.py','cython_schedule_proxy.py','external_guest_proxy.py','external_workload.py','external_oci.py','isolated_storage.py'):
            shutil.copy2(ROOT/'scripts'/name,code/name)
        shutil.copy2(ROOT/'configs/cython-reference-step79.patch',code/'cython-reference-step79.patch')
    started_source=False
    try:
        for row in completed:
            if row['status'] in ('ACCEPTED_CLEANUP_TIMEOUT','ACCEPTED_SEGMENTS_V3'):row['curve']=plot_run(row,published/'r1-grpo-reused')
        source=json.loads(subprocess.check_output(['docker','inspect',SOURCE],timeout=15))[0]
        assert source['Id']==SOURCE_ID and not source['State']['Running'] and source['HostConfig']['RestartPolicy']['Name']=='no'
        for item in pending:
            label=f"r{item['round']}-{item['schedule']}";state['stage']=label+'-resource-check'
            budget,checks=capacity_after_cleanup(ROOT,volume_gib(item['schedule'])*GIB,state['runs'][-1] if state['runs'] else None,**(dict(margin=START_MARGIN_BYTES) if matplotlib else dict(margin=4*GIB) if c4 else {}))
            memory=int(next(l for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')).split()[1])*1024
            write(out/(label+'-resources.json'),dict(budget=budget,disk_capacity_checks=checks,mem_available_bytes=memory,load=os.getloadavg(),utc=datetime.now(timezone.utc).isoformat()))
            assert budget['ready'] and memory>64*GIB,'resource budget insufficient before '+label+'; no guest created'
            if not started_source:
                subprocess.run(['docker','start',SOURCE],check=True,capture_output=True,timeout=30);started_source=True
                opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                for _ in range(60):
                    try:
                        with opener.open('http://127.0.0.1:39681/health',timeout=2) as response:
                            if response.status in (200,204):break
                    except OSError:pass
                    time.sleep(1)
                else:raise TimeoutError('source health timeout')
            row=dict(item,status='RUNNING',log=str(out/(label+'.log')))
            if args.reuse_django_first_round:row['measurement_origin']='new_rounds_2_3'
            if args.reuse_django_first_two_rounds:row['measurement_origin']='new_third_round_retry'
            state['runs'].append(row)
            unit=('asbhost-cython' if cython else 'asbhost-text' if text_editing else 'asbhost-matplotlib' if matplotlib else 'asbhost-c4' if c4 else 'asbhost-django')+out.name.split('-')[-1].lower()+'r'+str(item['round'])+item['schedule']
            slice_name=unit+'.slice'
            command=['systemd-run','--quiet','--wait','--pipe','--collect','--unit='+unit,'--slice='+slice_name,
                '--property=MemoryAccounting=yes','--property=IOAccounting=yes','--property=TimeoutStopSec=120',
                '--working-directory='+str(ROOT),'--setenv=ASB_HOST_FILE_SLICE='+slice_name,
                '--setenv=ASB_HOST_FILE_SAMPLING=on','--setenv=ASB_AGENTENV_SOURCE_SERVICE='+SOURCE,
                '--setenv=ASB_ISOLATED_UNUSED_NETWORK_POOLS=on',
                *['--setenv='+k+'=' for k in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy')],
                sys.executable,'-B',ROOT/'scripts/validate_isolated_agentenv.py','--automatic-window',
                '--external-workload-config',ROOT/('configs/external-cython-light.json' if cython else 'configs/external-text-editing-light.json' if text_editing else 'configs/external-matplotlib24177-light.json' if matplotlib else 'configs/external-c4-light.json' if c4 else 'configs/external-django16819-light.json'),
                '--external-workload-upstream',args.upstream.resolve(),
                '--external-workload-oci-input' if c4 or matplotlib or text_editing or cython else '--external-creation-oci-input',args.oci_input.resolve(),
                '--cython-schedule' if cython else '--text-editing-schedule' if text_editing else '--matplotlib-schedule' if matplotlib else '--c4-schedule' if c4 else '--django-schedule',item['schedule'],
                '--cython-schedule-inputs' if cython else '--text-editing-schedule-inputs' if text_editing else '--matplotlib-schedule-inputs' if matplotlib else '--c4-schedule-inputs' if c4 else '--django-schedule-inputs',args.inputs.resolve(),
                '--host-file-monitor','--host-current-only','--light-monitor']
            if args.experiment_memory_standard:command+=['--experiment-memory-standard']
            write(out/(label+'-command.json'),list(map(str,command)));write(out/'result.json',state)
            print('status=RUNNING run='+label+' result_dir='+str(out),flush=True)
            proxy=None
            try:
                begin=time.monotonic_ns()
                if cython and args.guest_relay_proxy:
                    from cython_schedule_proxy import ScheduleProxy
                    relay_dir=out/(label+'-relay');relay_dir.mkdir()
                    proxy=ScheduleProxy(relay_dir,args.upstream.resolve(),proxy_url=args.guest_relay_proxy)
                    relay_url=proxy.start()
                    command[command.index(sys.executable):command.index(sys.executable)]=['--setenv=ASB_EXTERNAL_GUEST_RELAY_URL='+relay_url,'--setenv=ASB_EXTERNAL_GUEST_RELAY_STATE='+str(relay_dir)]
                    command+=['--external-workload-relay']
                    write(out/(label+'-command.json'),list(map(str,command)))
                with Path(row['log']).open('x') as log:
                    process=subprocess.run(list(map(str,command)),cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=2400 if matplotlib or text_editing or cython else 1500)
                text=Path(row['log']).read_text()
                directories={Path(p) for p in re.findall(r'status=(?:PASS|FAIL|RUNNING) result_dir=(\S+)',text) if Path(p).parent==ROOT/'.artifacts/storage-isolated-service'}
                if len(directories)==1:row.update(storage=str(next(iter(directories))),raw=str(next(iter(directories))/'external-workload'))
                if len(directories)==1:
                    monitor=Path(row['raw'])/'runtime-monitor.json'
                    if monitor.exists():
                        row['stop_timeout_events']=[dict(session_id=s['session_id'],role=s['role'],event=s['stop_timeout_event'])
                            for s in read(monitor)['sessions'] if s.get('stop_timeout_event')]
                if process.returncode and args.experiment_memory_standard and len(directories)==1:
                    saved=read(next(iter(directories))/'result.json')
                    row.update(saved.get('experiment_memory_acceptance',{}))
                assert process.returncode==0,'native run exit '+str(process.returncode)+'; '+row['log']
                assert not row.get('stop_timeout_events'),'collector stop timeout; reclamation does not pass sampling'
                assert len(directories)==1
                row.update(collect(directories.pop(),{k:item[k] for k in ('task','round','schedule')}),driver_seconds=(time.monotonic_ns()-begin)/1e9)
                if args.experiment_memory_standard:
                    from report_experiment_schedule import collect_parent
                    row.update(collect_parent(Path(row['storage'])))
                    assert row['experiment_scope_validation']=='PASS', 'dedicated slice measurement unavailable or incomplete; see experiment_scope_errors'
                row['curve']=plot_run(row,published/label)
                print('status=PASS run='+label,flush=True)
            except BaseException as error:
                row.update(status='FAIL',error=repr(error))
                raise
            finally:
                try:
                    subprocess.run(['systemctl','stop',unit+'.service',slice_name],timeout=150,capture_output=True,check=False)
                finally:
                    if proxy:
                        try:
                            proxy.stop();row['owned_guest_relay_stopped']=True
                        except BaseException as error:
                            row.update(status='FAIL',owned_guest_relay_stopped=False,guest_relay_cleanup_error=repr(error));raise
            write(out/'result.json',state)
        assert len(state['runs'])==state['expected_runs'] and all(accepted_measurement(r) for r in state['runs'])
        state['status']='PASS_WITH_CLEANUP_EXCEPTION' if any(r['status']=='ACCEPTED_CLEANUP_TIMEOUT' for r in state['runs']) else 'PASS_WITH_REVALIDATION' if any(r['status']=='ACCEPTED_SEGMENTS_V3' for r in state['runs']) else 'PASS'
    except BaseException as error:
        state.update(status='FAIL',error=repr(error));(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        try:
            if started_source:subprocess.run(['docker','stop',SOURCE],capture_output=True,check=True,timeout=60)
            state['source_returned_to_stopped']=not json.loads(subprocess.check_output(['docker','inspect',SOURCE],timeout=15))[0]['State']['Running']
        except Exception as error:
            state.update(status='FAIL',source_cleanup_error=repr(error),source_returned_to_stopped=False)
        state['finished_utc']=datetime.now(timezone.utc).isoformat();write(out/'result.json',state)
        publish(state,published)
    print('status='+state['status']+' result_dir='+str(out)+' report_dir='+str(published),flush=True)
    return 0 if state['status'] in ('PASS','PASS_WITH_CLEANUP_EXCEPTION','PASS_WITH_REVALIDATION') else 1


if __name__=='__main__':raise SystemExit(main())
