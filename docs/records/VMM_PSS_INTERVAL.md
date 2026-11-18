# 可选周期PSS间隔：实现与验证

2026-11-06，先完成参数接入和离线检查，该阶段未启动服务、沙箱或真实采样。随后经用户确认完成C4双沙箱1秒／2秒各一次真实对照，见[实际报告](../../results/c4-pss-interval-pair-20261106/REPORT.md)。默认1秒保持。

在外部任务的新采集组合中，可显式添加`--vmm-pss-interval-seconds 2`。默认仍为1秒，旧命令不需要修改。PSS是按共享比例分摊的进程驻留内存；这里查询每个沙箱对应的VMM进程，不等于整个实验组的内存。

## 改什么，保留什么

- 只改变周期读取`/proc/PID/smaps_rollup`的间隔，优先候选为2秒。
- host组计数、guest普通计数和VMM普通计数仍为0.1秒；VMM身份和cgroup归属仍逐点核对。
- 第一次周期扫描仍采PSS；以后在普通扫描开始时判断是否到期，从这次扫描开始计下一个间隔，不补采过去错过的点。记录的是目标间隔，实际间隔可能因查询或调度而变长。
- 每个guest完成点的PSS查询仍独立执行，指定初始下层文件的mincore P1仍只查询一次，不受周期参数影响。
- 原任务动作、退出码、结果检查、回收策略、并发方式、计时边界及清理均保持。

非默认间隔仅适用于开启新采集组合的外部任务：C4、Django、Cython需要`--endpoint-metrics`，Matplotlib和大规模文本编辑原本就开启该组合。关闭采样、旧采集组合、密度入口及Python任务入口不接受这一非默认参数，避免参数传入却没有作用。原开关交替重复脚本没有改动，仍使用原默认1秒；1秒／2秒对照另用下文的显式脚本，不改变普通入口。

## 参数已传到线程和结果检查

传递路径：

```text
run-external-workload-light.sh
  → run_host_external_workload.py（创建实验组前及组内重入）
  → validate_isolated_agentenv.py
  → run_c4_parallel.py（仅双C4；单guest直接到下一项）
  → run_external_workload.py
  → ExternalMetrics
  → HostMonitor.collect
```

各层默认1秒；底层构造函数新增的参数只接受关键字，原位置参数调用保持可用。无效数值在绑定VMM和启动线程前被拒绝。普通默认命令不增加新命令行参数。

每个guest的`host-vmm/external/collector-status.json`记录实际传入的`pss_interval_seconds`，同时保留`interval_seconds=0.1`、`pss_samples`、普通点数、线程CPU和最长扫描时间。逐次PSS经过时间仍在`host-vmm.tsv`的`pss_scan_duration_ns`中。任务结果的`host_vmm_collector`包含同一收据；新执行路径在验收时检查请求间隔与收据一致，单guest生成报告的频率说明也使用该值。读取旧结果时，不带新校验参数的原验证函数保持原行为。

## 单组调用方式

下面是底层单组调用，会创建私有服务和两个沙箱，不是离线检查命令。它需要已有源服务健康可用；下文的一对对照脚本统一管理源服务启停。使用已经验证的本地输入，不联网或使用代理：

```bash
ASB_AGENTENV_SOURCE_SERVICE=asb-source-rebuilt-20261018T160925Z-2726362 \
bash scripts/run-external-workload-light.sh \
  configs/external-c4-light.json \
  .artifacts/workload-and-upper-20261030/upstream \
  --oci-input .artifacts/task-metrics-upgrade-20261105/oci/reshard-c4-data \
  --endpoint-metrics --sampling-control --guest-count 2 \
  --vmm-pss-interval-seconds 2
```

本轮真实对照使用相同任务参数，分别显式传1和2，其余条件保持；执行前和每组均检查原磁盘、负载及资源要求，不跳过原保护。

## 离线验证结果

25项相关测试通过，包括6项新增间隔测试和原外部任务、终点采集、接线、双C4统计检查。测试使用模拟进程／命令、虚拟时钟和临时合成文件，没有访问真实VMM的proc、cgroup或文件驻留。

```bash
PYTHONPATH=scripts:tests python3 -B -m unittest -v \
  test_external_task_metrics test_task_endpoint_upgrade \
  test_external_workload test_c4_parallel_repeats test_vmm_pss_interval
```

关键检查：

- 执行实际命令构造语句，确认外层重入、原生入口、并发协调程序和两个子程序都转发参数；省略参数时仍走1秒默认。
- 用虚拟时钟执行真实采集循环：0～4.1秒共42个普通点；默认1秒PSS为0／1／2／3／4秒共5点，2秒为0／2／4秒共3点；收据值相符。
- 用合成数据核对2秒配置仍执行独立终点PSS及一次P1，保留成本记录、文件身份检查和查询失败现场；间隔不匹配时验收失败。
- 保留原C4结果检查、采样关闭和单／双guest子程序清理检查；拒绝未接入组合或无效间隔。

日志：`.artifacts/vmm-pss-interval-offline-20261106/regression-tests.log`。修改前相关文件保存在同目录`before/`，没有改写已有实验数据或暂存文件。

## 真实对照的范围

[原离线成本分解](../../results/c4-parallel-costs-20261106/REPORT.md)推荐优先试2秒，但旧点筛选不能证明新频率的性能。后续已批准的实跑范围因此固定为双C4 1秒／2秒各一次，检查原36步、并发、身份和清理，以及实际间隔／点数／CPU和工具／任务时间；不同运行峰值差不当降频漏峰误差。不自动增加重复或正式Prettier九次。

## 2026-11-06：后续真实对照已完成

固定1秒→2秒各一次，4guest、144动作，原任务、并发、身份、真实间隔和终点检查均通过。PSS点84→41，VMM采集CPU −40.89%，全部周期CPU −30.29%。工具累计−2.91%、任务窗口−4.71%，但整轮+20.83%，2秒组17～18秒删除请求完整保留。只有顺序各一次，不能据此保证稳定减速改善；不同运行峰值差不当降频漏峰误差。原始路径、成本、file／P1差异及图见上述实际报告。

本轮使用新脚本管理源服务启停，并复用原轻量执行器。若需要复现一对，另选一个不存在的输出目录（下面命令会实际运行两个双沙箱任务）：

```bash
python3 -B scripts/run_c4_pss_interval_pair.py \
  --output .artifacts/c4-pss-interval-pair-新目录 \
  --upstream .artifacts/workload-and-upper-20261030/upstream \
  --oci-input .artifacts/task-metrics-upgrade-20261105/oci/reshard-c4-data
```

已保存结果可离线重新生成报告，不启动服务或重新查询驻留：

```bash
python3 -B scripts/report_c4_pss_interval_pair.py \
  .artifacts/c4-pss-interval-pair-20261106/live \
  results/c4-pss-interval-pair-新报告目录
```
