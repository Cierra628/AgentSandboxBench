"""Audit saved five-workload evidence and publish records; never starts a guest."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from external_workload import load
from external_five import validate_outputs, BARRIERS
from external_five_oci import validate as validate_oci
from report_external_five import sample_stats

ROOT = Path(__file__).resolve().parents[1]
RECORDS = ROOT/'docs/records'
MIB = 2**20
TASKS = {
    'hf-model-inference': ('HF 模型推理', 'HF'),
    'mozilla__pdf.js-20957': ('PDF.js #20957', 'PDFJS_20957'),
    'apache__logging-log4j2': ('Log4j2', 'LOG4J2'),
    'count-dataset-tokens': ('数据集 token 计数', 'TOKEN_COUNT'),
    'compile-compcert': ('CompCert 编译', 'COMPCERT'),
}


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def table(path, delimiter='\t'):
    with Path(path).open() as f:
        return list(csv.DictReader(f, delimiter=delimiter))


def check(ok, message):
    if not ok:
        raise ValueError(message)


def audit(row, upstream):
    task = row['task_id']
    summary = row['summary']
    raw = Path(summary['raw_paths'].get('original_controller', summary['raw_paths']['controller']))
    native_dir = Path(summary['raw_paths']['native'])
    outer_dir = Path(summary['raw_paths']['host']).parent
    state, native, outer = read(raw/'result.json'), read(native_dir/'result.json'), read(outer_dir/'result.json')
    cfg, replay, reference = load(raw/'config.json', upstream)
    check(cfg['task_id'] == task, 'task identity differs')
    primary = upstream/cfg['run']
    source_hashes = {}
    for name in ('steps.csv', 'validation.json', 'oci-origin.json', 'run.json'):
        path = raw/('reference-'+name)
        check(path.read_bytes() == (primary/name).read_bytes(), 'saved primary differs: '+name)
        source_hashes[str(path)] = sha(path)
    for name, content in replay['files'].items():
        check((raw/'input'/name).read_bytes() == content.encode(), 'original command/input differs: '+name)
    tools = table(raw/'tools.tsv')
    check(len(tools) == len(reference) == cfg['actions'], 'incomplete tool trace')
    previous = -1
    for i, (actual, expected) in enumerate(zip(tools, reference), 1):
        start, end = int(actual['start_monotonic_ns']), int(actual['end_monotonic_ns'])
        check(int(actual['step']) == i and start >= previous and end >= start, 'invalid original tool timing')
        check(end-start == int(actual['duration_ns']), 'tool duration differs')
        check(int(actual['exit_code']) == int(expected['exit_code']), 'reference exit differs at '+str(i))
        previous = end
    check(sum(int(r['duration_ns']) for r in tools) == summary['tool_duration_sum_ns'], 'tool cumulative differs')
    check(state['task_end_monotonic_ns']-state['task_start_monotonic_ns'] == state['task_duration_ns'], 'task window differs')
    check(state['task_duration_ns'] == summary['windows']['task']['duration_ns'], 'saved task duration differs')
    validation_dir = raw/'validation-download'
    actual_exit = int((validation_dir/'validation.exit').read_text())
    check(actual_exit == replay['validation']['exit_code'], 'original checker exit differs')
    functional = validate_outputs(task, raw/'input')
    if task == 'apache__logging-log4j2':
        lines = (validation_dir/'validation.stdout').read_text().splitlines()
        check(len([s for s in lines if s.endswith('.class')]) == 5, 'five original class paths missing')
    if task == 'hf-model-inference':
        check('local service functional checks passed' in (validation_dir/'validation.stdout').read_text(), 'HF sentiment checks missing')
    if task == 'compile-compcert':
        with (raw/'input/async-waits.tsv').open() as stream:
            waits = list(csv.reader(stream, delimiter='\t'))
        check([(int(r[1]), int(r[0])) for r in waits] == list(BARRIERS.items()), 'background wait sequence differs')
        check(all(r[4] == '0' for r in waits), 'background command failed')
        check(state['memory_mib'] == 32768, 'CompCert memory configuration differs')
    events = read(raw/'events.json')
    endpoint = read(raw/'endpoint.json')
    check(endpoint == state['endpoint'] == summary['endpoint'], 'saved endpoint differs')
    check(endpoint['status'] == 'OBSERVED' and endpoint['pss']['denominator'] == 1, 'endpoint PSS missing')
    check(endpoint['pss']['per_sandbox_bytes'] == endpoint['pss']['process']['pss_kib']*1024, 'PSS units differ')
    first_check = next(e for e in events if e['stage'] == 'original-validation')
    check(state['task_end_monotonic_ns'] <= endpoint['pss']['start_monotonic_ns'], 'endpoint precedes task end')
    check(endpoint['p1']['end_monotonic_ns'] <= first_check['start_monotonic_ns'], 'endpoint follows original check')
    initial = read(raw/'initial-lowers.json')
    p1 = endpoint['p1']
    objects = {(x['device'], x['inode']): x for x in initial['objects']}
    check(len(objects) == p1['unique_files'] == len(p1['files']), 'lower file set differs')
    for obj in p1['files']:
        before = objects[(obj['device'], obj['inode'])]
        check(all(obj[k] == before[k] for k in ('device', 'inode', 'size', 'mtime_ns')), 'lower identity differs')
        check(obj['stable'] and not obj['metadata_changed'], 'unstable endpoint lower')
    check(sum(x['resident_bytes'] for x in p1['files']) == p1['resident_bytes'], 'mincore total differs')
    monitor = read(raw/'runtime-monitor.json')
    check(len(monitor['sessions']) == 1, 'not one guest collector')
    session = monitor['sessions'][0]
    guest_path = raw/session['raw_directory']/'guest-memory.tsv'
    guest = table(guest_path)
    if task == 'compile-compcert':
        check(30*1024**2 < int(guest[0]['MemTotal_kib']) < 32*1024**2, 'actual CompCert guest memory differs')
    host_path = outer_dir/'host-file/memory.tsv'
    host = table(host_path)
    vmm_path = raw/'vmm/host-vmm.tsv'
    vmm = table(vmm_path)
    receipts = dict(host=read(outer_dir/'host-file/result.json'), guest=session['receipt'], vmm=state['vmm_collector'])
    check(all(r['status'] == 'PASS' for r in receipts.values()), 'collector receipt failed')
    check(receipts == summary['costs']['collectors'], 'saved collector costs differ')
    check(read(raw/'endpoint-setup.json') == summary['costs']['endpoint_setup'], 'endpoint setup cost differs')
    check(session['collector_reaped'], 'guest collector not reaped')
    check(len(guest) == receipts['guest']['samples'] and len(host) == receipts['host']['samples'], 'sample count differs')
    check(len(vmm) == receipts['vmm']['samples'], 'VMM sample count differs')
    pss_rows = [r for r in vmm if r['pss_kib']]
    check(len(pss_rows) == receipts['vmm']['pss_samples'], 'PSS sample count differs')
    identity = endpoint['pss']['identity']
    check(all(r['sandbox_id'] == state['sandbox_id'] and int(r['pid']) == identity['pid'] and r['start_ticks'] == identity['start_ticks'] for r in vmm), 'VMM binding differs')
    check(receipts['vmm']['identity'] == identity, 'VMM identity receipt differs')
    check(max(int(r['pss_kib'])*1024 for r in pss_rows) == summary['periodic_pss_observed_peak_bytes'], 'periodic PSS peak differs')
    check(sample_stats(host, ('current','file','anon','kernel','shmem','file_dirty','file_writeback')) == summary['host']['full'], 'full host statistics differ')
    for phase, start, end in (('task', state['task_start_monotonic_ns'], state['task_end_monotonic_ns']), ('replay', state['replay_start_monotonic_ns'], state['replay_end_monotonic_ns'])):
        check(sample_stats(host, ('current', 'file'), start, end) == summary['host'][phase], 'host phase statistics differ')
    for key in ('sandbox_removed', 'service_sandbox_list_empty'):
        check(state['cleanup'][key], 'guest cleanup missing')
    for key in ('owned_container_removed', 'mount_and_loop_removed', 'original_service_unchanged', 'original_source_firecracker_identities_unchanged'):
        check(native['cleanup'][key], 'owned/source cleanup check missing: '+key)
    check(outer['owned_guest_proxy_stopped'], 'guest relay not stopped')
    oci, _ = validate_oci(native['external_five_oci']['layout'], replay['origin'])
    original_origin = read(primary/'oci-origin.json')
    exact_primary = oci['manifest_digest'] == original_origin['verification']['manifest_digest']
    for path in (Path(row['report']).parent/'summary.json', raw/'result.json', native_dir/'result.json', outer_dir/'result.json', raw/'tools.tsv', raw/'events.json', raw/'endpoint.json', raw/'initial-lowers.json', guest_path, host_path, vmm_path, validation_dir/'validation.exit', validation_dir/'validation.stdout', validation_dir/'validation.stderr'):
        source_hashes[str(path)] = sha(path)
    return dict(task_id=task, label=TASKS[task][0], original_controller_status=state['status'],
                original_native_status=native['status'], original_outer_status=outer['status'],
                original_measurement_status=state['measurement_status'], original_error=state.get('error'),
                original_lifecycle_pass=all(x['status'] == 'PASS' for x in (state, native, outer)),
                trace_exit_match=True, original_files_match=True, actions=len(tools),
                expected_nonzero=[dict(step=i, exit_code=int(r['exit_code'])) for i,r in enumerate(reference,1) if int(r['exit_code'])],
                original_checker=dict(replay['validation'], actual_exit_code=actual_exit, actual_status='PASS' if actual_exit==0 else 'FAIL'),
                functional_evidence=functional, measurement_evidence_complete=True, exact_primary_oci=exact_primary,
                actual_oci=oci, primary_manifest=original_origin['verification']['manifest_digest'],
                source_image=cfg['image'], runtime_image=state['image'],
                rebuilt_local_image=replay['origin'].get('rebuilt_local_image') if not exact_primary else None,
                rebuilt_origin_receipt=cfg.get('log4j_rebuilt_origin'),
                primary_run=cfg['run'], input_sha256=cfg['input_sha256'], helper_sha256=cfg['helper_sha256'],
                sandbox_id=state['sandbox_id'], memory_mib=state['memory_mib'], cpu=state['cpu'],
                task_duration_ns=state['task_duration_ns'], tool_cumulative_ns=summary['tool_duration_sum_ns'],
                controller_elapsed_ns=state['controller_elapsed_ns'], outer_elapsed_ns=outer['outer_elapsed_ns'],
                replay_duration_ns=state['replay_end_monotonic_ns']-state['replay_start_monotonic_ns'],
                download_duration_ns=state['task_end_monotonic_ns']-state['download_start_monotonic_ns'],
                cold_create_event=next(e for e in events if e['stage']=='cold-create'),
                endpoint=endpoint, host=summary['host'], guest_memtotal_kib=int(guest[0]['MemTotal_kib']),
                guest_monitor_profile=monitor['profile'], native_meminfo_only=monitor.get('native_meminfo_only', False),
                guest_used_peak_mib=max((int(r['MemTotal_kib'])-int(r['MemAvailable_kib']))/1024 for r in guest),
                guest_cached_peak_mib=max(int(r['Cached_kib'])/1024 for r in guest), costs=summary['costs'],
                periodic_pss_observed_peak_bytes=max(int(r['pss_kib'])*1024 for r in pss_rows),
                periodic_pss_max_interval_ns=max(int(b['monotonic_ns'])-int(a['monotonic_ns']) for a,b in zip(pss_rows,pss_rows[1:])),
                extra_management_queries=summary.get('extra_management_queries', []),
                cleanup=dict(guest=state['cleanup'], native=native['cleanup'], relay_stopped=True),
                external_firecracker_changes=native.get('external_firecracker_changes'),
                raw_paths=dict(controller=str(raw), native=str(native_dir), outer=str(outer_dir), primary=str(primary)),
                source_sha256=source_hashes)


def failed_attempts(batch_dir):
    attempts=[]
    for relative in read(batch_dir/'progress.json')['previous_raw_results']:
        outer=ROOT/relative
        status=read(outer/'result.json')
        native=Path(status['storage'])
        raw=native/'external-workload'
        state=read(raw/'result.json')
        tool_path=next((p for p in (raw/'tools.tsv',raw/'failure-download/tools.tsv') if p.exists()),None)
        log_candidates=[]
        for i in (1,3,9,13,16):
            for suffix in ('stdout','stderr'):
                p=raw/'input/logs'/f'{i:03}.{suffix}'
                if p.exists():log_candidates.append(p)
        signatures={}
        for phrase in ('ECONNRESET','RemoteProtocolError','Server disconnected without sending a response','Failed to fetch','Connection failed'):
            hits=[str(p) for p in log_candidates if phrase in p.read_text(errors='replace')]
            if hits:signatures[phrase]=hits
        error=state.get('error','')
        if 'TimeoutExpired' in error:
            reason='原模型下载超时；TimeoutExpired，完整轨迹未结束'
        elif state['task_id']=='hf-model-inference' and 'FileNotFoundError' in error:
            reason='下载结果目录多拼input/，原检查器已退出0，但原生回执FAIL'
        elif state['task_id']=='compile-compcert':
            reason='原 apt 依赖安装失败，后续工具发生退出码不一致'
        elif state['task_id']=='count-dataset-tokens':
            reason='Hugging Face 请求 RemoteProtocolError，后续缺少答案文件'
        else:
            reason='原安装/下载连接中断，CLI 检查未满足'
        attempts.append(dict(task_id=state['task_id'],outer_status=status['status'],controller_status=state['status'],
                             reason=reason,error=error[:1200],actions_completed=len(table(tool_path)) if tool_path else 0,
                             exit_differences=state.get('exit_differences'),error_signatures=signatures,
                             raw_paths=dict(outer=str(outer),native=str(native),controller=str(raw)),
                             source_sha256={str(raw/'result.json'):sha(raw/'result.json'),**{str(p):sha(p) for p in log_candidates}}))
    return attempts


def seconds(ns):
    return f'{ns/1e9:.6f}'


def cpu_seconds(receipt):
    if 'thread_cpu_ns' in receipt:
        return receipt['thread_cpu_ns']/1e9
    return receipt['cpu_user_seconds']+receipt['cpu_system_seconds']


def publish(args):
    batch=read(args.batch/'summary.json')
    progress=read(args.batch/'progress.json')
    corrections=progress.get('corrective_runs', [])
    output=RECORDS/'evidence/external-five-20261109'
    output.mkdir(parents=True, exist_ok=True)
    rows=[]
    previous=failed_attempts(args.batch)
    audited=[(old,audit(old,args.upstream)) for old in batch['records']]
    for old,row in audited:
        row['previous_failed_attempts']=[p for p in previous if p['task_id']==row['task_id']]
        row['current_run_is_corrective']=any(r['task_id']==row['task_id'] and r['outer']==row['raw_paths']['outer'] for r in corrections)
        task=row['task_id'];dest=output/task;dest.mkdir(exist_ok=True)
        source=Path(old['report']).parent
        manifest={}
        for src, name in ((source/'guest.svg','guest.svg'),(source/'pss.svg','pss.svg'),(source/'host/overview.svg','host.svg'),(Path(row['raw_paths']['controller'])/'tools.tsv','tools.tsv')):
            shutil.copy2(src,dest/name)
            manifest[name]=dict(source=str(src),sha256=sha(dest/name),bytes=(dest/name).stat().st_size)
        row['published_files']=manifest
        (dest/'audit.json').write_text(json.dumps(row,ensure_ascii=False,indent=2)+'\n')
        rows.append(row)
    lifecycle=sum(r['original_lifecycle_pass'] for r in rows)
    check(len(rows)==5 and {r['task_id'] for r in rows}==set(TASKS), 'fixed five tasks required')
    check({r['task_id'] for r in rows if not r['exact_primary_oci']} == {'apache__logging-log4j2'}, 'unexpected rebuilt image task')
    result=dict(audit_utc=datetime.now(timezone.utc).isoformat(), source_revision=batch['source_revision'],
                benchmark_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                workload_reexecutions=len(corrections), publishing_workload_executions=0,
                corrective_runs=corrections, trace_exit_matches=5, original_lifecycle_passes=lifecycle,
                primary_exact_images=sum(r['exact_primary_oci'] for r in rows),
                original_checker_passes=sum(r['original_checker']['actual_exit_code']==0 for r in rows),
                measurement_evidence_complete=5, tasks=[r['task_id'] for r in rows],
                meaning='Current selected native lifecycle receipts and original expected checker exits are verified separately; historical failures retained; Log4j checker 141 and rebuilt image identity remain explicit')
    prior=read(output/'audit.json') if (output/'audit.json').exists() else {}
    requirements=prior.get('requirements_check')
    if requirements and requirements.get('selected_runs')=={r['task_id']:r['raw_paths']['outer'] for r in rows}:
        result['requirements_check']=requirements
    (output/'audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    shutil.copy2(args.batch/'batch-cleanup.json',output/'batch-cleanup.json')
    for row in rows:
        render_task(row,output)
    render_index(rows,result,output)
    owner=ROOT.stat()
    for p in [output,*output.rglob('*'),*(RECORDS/f'EXTERNAL_FIVE_{TASKS[r["task_id"]][1]}_20261109.md' for r in rows),RECORDS/'EXTERNAL_FIVE_MEASUREMENT_20261109.md']:
        os.chown(p,owner.st_uid,owner.st_gid)
    print(json.dumps(result,ensure_ascii=False))


def render_task(row, output):
    task=row['task_id'];e=row['endpoint'];p1=e['p1'];v=row['original_checker']
    path=RECORDS/f'EXTERNAL_FIVE_{TASKS[task][1]}_20261109.md'
    evidence='evidence/external-five-20261109/'+task
    lines=[f'# {row["label"]}：实跑与严格验收（2026-11-09）','',
           '[五项总记录](EXTERNAL_FIVE_MEASUREMENT_20261109.md)。本页持续更新当前选定运行的原始回执、采集和验收；归档脚本仅只读这些数据。','',
           f'完整原轨迹 {row["actions"]} 步，脚本/辅助输入逐字节匹配，全部退出码严格匹配；{row["cpu"]} vCPU / {row["memory_mib"]} MiB，N=1。', '',
           '|验收维度|实际状态|','|---|---|',
           f'|原控制器 / 原 native / 原外层|{row["original_controller_status"]} / {row["original_native_status"]} / {row["original_outer_status"]}|',
           f'|原 measurement_status|{row["original_measurement_status"]}|',
           f'|保存的采集证据|齐全，三类采集器回执 PASS；终点身份、时点及统计复核通过|',
           f'|原检查器|{v["actual_status"]}，实际退出 {v["actual_exit_code"]}，预期 {v["exit_code"]}|',
           f'|primary 镜像字节身份|{"精确匹配" if row["exact_primary_oci"] else "不相同：显式固定重建输入"}|','']
    if task=='hf-model-inference':
        if row['original_lifecycle_pass']:
            lines += ['**当前原生全链路 PASS。** 修复下载结果目录多拼 `input/` 的错误后，按用户授权完成一次完整纠正运行；11步、原检查器退出0、measurement_status=COMPLETE及清理均取得真实PASS回执。旧FAIL/INCOMPLETE回执保持原样，在先前失败轮次中保留。此页更新为当前运行的指标和曲线。','']
        else:
            lines += ['**原生全链路仍为 FAIL。** 原始FAIL/INCOMPLETE回执保留；保存证据的离线复核不能替代修复后原生PASS。','']
        lines += ['HF配置仅给该guest设置 `HF_HUB_DISABLE_XET=1`，固定模型版本及原命令不变。','']
    if task=='apache__logging-log4j2':
        lines += ['**原检查器退出 141 仍为 FAIL。** 原命令跳过测试；Maven BUILD SUCCESS 和五条 class 路径已核对。固定基础镜像、原 Containerfile 与源码提交重建是上游文档规定的方法，新 OCI 摘要明确单列，不能声称恢复了 primary 的原始镜像字节。','']
        build=row['rebuilt_local_image']
        lines += [f'固定源码提交：`{build["source_commit"]}`。固定基础镜像：`{build["base_image"]}`。原 Containerfile SHA256：`{build["containerfile_sha256"]}`。新旧身份和显式来源回执的 SHA256 均在 audit.json 中保留。','']
    if task=='compile-compcert':
        lines += [f'实际 guest MemTotal 为 {row["guest_memtotal_kib"]} KiB，符合32 GiB配置扣除内核保留后的范围。初始固定Ubuntu镜像没有Python，使用静态C meminfo采样器；原生计数模式回执为 native_meminfo_only={row["native_meminfo_only"]}。九个后台任务均有完成等待且退出0；没有以发出后台命令代替任务完成。','']
    lines += ['## 原输入、命令与检查范围','',f'固定上游：`{row["primary_run"]}`，提交 `cbe767b2aa23ceb59875b1cb0a7a6141c749312a`。全部原命令保留在该目录的 `replay-inputs.json`，本次逐文件核对了下载保留的原脚本和辅助文件。精确 SHA256 见本页 [audit.json]('+evidence+'/audit.json)。','',
              f'实际 manifest：`{row["actual_oci"]["manifest_digest"]}`。primary manifest：`{row["primary_manifest"]}`。','',
              '原检查器命令：','', '```bash',v['command'].rstrip(),'```','',f'范围：{v["scope"]}。未扩充成官方隐藏判题或完整上游测试验收。','',
              '功能证据：','']
    if task=='compile-compcert':
        lines += ['|后台步骤|等待前步骤|屏障补等秒数|后台退出|','|---:|---:|---:|---:|']
        for bg,before,start,end,code in row['functional_evidence']['background_waits']:
            lines.append(f'|{bg}|{before}|{(int(end)-int(start))/1e9:.6f}|{code}|')
        lines += ['', '表中是原显式sleep之外的屏障补等时间，使用guest单调时钟差值，不是后台整段构建耗时。接续动作只在完成标记出现后执行。','']
    else:
        lines += ['```json',json.dumps(row['functional_evidence'],ensure_ascii=False,indent=2),'```','']
    lines += ['## 时间与逐工具结果','', '|时段|秒|','|---|---:|']
    for label,key in [('任务窗口','task_duration_ns'),('原工具累计','tool_cumulative_ns'),('replay 调用','replay_duration_ns'),('结果下载','download_duration_ns'),('控制器整轮（含检查/清理）','controller_elapsed_ns'),('外层整轮','outer_elapsed_ns')]:
        lines.append(f'|{label}|{seconds(row[key])}|')
    lines += [f'|cold-create 调用（含首次本地镜像转换；详见原事件）|{seconds(row["cold_create_event"]["command_duration_ns"])}|','',
              '任务窗口从 host 控制器入口、创建前开始，到原路径和结果下载返回结束；终点查询和原检查另列。guest/host 时钟不直接相减；无保存/恢复。原 sleep 和非零退出计入工具累计；后台完成等待另列，不将并发累计视为墙钟占比。','',
              f'[全部逐工具起止、耗时、退出码 TSV]({evidence}/tools.tsv)。预期非零退出：'+(', '.join(f'步骤 {x["step"]}→{x["exit_code"]}' for x in row['expected_nonzero']) or '无')+'。','',
              '## 内存物理量与曲线','',
              f'终点 PSS/N：**{e["pss"]["per_sandbox_bytes"]/MIB:.3f} MiB/guest，N=1**；指定初始下层 mincore：**{p1["resident_bytes"]/MIB:.3f} MiB**，共 {p1["unique_files"]} 个按 device:inode 去重的文件。覆盖不足标记：`{e["coverage_insufficient"]}`。各物理量互有重叠，不相加或相减构造总内存/共享收益；upper 与其他未查对象保持未知。','',
              '|范围|current 观测峰值 MiB|current 样本中位数 MiB|file 观测峰值 MiB|file 样本中位数 MiB|','|---|---:|---:|---:|---:|']
    for phase in ('full','task','replay'):
        q=row['host'][phase]['quantities']
        lines.append('|'+phase+'|'+'|'.join(f'{q[k][stat]/MIB:.3f}' for k,stat in [('current','observed_peak_bytes'),('current','sample_median_bytes'),('file','observed_peak_bytes'),('file','sample_median_bytes')])+'|')
    base=row['host']['before_guest']
    lines += ['',f'创建前同组基线：current {base["current"]/MIB:.3f} MiB，file {base["file"]/MIB:.3f} MiB。cgroup 覆盖本轮准备、控制器及私有服务后代；不是全机内存。guest `MemTotal−MemAvailable` 观测峰值 {row["guest_used_peak_mib"]:.3f} MiB，Cached 观测峰值 {row["guest_cached_peak_mib"]:.3f} MiB；Cached 含 Shmem，不重复相加。','',
              f'![guest 内存、Cached 与逐工具时间]({evidence}/guest.svg)','',f'![host current/file 曲线]({evidence}/host.svg)','',f'![VMM PSS/N 曲线]({evidence}/pss.svg)','',
              '终点查询发生在任务/下载结束后、原检查器之前。VMM 绑定 sandbox ID、PID/start_ticks/boot_id 与自有 cgroup；下层身份与初始清单一致。P1 前后同组 current/file、各文件身份及 resident bytes 见 audit.json 的 endpoint。','',
              '## 采集成本','', '|采集器|有效点数|观测秒数|CPU 秒|最大采样间隔秒|','|---|---:|---:|---:|---:|']
    for label,r in row['costs']['collectors'].items():
        lines.append(f'|{label}|{r["samples"]}|{r["elapsed_ns"]/1e9:.6f}|{cpu_seconds(r):.6f}|{r["max_sample_interval_ns"]/1e9:.6f}|')
    lines += ['', '|终点/设置查询|墙钟秒|CPU 秒|','|---|---:|---:|']
    for label,key in [('初始身份/监控设置','endpoint_setup'),('终点 PSS','endpoint_pss'),('终点 mincore','endpoint_mincore')]:
        r=row['costs'][key]
        lines.append(f'|{label}|{(r["end_monotonic_ns"]-r["start_monotonic_ns"])/1e9:.6f}|{r["cpu_ns"]/1e9:.6f}|')
    lines += ['', 'guest 与 host 普通计数周期 0.1 秒，PSS 周期 1 秒；有效 PSS 点数与全部 VMM 扫描点数不同，见回执的 pss_samples。采样管理调用逐次计时保留；以上是观察到的采集 CPU/查询耗时，不解释为任务减速率。未新增采样开关对照。','']
    lines += [f'周期PSS有效点数 {row["costs"]["collectors"]["vmm"]["pss_samples"]}，实际最大PSS间隔 {row["periodic_pss_max_interval_ns"]/1e9:.6f} 秒，观测峰值 {row["periodic_pss_observed_peak_bytes"]/MIB:.3f} MiB/guest。上表vmm成本包含普通扫描和周期PSS，不猜测分摊成两份CPU。','']
    if row['extra_management_queries']:
        qs=row['extra_management_queries']
        lines += [f'另有 {len(qs)} 次只读进度查询，墙钟累计 {sum(q["wall_ns"] for q in qs)/1e9:.6f} 秒；父/子进程 CPU 在 audit.json 的 extra_management_queries 中单列，独立于原工具累计。','']
    lines += ['## 清理、限制与原始路径','',
              '自有 guest、私有服务、挂载/loop、relay 均已清理；本批源服务在全部任务结束后停止并移除。成功卷删除，失败现场与固定输入保留。全机存在外部 VMM 变化，自有源服务身份检查通过；本记录不声称独占主机或因果性能。','',
              '[批次清理回执](evidence/external-five-20261109/batch-cleanup.json)。原始路径保留如下，未搬移、覆盖或重新查询已删除 VMM 的历史 PSS/mincore：','', '```text',
              '\n'.join(k+': '+v for k,v in row['raw_paths'].items()), '```','',
              f'[源文件 SHA256、完整身份/成本/基线与审计结果]({evidence}/audit.json)。正式阅读入口位于 docs/records；原 results 内自动报告作为历史产物保留。','']
    if row['previous_failed_attempts']:
        lines += ['## 先前失败轮次（保留）','', '|原外层目录|完成动作数|原外层 / 控制器|具体失败|','|---|---:|---|---|']
        for attempt in row['previous_failed_attempts']:
            lines.append(f'|`{attempt["raw_paths"]["outer"]}`|{attempt["actions_completed"]}|{attempt["outer_status"]} / {attempt["controller_status"]}|{attempt["reason"]}|')
        lines += ['', '各次退出码差异、原错误、命中错误的原日志路径与 SHA256 均在本页 audit.json 的 previous_failed_attempts 中。纠正运行与失败轮次分开保存，未覆盖失败现场。']
    path.write_text('\n'.join(lines)+'\n')


def render_index(rows,result,output):
    lifecycle=result['original_lifecycle_passes']
    hf=next(r for r in rows if r['task_id']=='hf-model-inference')
    hf_note=('HF已按用户授权完成修复后的完整纠正运行，当前原生回执PASS，历史FAIL/INCOMPLETE保留。' if hf['original_lifecycle_pass']
             else 'HF原生回执仍为FAIL/INCOMPLETE，保存证据复核不改写其状态。')
    lines=['# 五项任务：实跑、严格验收与证据索引（2026-11-09）','',
           f'**当前选定的五条完整固定轨迹均严格匹配预期退出码，所需采集证据齐全；原生全链路为 {lifecycle} PASS / {5-lifecycle} FAIL。** '+hf_note+'Log4j2原检查器实际退出141，继续记FAIL且与原预期一致；其镜像为固定重建版本，与primary字节不相同。按原预期验收通过，不表示原检查器全部退出0。','',
           '正式总报告和各任务 MD 归档于 `docs/records/`；小型脱敏审计 JSON、逐工具 TSV、SVG 曲线归档于 `docs/records/evidence/external-five-20261109/`。`results/`、`.artifacts/` 保留本机原始运行产物、失败现场与固定镜像；不搬移原始证据，不把镜像、凭据、数据库或大日志加入文档。旧自动生成 MD 保留为历史产物，正式入口以本目录为准。','',
           f'已 `git pull --ff-only origin main`，基准提交 `{result["benchmark_head"]}`。上游固定提交 `{result["source_revision"]}`，以primary为准。当前更新包含用户明确授权的HF纠正重跑；其余四项沿用已通过的单沙箱运行，不进行重复、压力、多调度实验或调用模型。总记录和各任务文档持续覆盖更新，不为每次复核新开文档。','',
           '|任务 / 详细记录|动作数|原控制器/native/外层|原检查器|primary 镜像|','|---|---:|---|---|---|']
    for r in rows:
        name=f'EXTERNAL_FIVE_{TASKS[r["task_id"]][1]}_20261109.md'
        v=r['original_checker']
        lines.append(f'|[{r["label"]}]({name})|{r["actions"]}|{r["original_controller_status"]}/{r["original_native_status"]}/{r["original_outer_status"]}|{v["actual_status"]} / {v["actual_exit_code"]}|{"精确匹配" if r["exact_primary_oci"] else "固定重建，新摘要"}|')
    lines += ['', '原检查范围：HF 正负样例服务；PDF.js 两轮各 1127 specs / 0 failures / 56 pending；Log4j2 Maven 编译与五条 class 路径、原命令跳过测试；token 计数仅原 26 行、答案 79586；CompCert 32 GiB、318 步、九处后台完成等待及原编译器编译运行检查。预期非零保留，不扩展为完整官方隐藏验收。','',
              '|任务|任务窗口秒|原工具累计秒|终点 PSS/N MiB，N=1|终点初始下层 mincore MiB|下层文件数|','|---|---:|---:|---:|---:|---:|']
    for r in rows:
        e=r['endpoint']
        lines.append(f'|{r["label"]}|{r["task_duration_ns"]/1e9:.3f}|{r["tool_cumulative_ns"]/1e9:.3f}|{e["pss"]["per_sandbox_bytes"]/MIB:.3f}|{e["p1"]["resident_bytes"]/MIB:.3f}|{e["p1"]["unique_files"]}|')
    lines += ['', '上述内存量互有重叠，不能相加。各任务页另列 guest used/Cached 曲线、host current/file 的创建前基线、全流程/task/replay 观测峰值与样本中位数、各采集器 CPU/点数/最大间隔、终点查询成本，以及采集对象身份和覆盖不足。upper 等未查对象未知，不记为零；没有采样开关、重复、压力或多调度实验。','',
              '## HF：当前回执与历史失败','',
              hf_note+'原目录错误是读取下载结果时多拼了一层input/。当前数值来自本页选定的真实运行，旧失败目录仍可追溯。HF仅在本guest禁用Xet、走Hub HTTP，固定模型和原命令保持。','',
              '## Log4j2：固定重建与原检查器 FAIL','',
              '原 primary OCI 已被清理，采用固定上游文档允许的原 Containerfile、固定基础镜像和源码提交重建；全部基础 diff IDs、构建输入及新 OCI blobs 经核验，新旧 manifest 在任务页单列。实际检查器退出 141 与参考一致，只表示保留了原检查语义，不表示检查器成功。','',
              '## 失败历史、采集与清理','',
              '11 月 6 日的失败及 11 月 9 日纠正运行分别留在原路径，详见各页源路径和本机 `results/external-five-recovery-20261109/progress.json`、`serial-status.tsv`。此前 HF 下载超时，PDF.js 安装连接断开，token 的 Hugging Face 请求断开，CompCert 安装依赖失败；没有静默删除失败轮次。服务器曾重启，后四项前重验源服务。','',
              '自有 guest、私有服务、挂载/loop、relay 和批次源服务已清理；失败原卷仅回收零数据区，前后 SHA256、inode 与逻辑大小核验保留。共享主机的外部 VMM 变化继续记录，自有源服务检查通过，不主张主机独占或因果性能。','',
              '[本次严格复核计数](evidence/external-five-20261109/audit.json)；[原批次清理回执](evidence/external-five-20261109/batch-cleanup.json)。每个任务 audit.json 包含对原始源文件的 SHA256；本机源路径缺失时应明确报缺，不将离线副本当成新的真实运行。','',
              '本次归档时服务、镜像、磁盘与权限检查另见 [host-check.json](evidence/external-five-20261109/host-check.json)。源服务已按批次清理停止；本次只读文档审计不需要重启服务。','',
              '本次 `bash scripts/check-platform-offline.sh` 通过；它验证离线接线，不代替原生严格回执。未提交、推送或发布原始运行数据。']
    requirements=result.get('requirements_check')
    if requirements:
        lines += ['', '## 按原任务要求逐项检查','',
                  f'最近核对日期：{requirements["checked_client_date"]}。结果：**{requirements["status"]}**，范围为原任务规定的功能和采集验收；此次核对没有重新运行任务。继续更新本节及原审计摘要，不另开复核记录。','',
                  '|要求|完成情况|核对依据|','|---|---|---|']
        for item in requirements.get('items',[]):
            lines.append(f'|{item["requirement"]}|{item["status"]}|{item["evidence"]}|')
        current=requirements.get('current_resource_readiness')
        if current:
            lines += ['',f'本次核对时可用磁盘 {current["available_disk_bytes"]/2**30:.3f} GiB；新实跑入口仍要求严格大于58 GiB。自有源服务已按任务要求清理停止。当前新跑条件与已完成结果分别记录，后续运行前仍须重新满足资源及源服务条件。','']
        lines += ['', '注意：每项当前通过结果均来自单沙箱完整轨迹；此前失败和用户授权的纠正重跑全部留存，并非历史上每项只启动过一次。Log4j2退出141及固定重建镜像差异按原范围单列，原检查器并非全部退出0。','']
    (RECORDS/'EXTERNAL_FIVE_MEASUREMENT_20261109.md').write_text('\n'.join(lines)+'\n')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch',type=Path,default=ROOT/'results/external-five-recovery-20261109')
    parser.add_argument('--upstream',type=Path,default=ROOT.parent/'AgentSandboxWorkloadResults')
    publish(parser.parse_args())
