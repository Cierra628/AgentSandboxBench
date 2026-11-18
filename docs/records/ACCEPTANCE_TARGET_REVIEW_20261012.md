# 实验平台验收目标符合性检查

检查日期：2026-10-12。对象：当前工作目录的代码、配置及本机保留的原始结果。Git HEAD为`3ec69ee31f51085086a063b644855f4336fc2027`；Exp2及最新分支监控等内容仍有未提交修改，本报告包含这些工作区实现，不只评价该提交。

**按用户2026-10-12修订的口径及随后完成的在线验证，12项中11项满足、1项部分满足，没有整项未实现。单条重放、完整VM恢复续跑、自动曲线和阶段报告已可以交付。** D1改为“对沙箱运行的影响较小”，已验证的轻量监控按此口径通过，保留实际开销。B1真实在线Agent接入也已通过：4轮模型交互、3工具、独立任务检查及新VM严格重放，见[证据](evidence/online-agent-20261012.json)。A1部署迁移由用户暂缓，不是当前推进的阻塞。精细物理分析会暂停VM，仍作为另一种实验配置报告，不归入低影响监控。缓存清理对照已经具备；本次目标没有要求精确MiB上限，不将这种增强功能新增为交付门槛。

这不撤销此前限定范围的本机第一版交付结论，也不要求补齐所有任务与所有调度的组合才算初步交付。下文的“满足”均注明已验证范围，不表示任意任务、内核和机器均已支持。

## 检查方法和证据等级

已读取本地`AGENTS.md`，检查`README.md`、`docs/USAGE.md`、入口及配置，继续定位调度、恢复、采集、物理页计数、报告和管理实现。主要目录为：

| 目录 | 实际用途 |
| --- | --- |
| `scripts/`、`configs/` | 统一入口、任务适配、固定版本、采集和报告 |
| `AgentENV/` | 官方固定版本源码 |
| `IncrementalDAX_moti/` | 学长固定实验、冻结动作、controller及两后端fork |
| `backend/`、`patches/`、`runtime/` | 后端适配、已记录补丁、本机CLI和运行依赖 |
| `docs/records/evidence/` | 可上传的结果摘要和图表 |
| `.artifacts/` | 本机原始动作、状态证明、样本、事件、日志及归档 |

源码固定版本见[配置](../../configs/source-revisions.json)：官方AgentENV为`2f48c9c`，学长仓库为`a9bc7dea`；学长的AgentENV/TrEnv-X子模块分别为`6c67f92a`/`53041d8c`。平台复用原生生命周期和冻结runner，另有本地适配及修复，不能把全部结果称为未经修改的最新上游结果。

证据使用顺序为：本机原始结果及状态记录、由原始结果复算的报告、公开摘要、静态代码、文档计划。报告中的运行数字均为已有实测；本次没有启动VM、终止实验、修改平台代码、覆盖结果或清空host缓存。

本次额外执行的短时离线检查：

```bash
bash scripts/use-platform.sh check configs/platform-prettier6604-bpo.json --offline
bash scripts/use-platform.sh check configs/platform-prettier6604-tvcache.json --offline
bash scripts/use-platform.sh check configs/platform-exp2-drop.json --offline
```

三条均退出0、`status=VALIDATED`，仅验证配置和固定输入，不代替真实运行。完整输出在`.artifacts/acceptance-target-review-20261012/offline-config-checks.json`。

还以只读Python核对：BPO/TVCache原始`result.json`的状态、84个动作；六份`restored-*.json`的边界及下一步；逐会话`collector-status.json`、TSV样本数量和身份；三个任务的物理汇总与逐点`point.json`；Exp2三组的32动作/四保存/四恢复；原生归档恢复的进程、匿名内存、打开文件及实际续跑步骤。上述核对全部通过。首次临时核对命令有缩进错误，修正后完成；没有因此重新运行实验。

## 逐项汇总

| 验收项 | 判定 | 实现及运行证据 | 缺口/风险 |
| --- | --- | --- | --- |
| A1 部署、运行、管理、监控入口 | 部分满足 | `use_platform.main()`提供check/run/report/deployment/resources/retain；`platform_tasks.run()`调用隔离后端并自动报告。准备脚本`02-prepare-runtime.sh`、`03-smoke-server.sh`和固定源码获取脚本存在；本机完整运行见下文R1—R5 | 已准备本机闭环通过；运行仍依赖固定服务名、镜像、CLI及历史输入。新机器部署不是一条完整准备命令，未有第二服务器真实运行证据 |
| B1 已有及在线trajectory收集 | 满足 | `tool_trajectory.begin/capture/freeze/validate/replay`及`model_tool_agent.run()`实际接通GLM模型决策和工具结果回传；4轮模型/3工具、独立CSV测试及新VM严格重放PASS，204份raw不变，见在线证据 | 限当前shell接口；每调用新bash，无持久PTY。CSV为首个在线任务，其他研究任务在线提示与输入尚未配置化 |
| B2 单trajectory重放 | 满足 | `run_exp1.capture()`检查动作SHA、顺序、预期失败和patch；`run_prettier6604_grpo.verify_path()`及任务适配做同类检查。R1为当前统一入口29步严格重放PASS；固定Exp1有正式验收及原始重放 | 单条核心项已有实际证据。Sphinx/Xarray当前诊断配置保留严格FAIL，不能将其改称严格重放通过；这不否定已严格通过的单条路径 |
| C1 可选择不同rollout | 满足 | `platform_tasks.configuration/commands()`分派single/grpo/bpo/tvcache及Exp2 four-rounds；`execution_paths()`、`prefix_tree_plan()`执行各调度。Prettier GRPO、BPO、TVCache均有完整运行 | 调度可配置，但仅现有适配；不是任意插件。Sphinx/Xarray七路径和TrEnv-X Exp3适配未完成。当前调度不表示RL训练、自动熵选点或在线缓存系统 |
| C2 保存、恢复状态并继续 | 满足 | `run_exp2.verify_boundary()`核对文件/进度及PID、boot_id、匿名内存、打开文件；恢复后执行下一轮。R5为每组四次恢复，R3/R4为11及24步后六个恢复分支；R7为归档移除旧服务后恢复并只执行3、4步 | 已验证指定边界，未承诺任意边界。AgentENV为完整VM状态；TrEnv-X Exp1/Exp3文件层继承与Exp2原生完整VM恢复不能混称 |
| C3 模板/checkpoint及子沙箱管理 | 满足 | `platform_management.resources()`查询instances/templates/snapshots；控制器用`start <template/alias>`启动新VM并定向删除。`native_checkpoint_bundle.export/load/restore()`保存和验证原生仓库，R7在新私有服务加载成功 | 支持本机固定版本。`retain()`只保护结果/归档，不使已删除snapshot复活；归档按仓库保存，跨机器兼容和大任务归档尚未验证 |
| D1 自动采集，对沙箱运行影响较小 | 满足 | `RuntimeMonitor.start/mark/stop()`异步持续采样；任务输出、预期失败和patch保持。R8五对开关的工具/重放/生命周期平均变化为+2.40%/+7.43%/+6.44%，按用户修订的定性口径通过 | 判定限已验证轻量模式，不宣称零影响或所有负载都有相同开销。精细分析会暂停VM，单列成本；新BPO/TVCache的开销对照尚未运行，原始NOT_VALIDATED标记保留 |
| D2 内存曲线、峰值、物理缓存和组成 | 满足 | `guest_memory_collector`/`sandbox_runtime_monitor`读取guest meminfo；`host_sandbox_monitor.Binding.resolve/read()`绑定VMM并读RSS/PSS；`report_sandbox_runtime.write_report()`输出曲线、观测峰值、组成及占比；R1/R2有物理测量点，R9有跨VM去重 | 满足已声明的计量范围：物理点是file-LRU页，非连续全物理内存扫描；Xarray两点各最多4 KiB未知。默认持续报告无swap曲线，边界状态可读SwapTotal/Free；组成含重叠，不能相加成100%饼图 |
| D3 阶段时延breakdown | 满足 | `events.json`保存控制器阶段；runner保存guest动作时间；`report_exp2.report()`另列checkpoint、restore至工具可用、状态验证、清缓存；R3/R4/R7分别记录接口及归档阶段 | 工具并发耗时之和不是端到端时间；BPO/TVCache的restore请求时间不等于全部工具通道就绪时间。嵌套阶段不能重复相加 |
| E1 多任务执行并输出指标 | 满足 | R1/R2覆盖PPT三个任务；另有Exp1 Prettier #14400和R6工具采集负载。三任务均有持续曲线、阶段耗时和指定边界物理数据 | 三任务能执行并测量，不代表原模型修复全部通过。仅Prettier已覆盖完整七路径三调度，不要求所有18种组合作为本项前置 |
| E2 直接读出内存和page cache | 满足 | `runtime-metrics.json.per_sandbox`给出nonfree观测峰值、Cached及组成；`physical_page_cache.points[].kernel_cache.frozen_guest_file_cache_physical_bytes`给出物理量。共享配置另有`shared_cache.guest_cache/backing_cache` | 必须选择guest逻辑内存、host VMM或共享服务口径；不能把不同层级相加。峰值时刻与物理点时刻不同，不拿两者计算同一时刻占比 |
| E3 控制page cache大小并比较时延 | 满足 | Exp2 `cache_mode=preserve/drop`参数化；`run_exp2.run()`仅在guest执行sync/drop_caches=3，R5实际Cached下降且两组都有时延。`guest_damon_control`有回收策略开关及历史对照 | 已支持保留/清除缓存的对比，不是指定容量上限。drop=3也影响slab；每配置一次及顺序/冷热混杂，不足以把时延差全部归因缓存。精确预算属于后续增强 |

D2的“满足”指曲线、物理测量点和已测组成均已实现并运行，不表示每种内存组成都完整归因。新增swap持续曲线是小范围补充；不能把RSS（VMM进程驻留内存）、PSS（按共享比例分摊的驻留内存）当成guest匿名内存或物理文件缓存。

## 原始结果索引

以下公开摘要同时给出代码/输入哈希和本机原始路径；本次又直接读取了核心raw记录，未只凭摘要的PASS判定。

| 索引 | 公开证据 | 本机原始结果或报告 |
| --- | --- | --- |
| R1 Prettier完整重放及物理缓存 | [platform-task-entry-cache-20261011.json](evidence/platform-task-entry-cache-20261011.json) | `.artifacts/storage-isolated-service/20261011T065438Z-468941/prettier6604`；自动报告`.artifacts/platform-tasks/report-20261011T065320Z-468452` |
| R2 Sphinx/Xarray完整任务 | [python-task-cache-20261011.json](evidence/python-task-cache-20261011.json) | Sphinx：`.artifacts/storage-isolated-service/20261011T110513Z-628998/python-backbone`；Xarray：`.artifacts/storage-isolated-service/20261011T110827Z-666395/python-backbone` |
| R3 BPO完整分支 | [prettier6604-bpo-runtime-20261012.json](evidence/prettier6604-bpo-runtime-20261012.json) | `.artifacts/storage-isolated-service/20261012T050116Z-883459/prettier6604` |
| R4 TVCache完整分支 | [prettier6604-tvcache-runtime-20261012.json](evidence/prettier6604-tvcache-runtime-20261012.json) | `.artifacts/storage-isolated-service/20261012T051547Z-895688/prettier6604` |
| R5 Exp2三组 | [exp2-three-configurations-20261012.json](evidence/exp2-three-configurations-20261012.json) | AgentENV preserve/drop：`.artifacts/storage-isolated-service/20261012T030459Z-800877/prettier6604`、`20261012T030755Z-804092/prettier6604`；TrEnv-X路径见摘要runs |
| R6 新工具调用采集和重放 | [platform-tool-capture-20261011.json](evidence/platform-tool-capture-20261011.json) | `.artifacts/storage-isolated-service/20261011T115714Z-690913/python-backbone`内captured/replayed和replay-proof.json |
| R7 持久原生checkpoint恢复 | [native-checkpoint-durable-20261011.json](evidence/native-checkpoint-durable-20261011.json) | `.artifacts/storage-isolated-service/20261011T124240Z-706355/python-backbone/result.json`；源包在`20261011T123631Z-703748/checkpoint-bundle` |
| R8 轻量监控五对开关 | [runtime-prettier-expanded-pairs-20261011.json](evidence/runtime-prettier-expanded-pairs-20261011.json) | `.artifacts/runtime-monitor-pairs/20261011T035008Z-420104`及摘要runs逐次指向的raw |
| R9 共享计数及1/2/7实例 | [shared-task-cache-20261011.json](evidence/shared-task-cache-20261011.json) | 摘要successful_single/pair/seven下的raw及各`shared-cache/`物理页集合；采用最新成功七实例，旧FAIL保留 |

GRPO新增指标原始记录为`.artifacts/storage-isolated-service/20261010T044652Z-104008/prettier6604`，见[runtime-managed-grpo-20261010.json](evidence/runtime-managed-grpo-20261010.json)。固定Exp1验收在[initial-acceptance-delivery-20261007.json](evidence/initial-acceptance-delivery-20261007.json)，其输入及SHA由`configs/initial-acceptance-delivery.json`指定；这是历史完整验收证据，不冒充本次新实验。

## 已有指标及解释

### 三个研究任务

均为AgentENV、single、2 CPU/4096 MiB；约100 ms持续采样，物理分析在指定工具段结束后暂停测量。镜像准备缓存为温热状态，不主动清host/guest缓存，不称冷缓存实验。

| 任务 | 动作/持续样本 | guest非空闲观测峰值 MiB | 逻辑Cached观测峰值 MiB | 步骤：已确认物理file-LRU MiB | guest工具之和 秒 | 控制器生命周期 秒 | 分析/其中暂停 秒 |
| --- | ---: | ---: | ---: | --- | ---: | ---: | ---: |
| Prettier #6604 | 29 / 328 | 554.20 | 223.90 | 5:210.35；24:243.56；29:138.29 | 12.017 | 47.825 | 22.690 / 11.084 |
| Sphinx #7590 | 35 / 374 | 235.73 | 109.30 | 13:31.35；29:60.82；35:75.19 | 11.301 | 48.145 | 20.455 / 6.308 |
| Xarray #6992 | 35 / 789 | 965.50 | 348.30 | 13:128.17；22:309.16；35:332.18 | 34.018 | 98.013 | 28.399 / 13.499 |

MiB=2^20 bytes，原meminfo为KiB，事件为ns，表中换算成秒。guest非空闲内存是`MemTotal−MemFree`，包括文件缓存和内核等，不是应用RSS。物理file-LRU包括普通文件、块设备页和已识别文件系统元数据，类型分开保存，并包含每VM三个校准内容页12 KiB；不等于所有host缓存或DAX层驻留。

Xarray第22/35点已确认量分别324,177,920和348,319,744 bytes，各有一页未知，因此实际值的上界最多再增加4096 bytes。这是已量化的局部缺口，不需因此重跑整个任务。

Prettier非空闲峰值时Cached=213.62 MiB、占38.55%；AnonPages占40.02%、Buffers占8.07%、Slab占7.76%、Shmem占3.08%。Cached包含Shmem，以上是各指标相对同一分母的比例，不能当互斥分区求和。Cached的全程最大值223.90 MiB与非空闲峰值时的Cached不同。

模型任务评测单列：Prettier原路径0通过/2失败；Sphinx24通过/1失败；Xarray945通过/12失败。最新Python物理分析运行是批准的精确管道诊断变体，strict_replay_status=FAIL仍保留；较早Sphinx完整严格重放和第29步恢复另有[成功记录](evidence/sphinx7590-checkpoint-20261009.json)，Xarray第22步恢复见[记录](evidence/xarray6992-checkpoint-20261009.json)。平台执行/状态恢复与模型修复正确性分别判定。

### 调度、恢复及共享页

Prettier七实例GRPO为189个实际动作。BPO/TVCache各84个实际动作、17段，覆盖七条逻辑路径共189逻辑动作，各保存两次、恢复六次；不能把复用的前缀重新算成实际执行。两组持续样本1748/4607，CPU/I/O/RSS/PSS均有曲线；生命周期52.642/85.781秒，guest工具之和46.370/51.660秒。后者包含并发，不能用来代替前者。

BPO两次checkpoint接口合计3.309秒、六次restore请求合计1.586秒；TVCache分别4.182/1.528秒。原生checkpoint离线演示另有保存接口2.830秒、恢复接口0.302秒、导出31.518秒和导入19.228秒，范围互有包含，不相加成端到端时间。

共享物理计数直接从host PFN（物理页编号）集合求并集。1/2/7实例在相同前11步后的guest文件物理量211.07/399.22/582.21 MiB，host镜像层及私有文件驻留5317.24/5330.52/5375.69 MiB。七实例共享镜像层5308.07 MiB只计一次；逐VM相加会多计31848.40 MiB，即31.10 GiB。guest集合这次未发现共享host物理页。此测量排除卷外RAM准备缓存及provider匿名内存，不把两项文件驻留量称为总host内存。

### 监控影响和缓存控制

Prettier五对关闭/开启轻量采样的平均配对变化：guest工具之和+2.40%、重放区间+7.43%、控制器生命周期+6.44%。输入、动作、预期失败、输出及patch保持；顺序已交替，但跨日批次和外部缓存负载仍限制精确归因。该组不含物理扫描，不能套用到精细分析、所有任务或分支调度。不新增5%等验收门槛，也不因非零开销否定已经完成的功能。

Exp2 AgentENV preserve/drop各一次：工具8.426/12.372秒，生命周期28.033/33.593秒；每组32动作、四恢复通过。drop只在guest保存前执行`sync; echo 3 > /proc/sys/vm/drop_caches`，host缓存不清：

| 轮后 | 清除前Cached MiB | 保存时Cached MiB |
| --- | ---: | ---: |
| 1 | 183.61 | 58.62 |
| 2 | 107.46 | 38.94 |
| 3 | 117.03 | 33.70 |
| 4 | 79.93 | 23.98 |

这证明缓存占用确有变化，但不是指定容量上限。drop=3也清可回收slab；两组各一次、顺序执行、后续热状态不同，所以不能直接宣布全部用时差由page cache造成。DAMON（内存访问监测及回收机制）开关同样属于回收策略，而非精确设置缓存容量。在本项目scripts/configs未找到实际写入`memory.high/max`或`quota_sz/quota_ms`的缓存预算实现；即使将来使用cgroup总内存限制，也不能把它解释成专属page cache上限。

## 参数化程度及残留固定逻辑

已经从写死单一运行脚本推进到配置选择任务、后端、rollout、恢复边界、结果目录、物理测量段、shared-cache和root_count；`commands()`明确分派已有controller，非法组合会被拒绝，不会静默替换任务。固定任务输入和原动作哈希是正确性约束，保留它们不是缺陷。

仍影响使用范围的固定项：

| 位置 | 固定内容 | 实际影响 |
| --- | --- | --- |
| `validate_isolated_agentenv.py:SERVICE/IMAGE`；`platform_tasks.preflight()` | 特定历史服务名及本地服务镜像ID | 换服务/新主机即使AgentENV正常运行，也无法直接通过入口检查 |
| `prepare_prettier6604_cache.prepare()`和任务配置 | 已验证的历史卷备份、`.artifacts`输入和完整评测参考 | Git克隆不包含这些镜像、日志及输入；必须准备可迁移包或重新构建 |
| `platform_tasks.TASKS/configuration()` | 三个任务、固定24/29/22边界；多路径仅Prettier | 新任务/边界/调度仍要加适配，尚非通用harness |
| 控制器和采集入口 | 2 CPU/4096 MiB、约100 ms及PSS约1秒 | 当前受支持实验可重复；不能直接用配置任意改变配额或采样频率 |
| 复用学长runner的guest路径 | 部分保留`/workspace/artifacts/exp5/` | 当前显式适配运行已通过，不代表路由仍误入Exp5；命名易误导，新增适配时需核对路径用途 |

README开头的最新结论与较下方旧段落有冲突：仍有“Sphinx完整物理缓存/Xarray物理缓存尚未验证”等旧描述，R1/R2已经补齐。历史日期和结果应保留，但首页当前状态应只指向一份支持表，避免把旧缺口当作当前阻塞。本次只记录问题，没有顺手改写其他人的待提交文档。

## 待运行验证及建议命令

B2、C2已有实际成功证据，E3已有清缓存效果证据，本机不必为了本次审查重复运行。下面分清“复现已有能力”与“补齐未验证能力”。真实命令需要检查现有服务、输入及可用空间，顺序运行，不清host缓存。

| 目的 | 命令或步骤 | 应检查的结果 |
| --- | --- | --- |
| 第二台机器完整部署和运行：待验证 | 先把固定版本、补丁、CLI、镜像/输入准备改为明确流程；再`bash scripts/use-platform.sh deployment --output .artifacts/platform-management/deployment-newhost`；运行`bash scripts/use-platform.sh run configs/platform-prettier6604.json` | 无本机历史服务/路径依赖；实际29步、输出和patch、默认曲线、清理均通过。已有同伴离线报告不等于这一步 |
| 新机器复现恢复：待验证 | `bash scripts/use-platform.sh run configs/platform-prettier6604-checkpoint.json` | 恢复后24步状态/进度/进程保持，只执行25—29；先check，满足固定卷预算再运行 |
| 真正在线Agent采集：随后已验证 | `bash scripts/use-platform.sh collect-demo --agent-config configs/online-agent-glm.json` | 4轮模型决策和3工具，独立检查、冻结及新VM严格重放PASS；模型等待与工具分开，原失败保留。普通collect-demo仍是无模型演示，带agent-config才运行在线模式 |
| 指定缓存预算及用时比较：后续增强，待实现后验证 | 若研究要求在指定预算下持续运行，增加明确guest或host控制层级及预算配置；先一组校准负载确认驻留量，再固定任务/轨迹/rollout/CPU/RAM/镜像/冷热策略比较两个预算 | 目标缓存确有差异，收集物理点和持续曲线；说明匿名内存、slab和I/O变化。当前没有可执行的cache-size选项，不能编造命令；不作为本次E3通过的前置 |
| 重现现有清缓存效果：已有证据，按需使用 | 顺序运行`bash scripts/use-platform.sh run configs/platform-exp2-preserve.json`和`bash scripts/use-platform.sh run configs/platform-exp2-drop.json` | 四轮实际Cached下降及恢复状态保持；每次32 GiB卷要求主机大于58 GiB可用。这不是新的必须补测项 |

需要离线复算已有完整任务时，可以执行：

```bash
bash scripts/use-platform.sh report \
  .artifacts/platform-tasks/20261011T065320Z-468452 \
  --output .artifacts/platform-tasks/acceptance-review-recomputed-new
```

输出目录必须不存在。本次已读到原自动报告与历史独立复算记录，未追加同一完整复算。固定Exp1另可用`python3 -B scripts/verify_initial_acceptance.py`按既定哈希复算，不能把离线PASS说成刚运行了VM。

## 当前推进范围：真实在线Agent

用户2026-10-12确认D1采用“对沙箱运行的影响较小”，部署迁移暂时不考虑。按修订口径复用五对轻量监控证据，不追加开销实验；不改写历史原始结果。此前三项补充工作的当前安排为：

随后在线闭环已完成，见[本轮证据](evidence/online-agent-20261012.json)：173个guest样本，10份报告复算一致，成功资源及RAM缓存回收。下方第1项实现及验证已完成，不再作为验收缺口；新增任务配置与持续会话另列后续。

1. **接一条真实在线Agent工具轨迹，当前优先。** 模型循环运行在host，暴露一个shell执行工具；模型生成命令后调用现有`capture()`在AgentENV执行，将退出码及stdout/stderr返回模型。另存模型标识、任务说明、对话和工具调用关联，最终`freeze()`，再从同镜像新沙箱`replay()`。模型请求用时与工具用时分开，离线重放不调用模型。先完成一个短文件处理任务的真实采集/冻结/重放及默认曲线，再接入Prettier；不先引入RL训练、多实例或新调度。
2. **监控口径已明确，沿用证据。** 默认性能模式保留异步曲线；精细物理分析作为独立配置，显示暂停和分析成本。已有开销量化和未测调度标记继续保留，不将本次口径修改冒充新的运行结果。
3. **部署迁移暂缓。** 服务名、镜像和输入来源配置化、新服务器真实重放及恢复仍留作后续，不是当前在线接入的前置。

后续研究增强：需要指定缓存预算时再补实际控制方法和容量校准；需要swap研究时增加持续字段。现有清缓存对照可继续使用，但性能统计应控制顺序、冷热状态及slab混杂，另行约定重复轮数。这些不是本次初步交付的新增前置条件。

不把Kubernetes、Prometheus、ARIES、18种调度组合或自行增加的性能阈值作为上述交付条件。本次没有暂存、提交或推送，原有工作区修改保持。
