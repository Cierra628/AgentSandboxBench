# 当前状态

截至 2026-09-28。这里是项目状态的统一入口；`records/` 保存各阶段的详细证据。原始日志、镜像和样本保存在各次运行的本机 `.artifacts/`，不随 Git 上传。所有命令从仓库根目录执行。

**当前结论：TrEnv-X Exp1 的 26 步真实重放及第 013、022 步文件恢复续跑均已通过单次验收；实际服务的复制抛错和复制子进程超时两种受控恢复测试也通过。19 次运行的离线指标审计及新五对 off/light 采集通过；五对差异为 −3.25% 至 +2.65%，每对第二次运行更快，存在顺序相关波动；5% 监控开销与跨后端性能比较尚未判定。** 新增完整重放和第 022 步恢复两次诊断均通过，已实际核对虚拟机进程的资源组并记录 PSS，生成内存曲线和部分阶段耗时图。此前已修复模板管理器在非 reflink 回退路径下静默截短大镜像的问题。

## 平台三部分的进展

| 部分 | 原有平台与历史验证 | 本轮新增交付 | 尚未完成 |
| --- | --- | --- | --- |
| 沙箱运行、轨迹重放 | AgentENV 官方 Python 示例；Exp1 26 步的输入哈希、动作顺序、预期失败、patch/oracle 及针对性 SVG 功能检查通过。 | TrEnv-X Exp1 单次完整重放通过；manifest、初始 HEAD 和 patch 与已有 AgentENV 原始数据一致。 | 更多任务、官方完整评测及新轨迹自动生成。 |
| checkpoint/restore 管理 | AgentENV 第 013 步后保存并续跑 014–026，patch 一致和 guest 进程身份连续；受控 upper fixture 通过。 | TrEnv-X 实际 controller 在第 013、022 步保存文件状态，删除父实例后恢复并续跑；第 022 步已修改源码的哈希与 diff 保留。复制抛错及子进程超时后，CH 恢复、guest 解冻、父任务继续。 | 其他服务异常路径、打开文件、跨服务重启和远端持久化；不能宣称任意父进程恢复。 |
| 指标采集、分析 | guest/host 内存样本、事件、正确性和时延采集；物理页/DAX 已知负载校准及 timing_schema=2 离线审计已有记录。 | 19 次 Exp1 原始数据审计通过；五对采集完成。新增两次诊断确认进程归属，记录 PSS、首个工具成功时间及保存/恢复耗时，已生成图表。 | 两后端统一的 VMM/PSS 采集、任务中文件内容物理量、完整阶段计时、跨后端公平收益和监控开销统计；暂定 5% 目标未判定。 |

TrEnv-X 的恢复属于文件状态继承：新 guest 使用封存文件层和选定 runtime 文件；它不恢复任意父 shell、后台进程、打开 FD 或完整父 VM 内存。AgentENV 历史进程连续性证据不能移用为 TrEnv-X 的恢复保证。

## 后端实现与实际验收

早期隔离验证使用 Python 3.12.3、Go 1.22.2、QEMU 8.2.2 和 6.1.134 guest kernel，固定源码/补丁、SDK/CLI、envd 编译及隔离测试通过。后续完整服务二进制使用固定 Go 1.23.0 构建；新版 envd 已装入私有模板并通过下述服务 smoke。

入口：

```bash
bash scripts/41-verify-trenvx-server.sh
```

| 检查 | 实际结果 | 证据范围 |
| --- | --- | --- |
| 固定版本/补丁与 SDK/CLI | PASS | 源码 HEAD、补丁一致性、SDK 导入/依赖检查、aenv 版本；没有连接完整服务。 |
| Go 契约 | 8 个 PASS，含 race | 模拟 cgroup I/O，检查所有权、启动门禁、异常解冻和清理。host 内核测试 SKIP 不计为 PASS。 |
| Python 契约 | 4 个 PASS | 模拟 guest/CH，检查捕获顺序、异常恢复、异步取消后等待 worker 和 inventory 定向清理。 |
| 固定 envd 包检查及编译 | PASS | 真实 fork 的 process/terminal/taskcgroup 接入可编译；terminal 包没有独立 host 测试。 |
| QEMU 真实内核/启动代码 | 两个 guest 用例 PASS | shell 退出后的 setsid 后代仍归属；冻结后停止写入、解冻后继续；只清理本 manager 任务；simple HTTP / RPC / PTY 路径。没有启动完整 envd/main、orchestrator 或 CH 服务。 |

首次完整短验收 `20260926T090846Z-1954195` 和复跑 `20260926T091827Z-1959097` 均为 PASS；后者的隔离 guest 为 `20260926T091830Z-1960776`。结果表明短验证可重复执行，不构成服务稳定性或性能统计。阶段解释、实际结果和接口见[本轮平台进展](records/PLATFORM_PROGRESS_20260926.md)。

2026-09-27 将同伴的 PR #1 快进同步至本机 `main`（`f2791b2`），在固定研究源码上应用两个新补丁，并以普通用户运行短验收。第一次运行 `20260927T030954Z-2041849` 在 envd build 阶段失败：Go 的 VCS stamping 试图读取 root 持有、普通用户不可读的 Git index；此前的补丁、SDK、契约和包检查通过，隔离 guest 尚未运行。验证脚本的 envd build 增加 `-buildvcs=false`，版本仍由 `environment.json` 单独记录。复跑 `20260927T031252Z-2057457` 全部 PASS，包含 envd 编译和隔离 QEMU guest 中的真实 simple HTTP、RPC、PTY 启动与任务冻结/清理。原始证据见本机 `.artifacts/trenvx-server-validation/20260927T031252Z-2057457/` 和 `.artifacts/trenvx-task-cgroup/20260927T031301Z-2060830/`。这仍未验证完整 controller 服务、DAX 文件层恢复或真实 trajectory。

完整服务运行 `20260927T034748Z-2077790` 为 PASS：真实 template-manager/orchestrator/envd/CH 和 controller 捕获、封存、恢复路径已执行，父子文件修改隔离，任务/实例/服务/cgroup/loop 清理通过。独立 namespace 内使用既有模板的私有副本，未验证从镜像 digest 开始的全新部署。详情、三个失败尝试和复制缺陷回归见[服务 smoke 记录](records/TRENVX_SERVICE_SMOKE.md)；原始证据位于 `.artifacts/trenvx-service-smoke/20260927T034748Z-2077790/`。没有增加真实轨迹或性能样本。

随后 Exp1 完整重放 `20260927T110632Z-2094910`、第 013 步恢复续跑 `20260927T110838Z-2095712` 及第 022 步恢复续跑 `20260927T112826Z-2098756` 均 PASS，原始数据同在 `.artifacts/trenvx-service-smoke/`。输入哈希、26 步顺序、预期失败、最终 patch、独立 SVG 检查及清理通过；恢复后的前缀记录与 baseline tree 原样保留。第 022 步已修改源码在新 guest 中保持原哈希与 diff。三次使用同一 runner，关闭内存采样。详见 [TrEnv-X Exp1](records/TRENVX_EXP1.md)。

复制阶段故障恢复 `20260927T113837Z-2101631` 为 PASS：在真实服务的 guest 冻结、CH 暂停后注入一次 host upper 复制错误，checkpoint 失败且未生成模板；CH 恢复 Running，guest 解冻，父任务继续写入，后续工具可执行，资源定向清理通过。见[服务 smoke 记录](records/TRENVX_SERVICE_SMOKE.md)。

超时首次尝试 `20260927T115731Z-2104794` 为 FAIL：稀疏 upper 在 20 ms 限额内复制完成，未触发预期超时。改用无限输入注入后，完整服务运行 `20260927T123214Z-2113498` 为 PASS：复制子进程超时且回收，checkpoint 未完成，CH 恢复、guest 解冻、父实例继续，资源清理通过。它不等于真实 upper 或磁盘 I/O 自然超时。经用户授权，两个测试脚本失败运行的私有 `data/` 已逐个归档校验后移除，日志仍在；合计净释放约 16.3 GiB。详见[服务 smoke 记录](records/TRENVX_SERVICE_SMOKE.md)。

离线指标审计检查六次成功运行的输入、顺序、失败集合及最终 patch 一致。新 TrEnv-X 轻量采样重放 `20260927T133457Z-2124323` 为 PASS，记录 49 个本次沙箱父 cgroup 的 host 样本；guest、VMM PSS 和文件内容物理驻留未测。样本最大值不代表瞬时峰值或整机内存收益，采样读取耗时也不能替代 A/A 监控开销验证。该运行约 11.1 GiB 的私有镜像已归档校验为约 2.7 GiB 后移除原目录；归档后六次审计仍 PASS。后续 TrEnv-X 运行默认执行同一归档清理，失败则保留未删源数据，`--keep-data` 可保留解包镜像。详见[Exp1 指标口径审计](records/EXP1_METRICS_AUDIT.md)。

进一步将已有完整重放和第 013 步恢复运行的私有镜像校验归档，归档后离线审计仍 PASS。两对 TrEnv-X off/light 交错 smoke `20260927T150515Z-2146099` 的四次运行均正确且自动归档通过，light/off runner 调用耗时变化分别为 +2.46% 和 −2.80%，不能判断 5% 目标。每次运行归档约需 456 秒、留存约 2.69 GiB，归档吞吐和累计空间是继续十对前的实际瓶颈。详见[指标记录](records/EXP1_METRICS_AUDIT.md)。

在另一条已完成运行上测试 `pigz-fast` 无损归档：gzip 检查、tar 逐项比对、哈希及源目录清理通过，总耗时约 200 秒、归档约 2.94 GiB。两种方式使用不同输入，不能作严格压缩性能 A/B；新运行默认采用明确记录的 `pigz-fast`，也可指定旧 gzip。随后第 022 步恢复运行 `20260927T160728Z-2165849` 已验证新默认的自动接入：重放、文件恢复、清理及归档全部 PASS。新增运行时磁盘保护每 0.25 秒检查空闲量，达到 20 GiB 停止本次进程组；同一入口和独立归档器共用写入锁。四个受保护阶段的观测最低空闲量为 34.89 GiB，归档后约剩 50 GiB。低空间终止仅在注入读数的临时测试中验证；轮询不能保证其他工作负载突然写满共享磁盘时仍有余量。

## 历史实验结论与边界

以下是既有实验记录中的结果，本轮没有重新运行这些完整服务实验：

- 官方 AgentENV Python 示例通过，使用固定预构建镜像与 `e2b 2.26.0`；不等于模型采样实验或学长 AgentENV fork 构建通过。
- Exp1 的完整重放与指定步骤恢复、独立 SVG 属性用例和上游测试子集通过；仍缺官方任务专属完整评测、更多任务和其他 checkpoint 边界。
- AgentENV snapshot 双实例的已知文件页共享/写入隔离，TrEnv-X DAX 只读层共享、OverlayFS 整文件 copy-up 和两代封存重建已有校准；两后端写入语义不同，尚无任务级公平内存收益结论。
- 在线复制 ext4 upper 后直接 `ro,noload` 封存曾遗漏 fsync 更新；受控 fixture 通过后，同一捕获协议如今已在完整服务的小文件 smoke 中通过；复制抛错与子进程超时的受控恢复也通过，其他异常路径仍待验收。
- 监控正确性/清理已做 10 对 A/B、10 对 A/A 和 10 个交错块、共 80 次完整重放；A/A 波动与 A/B 同量级，5% 目标未判定。schema=2 的 8 次调用审计通过，尚未按新口径开展统计验收。

历史“下一步”描述的是各记录完成时的状态。任务 cgroup 和 controller 代码补丁如今已交付，不能继续将其视为尚未实现；同样不能把代码或 fixture 的 PASS 写成完整服务通过。历史原始数据没有随 Git 同步，学长历史 raw 尚未取得；全新机器端到端复现仍未验收。

## 下一步顺序

1. 在已有正确性通过的任务上补充虚拟机内部内存与文件实际驻留页测量。现有两次诊断已核对虚拟机进程归属和 PSS，但资源组内存与进程内存的差额尚未逐页查明；两类数据分别报告，不能相加。当前磁盘约余 66 GiB，新运行须重新核验空间并保留 20 GiB 停止线。
2. 五对关闭/开启采样的比较已经完成，五对中第二次运行均更快。若以后需要判定 5% 开销，先约定包含相同模式对照、能检查运行顺序和时间变化的新实验方案，再估算归档空间；现有五对不自动扩轮。
3. 补充任务专属完整测试、其他轨迹与保存步骤，继续明确 TrEnv-X 只继承文件状态的范围。
4. 补齐从固定镜像版本开始的新模板准备流程；当前入口依赖已有模板，全新机器从零复现尚未验收。
5. 统一两后端的计时、内存和缓存设置后，再进行同一任务的公平比较；之后推进 GRPO/BPO/TVCache 调度实验。当前调度形状不代表已经训练策略。

## 证据导航

| 主题 | 详细记录 |
| --- | --- |
| TrEnv-X 真实 Exp1 与第 013、022 步文件恢复 | [Exp1 验收](records/TRENVX_EXP1.md) |
| 19 次 Exp1 审计、五对采集、诊断图表与磁盘保护 | [指标审计](records/EXP1_METRICS_AUDIT.md) |
| 完整服务文件恢复与大镜像复制修复 | [服务 smoke](records/TRENVX_SERVICE_SMOKE.md) |
| 本轮平台交付、用户复跑及完整服务集成缺口 | [平台进展](records/PLATFORM_PROGRESS_20260926.md) |
| TrEnv-X 后端任务组接口、固定补丁及隔离验证 | [后端交付记录](records/TRENVX_TASK_CGROUP_BACKEND.md) |
| 历史功能收尾、独立测试、计时及 cgroup fixture | [收尾记录](records/CLOSEOUT_STATUS.md) |
| 历史 Exp1、监控 A/B/A/A、时延及服务盘点 | [阶段交接记录](records/PLATFORM_HANDOFF.md) |
| 单 VM、snapshot 双实例内存校准 | [VM 文件页](records/VM_FILE_CALIBRATION.md)、[双实例](records/SNAPSHOT_SIBLING_CALIBRATION.md) |
| DAX 与 checkpoint 文件层 fixture | [DAX 底层](records/TRENVX_DAX_PRIMITIVES.md)、[两代封存](records/LAYER_FIDELITY.md)、[在线不一致](records/ONLINE_UPPER_CONSISTENCY.md)、[受控协议](records/QUIESCED_UPPER_PROTOCOL.md) |
| 历史环境迁移与官方示例 | [迁移](../MIGRATION.md)、[cpu-15](records/CPU15_RESULTS.md)、[官方示例](records/KIMI_AGENTENV_EXAMPLE.md) |

固定源码版本见 [`configs/source-revisions.json`](../configs/source-revisions.json)。上游 checkout、运行时依赖/凭据及 `.artifacts/` 不随 Git 上传；修改固定 fork 的可复现补丁保存在本仓库 `patches/`。
