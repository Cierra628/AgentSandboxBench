# Prettier完整GRPO／BPO：当前轻量测量实跑（2026-10-22）

**GRPO和BPO各执行一次，已取得当前轻量口径的工具时延、每guest内存／Cached、host file及PSS曲线。** 不是旧数据重画。GRPO原流程的任务后附加驻留检查失败单列，189个已完成动作与曲线直接复用，没有重跑GRPO。

## 本轮结果

| 指标 | GRPO | BPO |
| --- | ---: | ---: |
| 实际工具动作数 | 189 | 84 |
| 完整逻辑路径数 | 7 | 7 |
| 实际沙箱数／采样会话数 | 7／7 | 7／9 |
| guest周期样本总数 | 2164 | 1767 |
| host计数样本数 | 1379 | 1534 |
| 工具累计耗时／秒 | 61.753 | 45.479 |
| 整轮rollout耗时／秒 | 33.323 | 48.325 |
| PSS观察次数／其中不完整次数 | 138／6 | 154／8 |
| host实际最大采样间隔／毫秒 | 100.138 | 100.142 |
| 首段派发前host file／GiB | 21.725 | 21.960 |
| 任务观察区间host file最高值／GiB | 21.764 | 29.658 |
| 任务观察区间最后一点相对基线变化／MiB | 40.453 | 7883.309 |

工具累计时间包含并发命令，不能当作端到端耗时；BPO继承前缀不重复执行，因此84个实际动作对应七条完整逻辑路径的189个动作。整轮计时从开始创建本轮沙箱前至全部分支结果检查完成，排除外层输入准备、离线报告和清理。每种调度只有一次，不据此声称稳定性能优势。BPO观察区间包含两次保存和六次恢复，相对基线增长7883.309 MiB是整组file净变化，不等于任务Cached或共享节省；本轮没有做逐文件来源分解。

## 查看曲线

- GRPO：[guest三指标](evidence/prettier-grpo-light-20261022-guest.png)、[host完整曲线](evidence/prettier-grpo-light-20261022-host.png)、[host任务区间与基线变化](evidence/prettier-grpo-light-20261022-host-task.png)。
- BPO：[guest三指标](evidence/prettier-bpo-light-20261022-guest.png)、[host完整曲线](evidence/prettier-bpo-light-20261022-host.png)、[host任务区间与基线变化](evidence/prettier-bpo-light-20261022-host-task.png)。

guest横轴在会话内使用单调计时，按host会话启动时刻排列；host图使用host单调时钟。会话启动与guest首点之间的准备延迟未校准，不把两张图的横轴零点当成精确同时。快照期间缺口不插值，恢复实例只展示自己实际执行后的观测，不补造继承前缀。工具柱状图编号按保存的段记录展开，不冒称跨分支的全局命令开始顺序。

## 各沙箱采样峰值

峰值来自采样点，不求和各沙箱在不同时刻的峰值。内存为MemTotal−MemAvailable；Cached包含与Shmem重叠部分，不相加。

| 调度 | 分支 | 样本数 | guest已用内存峰值／MiB | Cached峰值／MiB |
| --- | --- | ---: | ---: | ---: |
| GRPO | backbone | 316 | 619.707 | 199.324 |
| GRPO | early_1 | 310 | 447.480 | 184.504 |
| GRPO | early_2 | 305 | 460.441 | 185.707 |
| GRPO | early_3 | 310 | 452.570 | 186.746 |
| GRPO | late_1 | 299 | 691.129 | 199.816 |
| GRPO | late_2 | 321 | 457.875 | 181.230 |
| GRPO | late_3 | 303 | 589.461 | 204.176 |
| BPO | backbone | 402 | 697.180 | 203.988 |
| BPO | early_1 | 230 | 454.402 | 191.336 |
| BPO | early_2 | 219 | 460.109 | 191.340 |
| BPO | early_3 | 224 | 460.266 | 191.320 |
| BPO | late_1 | 237 | 500.262 | 207.035 |
| BPO | late_2 | 231 | 461.660 | 207.238 |
| BPO | late_3 | 224 | 641.953 | 207.273 |

## 确实只启动了约定的轻量采样

新配置为`configs/platform-prettier6604-grpo-light-host.json`和`configs/platform-prettier6604-bpo-light-host.json`。旧GRPO／BPO配置默认仍使用原完整采样，不改写历史结果。

- guest每0.1秒只读取meminfo；七／九个会话均标为light、正常结束并确认进程回收，没有guest CPU、diskstats或周期vmstat文件。工具边界记录沿用既有执行器。
- host从输入准备前建立独立统计组，开启内存和I/O记账；每0.1秒读取file、current、anon、shmem及脏页／写回等计数，每1秒读取全组PSS。未启动旧服务采集器或逐VMM采集器。
- 没有逐页探针、BPF、额外采样暂停、预读、锁页或新增缓存回收；BPO保存本身需要的暂停属于调度操作。原内核和默认DAMON配置保持，不额外采DAMON事件。
- 任务结束后沿用一次普通文件mincore检查，不在任务中循环扫描，也不据此计算整体物理节省。host file是组内记账，guest Cached是guest逻辑量；PSS与file／current可能重叠，不相加。

PSS遇到进程退出的6／8次不完整扫描明确标为PARTIAL，曲线留空，不当作完整总量或零占用。

第一段派发前同步读取host基线；任务区间包含后续段间等待、保存／恢复和结果检查，不是任务进程独占占用。恢复请求耗时和恢复返回至首段派发的间隔分别保存在JSON中；后者含采样准备与恢复状态检查，不冒称guest首命令开始的精确时刻。

## 附加检查失败与修补

GRPO原工作流程`.artifacts/platform-tasks/20261022T065423Z-160678/`仍为FAIL：任务与报告通过，最后mincore扫描16 GiB卷镜像期间修改时间变化，清单INCOMPLETE。所有guest、私有服务、挂载和loop已释放；失败卷原样保留。原mincore不足与不完整状态不改判、不补零。派生报告只交付已通过的计数／时延与guest曲线，状态为PASS_LIGHT_METRICS_WITH_INCOMPLETE_INVENTORY。

未重跑GRPO。离线生成派生报告时曾把GRPO不提供的logical_action_count当成必填，报告在读取该字段处失败；按原GRPO口径使用实际189动作，补回归后直接复用原成功数据。该报告失败没有启动服务或任务。

BPO任务后查询复用现有独立卷入口的处理：先查卷内文件，再同步自有文件系统，最后查询卷镜像，避免目录元数据写回期间的修改时间变化。此改动在任务和主计时结束之后，不改变运行期采样或冻结动作。BPO的196文件附加清单为OBSERVED，本轮未检出相对file的净不足；完整记账与整体物理节省没有据此判为已测得。

## 功能与资源状态

两组原动作、顺序、退出码、关键输出、最终补丁与独立结果核对通过；BPO第11、24步保存及六次恢复的文件、进度和进程连续性检查通过。每个实例原官方结果仍0通过／2失败；采集成功不改写原任务失败。

本轮新路径通过4项回归，原轻量7项及相关77项回归通过；针对实际任务后失败增加显式报告检查并运行45项相关回归通过。后续字段修补只运行受影响的4项用例。所有回归均为离线测试，不启动AgentENV或设备。未为报告错误重跑任务。

最终源服务已恢复停止，无活动实验沙箱或私有服务；GRPO16 GiB失败卷保留，BPO成功卷按原规则回收。不暂存、提交或推送。

## 复现与证据

```bash
# 新一对实验：源服务初态须停止，脚本检查身份并按原状态收尾。
bash scripts/run-light-rollout-pair.sh --output .artifacts/<新的结果目录>
```

每轮自动核对磁盘预算：GRPO16 GiB卷须超过42 GiB可用，BPO32 GiB卷须超过58 GiB可用；20 GiB保护量与6 GiB余量保持。脚本不自动删除失败卷。

- [脱敏汇总](evidence/prettier-light-rollout-pair-20261022.json)。
- 最终汇总与各调度独立数据：`.artifacts/light-rollout-pair-20261022-final/`。
- 原GRPO采样与任务：`.artifacts/storage-isolated-service/20261022T065540Z-161492/`。
- BPO采样与任务：`.artifacts/storage-isolated-service/20261022T070143Z-166686`。
- 实现前副本、差异、测试、首个失败与恢复执行日志：`.artifacts/light-rollout-pair-implementation-20261022/`。

## 后续：相同前缀1对7对照已完成

为直接研究多沙箱文件共享，已按用户新授权补齐[相同11步的1对7短对照](HOST_PREFIX_COMPARISON.md)。指定只读rootfs缓存驻留未随数量倍增；整组记账参考差与物理分项分别列出。该短对照不是重跑本页完整GRPO／BPO，另外两个任务的完整多路径仍未补齐。

## 下一步建议

Prettier当前轻量口径的完整GRPO／BPO各一次已齐，不追加相近重跑。若恢复三任务多路径覆盖，仍建议先离线接入Sphinx七路径，再以GRPO／BPO各一次验证相同指标、分支和恢复后采样以及原结果；Xarray随后按同一范围补齐。当前用户优先研究文件共享，下一步以短对照报告为准。三任务完整多路径对照目前仍缺另外两个任务，本建议不是自动启动授权。
