"""Exactly one full Prettier seven-role run per schedule; stop on the first failure."""
import argparse
import csv
from datetime import datetime, timezone
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

from isolated_storage import available, capacity_budget, GIB
from platform_tasks import ROOT, configuration, commands
from prettier_no_proxy import clean_environment
from run_light_monitor_controls import SOURCE, SOURCE_ID

SCHEDULES = ('grpo', 'bpo', 'tvcache')


def read(path):return json.loads(Path(path).read_text())
def write(path, value):Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False)+'\n')


def cache_can_release(cache,preexisting):
    return not preexisting and (cache/'resources.json').exists()


def summarize(storage):
    raw = storage/'prettier6604'; outer = read(storage/'result.json'); task = read(raw/'result.json')
    monitor = read(raw/'runtime-monitor.json'); metrics = read(raw/'schedule-smoke-metrics.json')
    assert outer['status']==task['status']==monitor['status']=='PASS' and metrics['status']=='OBSERVED'
    assert monitor['host_vmm_requested'] and monitor['interval_seconds']==.1 and monitor['host_pss_interval_seconds']==1
    assert len({s['sandbox_id'] for s in monitor['sessions']})==7
    assert len(task['paths'])==7 and task['actual_action_count']==(189 if task['schedule']=='grpo' else 84)
    assert all(v['official_passed']==0 and v['official_failed']==2 for v in task['paths'].values())
    assert len(metrics['pss']['processes'])==7 and all(p['uid']==0 for p in metrics['pss']['processes'])
    assert metrics['p1']['unique_files']==19
    assert all(outer['cleanup'][k] for k in ('owned_container_removed','mount_and_loop_removed','successful_fixture_removed'))
    with (raw/'events.tsv').open() as stream:events=list(csv.DictReader(stream, delimiter='\t'))
    saved=[dict(point=e['point'],seconds=int(e['latency_ns'])/1e9) for e in events if e['event']=='checkpoint']
    restored=[dict(sandbox_id=e['children'],seconds=int(e['latency_ns'])/1e9) for e in events if e['event']=='start' and e['parent']]
    assert (len(saved),len(restored))==((0,0) if task['schedule']=='grpo' else (2,6))
    with (raw/'host/memory-samples.tsv').open() as stream:host=list(csv.DictReader(stream, delimiter='\t'))
    assert host and all(r['cgroup_file_bytes'] for r in host)
    for session in monitor['sessions']:
        directory=raw/session['raw_directory'];assert session['status']=='PASS' and session['collector_reaped']
    host_receipts=[read(path) for path in raw.glob('host-vmm/*/*/collector-status.json')]
    assert len({r['sandbox_id'] for r in host_receipts})==7
    assert all(r['status']=='PASS' and r['pss_samples']>0 for r in host_receipts)
    return dict(schedule=task['schedule'],status='PASS',storage=str(storage),raw=str(raw),
        actual_actions=task['actual_action_count'],logical_actions=189,save_calls=saved,restore_calls=restored,
        pss_total_mib=metrics['pss']['total_bytes']/2**20,pss_per_sandbox_mib=metrics['pss']['per_sandbox_bytes']/2**20,
        mincore_p1_mib=metrics['p1']['resident_bytes']/2**20,
        cgroup_file_p1_mib=metrics['p1']['counters_before']['file']/2**20,
        cgroup_file_peak_mib=max(int(r['cgroup_file_bytes']) for r in host)/2**20,
        controller_seconds=task['controller_elapsed_ns']/1e9,
        action_download_window_seconds=(metrics['original_paths_end_monotonic_ns']-task['first_tool_start_monotonic_ns'])/1e9,
        tool_seconds=sum(p['guest_action_sum_ns'] for p in task['paths'].values())/1e9,
        endpoint_wall_seconds=(metrics['end_monotonic_ns']-metrics['start_monotonic_ns'])/1e9,
        endpoint_cpu_seconds=metrics['cpu_ns']/1e9,
        pss_wall_seconds=(metrics['pss']['end_monotonic_ns']-metrics['pss']['start_monotonic_ns'])/1e9,
        mincore_wall_seconds=(metrics['p1']['end_monotonic_ns']-metrics['p1']['start_monotonic_ns'])/1e9,
        metrics_path=str(raw/'schedule-smoke-metrics.json'))


def expanded_summary(storage):
    metrics=read(storage/'prettier6604/schedule-smoke-metrics.json');e=metrics['expanded_files']
    assert e['status'] in ('OBSERVED','PARTIAL') and e['reused_initial_objects']==19
    rows=e['files'];keys=[tuple(r['object_key']) for r in rows]
    assert len(keys)==len(set(keys))==e['unique_objects']
    rootfs=metrics['rootfs_inventory']
    for role,guest in rootfs['guests'].items():
        for lower in guest['lowers']:
            if lower['status']=='IDENTIFIED':assert ('regular',lower['device'],lower['inode']) in keys
        for kind in ('data','index'):
            assert any('rootfs_upper_'+kind in r['categories'] and role in r['users'] for r in rows) or any(
                u.get('role')==role and u['source']=='rootfs_upper_'+kind for u in e['unresolved'])
    stable=sum(r['resident_bytes'] for r in rows if r['status']=='OBSERVED')
    assert stable==e['stable_regular_resident_bytes']==sum(r['stable_resident_bytes'] for r in e['primary_categories'].values())
    return dict(status=e['status'],unique_objects=e['unique_objects'],
        stable_regular_mib=stable/2**20,new_stable_regular_mib=e['new_stable_regular_resident_bytes']/2**20,
        unknown_objects=e['unknown_objects'],unresolved_sources=len(e['unresolved']),
        separate_shmem_or_hugetlb_objects=e['separate_shmem_or_hugetlb_objects'],
        primary_categories=e['primary_categories'],
        wall_seconds=(e['end_monotonic_ns']-e['start_monotonic_ns'])/1e9,cpu_seconds=e['cpu_ns']/1e9,
        full_host_coverage=False)


def report(out, result):
    lines=['# 三调度单次可用性验证','', '仅为冒烟测试，每种至多一次；未运行和失败项不补零，不作性能优劣结论。', '',
        '| 调度 | 是否跑通 | 动作数（实际／逻辑） | 官方结果 | save/restore次数 | PSS总量 MiB | 每沙箱PSS MiB | mincore P1 MiB | cgroup file（P1）MiB | 整轮秒 | 工具累计秒 | 备注 |',
        '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    for schedule in SCHEDULES:
        row=next((r for r in result['runs'] if r['schedule']==schedule),None)
        if not row or row['status']!='PASS':
            status='未运行' if not row else 'FAIL'
            lines.append(f'| {schedule.upper()} | {status} | — | — | — | — | — | — | — | — | — | 见状态及错误记录 |');continue
        r=row
        lines.append(f"| {schedule.upper()} | PASS | {r['actual_actions']}/189 | 每实例0通过2失败 | {len(r['save_calls'])}/{len(r['restore_calls'])} | {r['pss_total_mib']:.3f} | {r['pss_per_sandbox_mib']:.3f} | {r['mincore_p1_mib']:.3f} | {r['cgroup_file_p1_mib']:.3f} | {r['controller_seconds']:.3f} | {r['tool_seconds']:.3f} | 单次描述值 |")
    lines += ['', '## 口径', '',
        'PSS在原动作及下载完成、官方检查及删除之前，按sandbox ID绑定七个root Firecracker，读取各自`/proc/PID/smaps_rollup`。内核按映射次数分摊共享页。总量是一个记录起止时刻的顺序读取窗口内七VMM之和，再除7；不是七个独立峰值之和，也不含服务及块设备提供进程。普通计数0.1秒，周期PSS保持1秒，另有这一次终点汇总。',
        'mincore仅查询经实际Firecracker drive及rootfs配置确认的19个初始下层文件，按设备号:inode去重。它是host指定文件驻留，不是guest Cached。file是该私有服务cgroup的文件记账，含shmem。PSS、file、mincore不相加当总物理内存。',
        '整轮为原控制程序的controller_elapsed_ns，含创建、准备、保存恢复、重放、检查和清理，不含外层服务分卷及RAM输入准备。工具累计为所有实际动作工具计时之和，包含并发重叠，不是墙钟。动作及下载窗口从第一段开始到最后一段返回，在P1查询前截止。',
        'GRPO七根并发执行完整路径；BPO先执行主干及两次保存，后恢复六个分支并发执行后缀；TVCache复用现有确定性前缀树实现，分叉后按子树顺序推进，并非动态内存淘汰。两种恢复调度均为84实际／189逻辑动作。', '', '## 逐次记录', '']
    for r in result['runs']:
        lines += ['### '+r['schedule'].upper(), '', '```json', json.dumps(r,indent=2,ensure_ascii=False), '```', '']
    if result.get('error'):lines += ['## 首个错误', '', '```text', result['error'], '```', '完整错误：`'+str(out/'failure.txt')+'`。原始目录保存在result.json，不自动重跑。', '']
    lines += ['批次原始记录：`'+str(out)+'`。完整save/restore逐次耗时、动作窗口及查询成本见逐次记录；失败组的原始文件不覆盖。', '']
    if result.get('expanded_files_requested'):
        lines += ['## 扩展普通文件查询', '',
                  '原19文件P1单独保留。扩展结果按设备号:inode去重，包括全部活跃lower／upper、本服务env、打开的普通文件及文件映射。未知、变化、shmem／hugetlb和设备层分列；不把已查询小计当作全部host缓存。', '',
                  '| 调度 | 覆盖状态 | 唯一对象 | 稳定普通文件驻留 MiB | 新增驻留 MiB | 未知／变化对象 | 未解析来源 | 查询墙钟秒 | CPU秒 |',
                  '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
        for r in result['runs']:
            e=r.get('expanded_files')
            if not e:continue
            lines.append(f"| {r['schedule'].upper()} | {e['status']} | {e['unique_objects']} | {e['stable_regular_mib']:.3f} | {e['new_stable_regular_mib']:.3f} | {e['unknown_objects']} | {e['unresolved_sources']} | {e['wall_seconds']:.6f} | {e['cpu_seconds']:.6f} |")
        lines += ['', 'OBSERVED仅表示该次登记范围的普通文件查询完整；PARTIAL表示存在明确未知或变化，原任务通过与覆盖状态分开。分类、完整对象及排除项在每次原始`schedule-smoke-metrics.json`的`expanded_files`中。没有周期mincore、预读、暂停、强制驻留或额外缓存控制。', '']
    if result.get('light_rollout_requested'):
        lines=[line.replace('file是该私有服务cgroup的文件记账，含shmem。','本次显式light-rollout的file来自专属slice父组，含控制程序、服务、VMM与shmem；旧host/memory-samples.tsv仍是Docker范围，不混用。') for line in lines]
        lines += ['', '## 本次新增统一轮次', '',
                  '本次仅运行所选调度，保留原有guest与host监控及终点19文件查询，另加0.1秒统一父组/PSS轮次；并非仅开轻量采样的开销实验。light-memory.jsonl中待创建和保存中的角色总量留空，实际绑定分母逐点保存。父子闭合只诊断，不作业务失败条件。普通入口默认未改变。', '']
    (out/'REPORT.md').write_text('\n'.join(lines))


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--schedule',choices=SCHEDULES,help='Run only this schedule once; omitted retains all three')
    parser.add_argument('--block-cache-method',choices=('mincore','cachestat','compare'),help='Explicit block cache method, requires cache layers; omitted preserves mincore')
    parser.add_argument('--memory-backing-links',action='store_true',help='Record memory configuration and backing layer links; requires cache layers')
    parser.add_argument('--split-ram-cache',action='store_true',help='Extra preplanned RAM-device cachestat, separate from strict mapping checks; requires cache layers and cachestat')
    parser.add_argument('--cache-layers',action='store_true',help='Opt-in four cache layers; requires expanded files')
    parser.add_argument('--expanded-files',action='store_true',help='Opt-in endpoint file coverage, once per schedule')
    parser.add_argument('--unused-network-pools',action='store_true',help='Explicit existing unused-network selector; ordinary defaults unchanged')
    parser.add_argument('--light-rollout',action='store_true',help='Explicit single BPO or GRPO light rounds alongside original validation')
    parser.add_argument('--bpo-checkpoint-provider', type=Path)
    parser.add_argument('--diagnostic-guest-kernel',type=Path)
    parser.add_argument('--diagnostic-server',type=Path)
    args=parser.parse_args();out=args.output.resolve()
    from light_prettier_rollout import diagnostic_kernel_args
    if args.diagnostic_guest_kernel and not args.light_rollout:parser.error('diagnostic guest kernel requires light rollout')
    kernel_flags=diagnostic_kernel_args(args.diagnostic_guest_kernel)
    if args.bpo_checkpoint_provider:
        if args.schedule!='bpo' or not args.light_rollout or not kernel_flags:parser.error('checkpoint window requires diagnostic light BPO')
        kernel_flags+=['--bpo-checkpoint-window','--snapshot-provider-binary',str(args.bpo_checkpoint_provider),'--snapshot-provider-get-data']
    if args.light_rollout:
        if args.schedule not in ('bpo','grpo') or not args.diagnostic_server or args.expanded_files or args.cache_layers:
            parser.error('light rollout requires single BPO or GRPO and diagnostic server without expanded queries')
    elif args.diagnostic_server:parser.error('diagnostic server requires light rollout')
    if args.memory_backing_links and not args.cache_layers:parser.error('--memory-backing-links requires --cache-layers')
    if args.cache_layers and not args.expanded_files:parser.error('--cache-layers requires --expanded-files')
    if args.block_cache_method is not None and not args.cache_layers:parser.error('--block-cache-method requires --cache-layers')
    if args.split_ram_cache and (not args.cache_layers or args.block_cache_method!='cachestat'):
        parser.error('--split-ram-cache requires --cache-layers and --block-cache-method cachestat')
    schedules=(args.schedule,) if args.schedule else SCHEDULES
    assert out.is_relative_to(ROOT/'.artifacts') and not out.exists()
    os.umask(0o077);out.mkdir(parents=True);(out/'code').mkdir()
    for name in ('run_schedule_smoke.py','schedule_smoke_metrics.py','validate_isolated_agentenv.py','run_prettier6604_grpo.py','prettier_no_proxy.py'):
        shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    if args.expanded_files:
        for name in ('host_schedule_files.py','host_file_monitor.py','experiment_memory.py','host_special_metadata.py','host_rootfs_layers.py'):
            shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    if args.cache_layers:
        for name in ('host_schedule_cache_layers.py','host_deleted_backing.py','isolated_storage.py','host_memory_backing_links.py'):
            shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    env=clean_environment(dict(os.environ,ASB_AGENTENV_SOURCE_SERVICE=SOURCE,PYTHONDONTWRITEBYTECODE='1'))
    env.pop('ASB_LOCAL_NO_PROXY',None);env.pop('ASB_LOCAL_NO_PROXY_CHECK',None)
    if args.unused_network_pools:env['ASB_ISOLATED_UNUSED_NETWORK_POOLS']='on'
    flags=['--schedule-smoke-metrics']+(['--schedule-expanded-files'] if args.expanded_files else [])+(['--schedule-cache-layers'] if args.cache_layers else [])
    if args.block_cache_method is not None:flags+=['--schedule-block-cache-method',args.block_cache_method]
    if args.memory_backing_links:flags+=['--schedule-memory-backing-links']
    if args.split_ram_cache:flags+=['--schedule-split-ram-cache']
    if args.light_rollout:flags+=['--light-rollout']
    cache=ROOT/'.artifacts/prettier6604'/('schedule-smoke-cache-'+out.name)
    cache_preexisting=cache.exists()
    result=dict(status='RUNNING',stage='preflight',runs=[]);started=False
    if args.light_rollout:result['light_rollout_requested']=True
    if args.expanded_files:result['expanded_files_requested']=True
    if args.cache_layers:result['cache_layers_requested']=True
    if args.split_ram_cache:result['split_ram_cache_requested']=True
    def call(argv,label,timeout):
        with (out/(label+'.log')).open('x') as log:
            p=subprocess.run(list(map(str,argv)),cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=timeout)
        if p.returncode:raise RuntimeError(label+' exited '+str(p.returncode)+'; '+str(out/(label+'.log')))
    try:
        if cache_preexisting:raise FileExistsError('cache directory already exists; not owned by this run: '+str(cache))
        source=json.loads(subprocess.check_output(['docker','inspect',SOURCE],timeout=15))[0]
        assert source['Id']==SOURCE_ID and not source['State']['Running'] and source['HostConfig']['RestartPolicy']['Name']=='no'
        write(out/'source-before.json',source['State'])
        configs={s:configuration(ROOT/f'configs/platform-prettier6604-{s}.json') for s in schedules}
        write(out/'effective-plan.json',dict(configs=configs,extra_flag='--schedule-smoke-metrics',extra_flags=flags,
            expanded_files_requested=args.expanded_files,cache_layers_requested=args.cache_layers,block_cache_method=args.block_cache_method or 'mincore',selected_schedules=schedules,
            unused_network_pools=args.unused_network_pools,
            split_ram_cache=args.split_ram_cache,
            repeats_per_schedule=1,guest_count=7,ordinary_interval_seconds=.1,pss_interval_seconds=1,
            initial_lower_p1_count=19,pressure=False,source_service=SOURCE,
            proxy_policy='Empty proxy environment in controller/private service/guest; pinned local registry responses only'))
        for s,cfg in configs.items():
            check,_=commands(cfg,cache)
            call(check+flags,s+'-offline',60)
        assert capacity_budget(available(ROOT),32*GIB)['ready'],'disk must exceed 58 GiB before preparation'
        result['stage']='cache-prepare';write(out/'result.json',result)
        call([sys.executable,'-B','scripts/prepare_prettier6604_cache.py',cache],'cache-prepare',300)
        subprocess.run(['docker','start',SOURCE],check=True,capture_output=True,timeout=30);started=True
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(60):
            try:
                with opener.open('http://127.0.0.1:39681/health',timeout=2) as response:
                    if response.status in (200,204):break
            except OSError:pass
            time.sleep(1)
        else:raise TimeoutError('source health timeout')
        for schedule,cfg in configs.items():
            budget=capacity_budget(available(ROOT),(16 if schedule=='grpo' else 32)*GIB)
            assert budget['ready'],schedule+' disk budget insufficient'
            memory=int(next(line for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')).split()[1])*1024
            assert memory>64*GIB,'preparation requires more than 64 GiB available memory'
            result['stage']=schedule;write(out/'result.json',result)
            write(out/(schedule+'-resources.json'),dict(budget=budget,load=os.getloadavg()))
            print('status=RUNNING schedule='+schedule+' result_dir='+str(out),flush=True)
            _,live=commands(cfg,cache);start=time.monotonic_ns()
            try:
                call(live+flags+(['--diagnostic-server-binary',str(args.diagnostic_server)] if args.light_rollout else [])+kernel_flags,schedule,1100)
                log=(out/(schedule+'.log')).read_text()
                paths=re.findall(r'status=PASS result_dir=(\S+)',log)
                storage=[Path(p) for p in paths if Path(p).parent==ROOT/'.artifacts/storage-isolated-service']
                assert len(storage)==1
                row=summarize(storage[0])
                if args.light_rollout:
                    row['light_rollout_sampling']=read(storage[0]/'prettier6604/result.json')['light_rollout_sampling']
                    assert row['light_rollout_sampling']['status']=='PASS'
                row['private_service_round_seconds']=(time.monotonic_ns()-start)/1e9
                if args.expanded_files:row['expanded_files']=expanded_summary(storage[0])
                if args.cache_layers:row['cache_layers']=read(storage[0]/'prettier6604/schedule-smoke-metrics.json')['cache_layers']
                result['runs'].append(row)
                print('status=PASS schedule='+schedule,flush=True)
            except BaseException as error:
                log=(out/(schedule+'.log')).read_text() if (out/(schedule+'.log')).exists() else ''
                paths=re.findall(r'status=RUNNING result_dir=(\S+)',log)
                storage=next((Path(p) for p in paths if Path(p).parent==ROOT/'.artifacts/storage-isolated-service'),None)
                row=dict(schedule=schedule,status='FAIL',error=repr(error),storage=str(storage) if storage else None)
                if storage and (storage/'result.json').exists():
                    state=read(storage/'result.json');row.update(failed_stage=state.get('failed_stage'),original_error=state.get('error'),raw=str(storage/'prettier6604'))
                result['runs'].append(row);raise
        result.update(status='PASS',stage='complete')
    except BaseException as error:
        result.update(status='FAIL',error=repr(error));(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        if started:
            try:
                call(['docker','stop','-t','30',SOURCE],'source-stop',60)
                state=json.loads(subprocess.check_output(['docker','inspect','--format','{{json .State}}',SOURCE],timeout=15))
                write(out/'source-after.json',state);assert not state['Running']
                result['source_returned_to_stopped']=True
            except Exception as error:result.update(status='FAIL',source_cleanup_error=repr(error))
        if cache_can_release(cache,cache_preexisting):
            try:
                call([sys.executable,'-B','scripts/prepare_prettier6604_cache.py',cache,'--release'],'cache-release',90)
                result['cache_released']=read(cache/'resources.json')['released']
            except Exception as error:result.update(status='FAIL',cache_cleanup_error=repr(error))
        write(out/'result.json',result);report(out,result)
        print('status='+result['status']+' result_dir='+str(out),flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
