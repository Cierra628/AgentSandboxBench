# 外部任务计时：区分命令返回和本地日志写入

2026-11-02，离线修改及验证完成。适用于 `run_external_workload.py` 的 C4、Django、Cython 路径，包括已有 C4 多沙箱入口。该离线阶段没有启动沙箱或任务；随后完成的真实验证见下文。

## 现在分别记录什么

每次调用仍写入 `events.json`，新增 `call_timing_version=2`，所有时间以纳秒为单位。

| 字段 | 实际范围 |
| --- | --- |
| `command_start_monotonic_ns`、`command_end_monotonic_ns`、`command_duration_ns` | 紧贴 `subprocess.run` 前后，包含客户端启动、通信、输出接收、等待和退出；不含返回后的 stdout 文件写入 |
| `stdout_log_duration_ns` | 返回后创建、写入并关闭本地 stdout 文件 |
| `event_lock_wait_ns` | 等待其他调用完成事件记录的锁 |
| `events_log_duration_ns` | 获得锁后，JSON 编码及 events.json 文件写入 |
| `call_elapsed_ns` | 本次调用开始到 events.json 写入结束，包含上述等待以及本地文件准备等余量 |
| 原 `duration_ns` | 保留原边界：包含 stdout 写入及事件锁等待，不含随后 events.json 写入 |

命令时间不是纯 RPC（远程调用）或纯 guest 工具时间。stderr 仍在命令运行中直接写本地文件，其阻塞属于命令时间；本次没有拆开这部分。文件写入时间包括打开、编码、写入和关闭，没有额外调用 fsync，不表示数据已持久保存到磁盘。

`command_returned` 表示是否取得退出码；超时或启动失败保留 `exit_code=null`，未执行的 stdout 写入时间为 null，不填零。`stdout_log_completed`、`events_log_completed` 分别标明本地写入是否成功。非零退出、超时和日志写入错误仍使执行失败。

## 最后一条事件如何保存自己的写入时间

事件写入结束后才能知道这次写入耗时，因此先在内存中补全该条记录，下次写入会保存它。控制程序收尾时，在采集管理线程收回后再写一次 events.json，保存最后一条记录的完整计时。这次额外写入的耗时（含锁等待）单独存在 `result.json` 的 `call_timing.final_events_flush_ns`；写入失败则状态为 FAIL，保留 `events_final_flush_error`。

原 `controller_elapsed_ns` 保留原起止位置：从控制程序设置 begin，到收尾完成、写 result.json 之前；现在也包含新增的最终事件写入。不包含此前的模块导入、配置加载或随后 result.json 编码／写入。并行调用的日志累计时间可能与其他调用重叠，不能把各累计值再加到控制程序墙钟时间上。

结果的 `call_timing` 给出 replay 的完整记录、stdout／事件写入／锁等待累计值和最终事件写入成本。采样开关报告透传该对象；密度报告按 guest 透传到 `external_call_timing`。旧结果没有这些字段时仍可读取，不反推缺失的命令时间；原 `replay_cli`、动作／下载窗口及全部历史结果不变。

## 离线验证结果

[模拟记录](../../results/external-call-timing-offline-20261102/REPORT.md)用虚拟时钟和模拟命令得到：命令 10 ms、stdout 写入 20 ms、事件锁等待 5 ms、events.json 写入 30 ms；原 duration 为 35 ms，完整调用为 65 ms。额外最终事件写入为 35 ms（含 5 ms 锁等待）。这些数字是设定的测试条件，不是沙箱性能结果。

59 项相关测试通过，覆盖慢日志边界、并发记录、非零退出／超时、两类日志写入失败、最终事件写入失败、原 C4／Django／Cython 验收与清理、旧数据兼容及报告传递。原任务动作、普通采样／回收设置和入口参数保持；新增计时及最终文件写入的实际成本尚未实测。

```bash
python3 -B -m unittest discover -s tests -p 'test_external*py' -v
PYTHONPATH=scripts:tests python3 -B -m unittest \
  test_c4_density_child test_collect_host_density test_host_density_offline -v
```

测试只使用临时文件、模拟服务调用和少量本地 Shell 样例。完整输出位于 `.artifacts/external-call-timing-20261102/external-tests.log`、`density-tests.log`。

## P0只提示分项不同步，不改变取样

密度报告新加 `p0_counter_flags.counters_before/counters_after.file_exceeds_current`，直接比较原 P0 前后保存的 file 与 current。true 提示该时点不能把分项当作同步拆账；false 也不证明同步或组外容量为零。不改原值、不等数值对齐、不增读量，不改 P0 驻留为零的验收。已有12组报告不覆盖。

## C4真实两组已完成

[共享／独立各七沙箱的正式结果](../../results/c4-call-timing-20261102/REPORT.md)已完成，共14guest／504动作。原任务、身份、驻留、0.1秒采样及清理通过，逐调用计时完整；七guest所有stdout／事件／锁等待／最终事件写入累计0.099／0.197秒，普通调用日志及锁等待最大9.050／27.607毫秒。本轮未出现旧47秒等待，不据此抹去旧异常或估算新旧计时的整体减速。

仅运行这两组的显式入口（普通十二组默认保持）：

```bash
bash scripts/run-host-density.sh --c4-timing-pair \
  --output .artifacts/新的批次目录 \
  --daemon .artifacts/host-density-build-20261102/uvm-ublk-daemon \
  --c4-input .artifacts/c4-oci/20261102 \
  --upstream .artifacts/workload-and-upper-20261030/upstream
```

每组仍要求可用盘严格>106 GiB、MemAvailable>64 GiB，运行中保留20 GiB保护；镜像复用本地校验收据，不联网。两组执行后按原入口收集逐组数据，再用`scripts/report_c4_call_timing.py 批次目录 results目录`生成计时报告。本轮报告和七张图已实际核对，服务／guest及成功卷均已收尾。

## 下一步建议

仅离线统一推荐入口与新旧计时说明。本轮日志不是主要成本，不再追加同类实跑或日志优化；后续若重新出现明显等待，利用新字段定位受影响调用后再决定验证范围。
