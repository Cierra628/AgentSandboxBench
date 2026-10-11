# AgentSandboxBench

**本机第一版已达到初步交付条件。** 完整重放、指定边界恢复续跑、内存与时延报告及采集影响验证均有实际证据；新增各沙箱CPU/I/O和host进程指标也已验证。验收范围、证据及后续事项见[交付判定](docs/records/CLOSEOUT_STATUS.md#2026-10-11本机第一版可以初步交付)，运行方法见[使用说明](docs/USAGE.md)。

2026-10-11更新：统一入口已完成Prettier #6604的29步重放和第5/24/29步后的物理缓存测量，分别为210.35/243.56/138.29 MiB；自动报告与离线复算一致，见[变化图](docs/records/evidence/prettier-physical-cache-20261011.svg)和[本轮证据](docs/records/evidence/platform-task-entry-cache-20261011.json)。此前CPU/I/O/RSS/PSS监控的五对验证也已完成，平均配对用时变化为工具+2.40%、重放+7.43%、控制器生命周期+6.44%；该五对未带精细物理分析，两批不混合，范围见[监控报告](docs/records/PRETTIER_RUNTIME_MONITORING.md)。Sphinx旧未完成批次保留。


共享计数校准及Prettier单/双/七实例物理比较已完成：完整动作分别29/50/189个，持续曲线和清理通过。七实例host共享镜像层只计一次，直接相加会多计31.10 GiB；guest文件缓存未发现共享物理页。三配置报告独立复算一致。见[共享缓存记录](docs/records/SHARED_TASK_CACHE.md)。

Sphinx和Xarray现也各完成35步及三点物理缓存测量，持续曲线、独立复算和清理通过；Xarray两点各有最多4 KiB未知量，原模型失败及退出码诊断规则保留。三任务统一测量流程的范围和数据见[完整任务报告](docs/records/PYTHON_TASK_CACHE.md)。

新增本机部署检查、原生模板/checkpoint/实例查询及结果保留入口；4次新工具调用的实际采集、冻结和新沙箱重放通过，原失败退出码及最终文件保持。两实例持续曲线及清理通过，离线报告一致。使用方式见[工具采集与管理](docs/USAGE.md#部署检查资源查询和结果保留)。原生checkpoint离线包及新服务重新加载也已通过，保留进程、匿名内存、打开文件和步骤进度，见[保存与加载](docs/USAGE.md#原生checkpoint离线保存与重新加载)。在线模型连接、任务归档扩展和新机部署为后续工作。

单机 Agent 沙箱实验平台。复用官方 AgentENV 和学长固定的 IncrementalDAX_moti / TrEnv-X，执行冻结工具轨迹、指定边界恢复续跑，并记录正确性、内存及阶段耗时。固定版本见[源码配置](configs/source-revisions.json)。

## 平台日常使用

现有服务上的配置检查、完整重放、指定步骤保存/恢复/续跑和自动报告统一到：

```bash
bash scripts/use-platform.sh check
bash scripts/use-platform.sh run
```

默认AgentENV、Prettier #14400和第022步保存；版本2配置现可选择Prettier #6604、Sphinx和Xarray及现有调度，原控制器证据与新入口验证范围见[任务支持表](docs/USAGE.md#任务配置和支持范围)。[使用说明](docs/USAGE.md)列出配置、轨迹格式、其他后端、独立复算和资源要求。AgentENV新组合已实际通过；TrEnv-X新恢复组合要求超过60 GiB空闲空间，不降低保护线。此入口不安装系统或接入在线模型，不替代下方固定验收复算。 新增[固定容量配置](configs/platform-exp1-isolated.json)，两后端顺序使用临时卷；AgentENV完整/恢复及TrEnv-X完整重放已有成功证据；TrEnv-X第022步封存错误已修复，独立恢复复验和报告通过。原组合FAIL保留，见[当前状态](docs/STATUS.md)。

## 当前交付：本机单条 Exp1 初步验收

Prettier #14400 的完整重放、指定步骤恢复续跑，以及明确范围的内存/时延/采集影响三项已验证通过。证据覆盖两后端各五对、20次正式重放，以及原始恢复记录。**该结论仅限已准备好的本机及单条冻结轨迹，不代表PPT三个任务、通用部署或多VM共享收益完成。**

PPT中的Prettier #6604已有29步重放、第24步VM保存/恢复/续跑，以及修正分支输入后的七实例GRPO、BPO和TVCache功能验证。BPO和TVCache两处保存及六个恢复分支的文件、进度和探针进程连续性通过；平台重放与恢复通过；七条路径的官方测试均为0通过、2失败，原模型补丁没有完整修复任务。此任务新增监控的五对影响验证及指定步骤物理缓存分析已完成；精细分析需暂停VM，其成本另列，见[最新进展](docs/STATUS.md)。

Xarray #6992已完成35步完整诊断、第22步完整VM保存/恢复及原第23–35步续跑：99份文件、进度与探针连续性通过，最终patch一致；957项任务测试仍945通过、12失败、0跳过，模型任务未通过。一条管道的0/141差异按已同意的精确诊断规则记录，严格重放仍FAIL。报告和曲线已独立复算，物理缓存、新采样开销及长期checkpoint重载尚未验证，见[当前状态](docs/STATUS.md)和[恢复证据](docs/records/evidence/xarray6992-checkpoint-20261009.json)。Sphinx #7590已通过35步严格重放、第29步完整VM保存/恢复及原第30–35步续跑：124份文件、进度和进程探针连续性通过，最终patch与完整重放一致；25项任务测试仍24通过、1失败、0跳过，模型任务FAIL。报告及曲线两次复算一致，成功卷、快照及RAM缓存已回收；完整轨迹物理缓存及稳定采样开销仍未验证，见[恢复证据](docs/records/evidence/sphinx7590-checkpoint-20261009.json)。

Sphinx前6步的新短验证已测得暂停点file-LRU物理量94.01 MiB，类型及host映射未知零；三个采集配置的输出和状态一致。精细分析8.633秒、VM暂停4.148秒，周期采样短对照不足以判断稳定开销，见[实测证据](docs/records/evidence/sphinx7590-short-cache-20261009.json)。当前普通实验无需逐次确认空闲窗口，仍检查容量并保留失败记录。

轻量采样现已接入各任务默认入口，按实际沙箱ID持续记录内存并生成page cache和组成曲线。三任务完整执行期间的曲线及Prettier七实例GRPO通过，覆盖等待和任务测试；Xarray仍为完整诊断通过、严格重放FAIL。同步阶段调用已优化，Sphinx新五对和七实例复验保持任务及采样结果；命令计时以外的配对额外剩余用时均值由2.451秒降至0.432秒。一次conda/pip慢调用使整体开销百分比仍不稳定，不宣称零性能影响。此前短恢复采样也通过，见[持续采样记录及曲线](docs/records/SANDBOX_MONITORING.md)。

短诊断已确认当前环境中pip联网版本检查增加等待：仅在诊断子进程关闭检查，原命令用时由1.78–1.99秒降至0.60–0.61秒，输出和退出码不变。历史6.395秒慢调用未复现，正式轨迹与默认环境保持原样；报告已增加全部35步的波动统计，见[诊断证据](docs/records/evidence/sandbox-runtime-command-diagnostic-20261009.json)。

现已接入每个沙箱的CPU、块设备I/O及对应宿主机VM进程CPU/I/O/RSS/PSS曲线。四VM、五会话的短恢复及两个子实例原第7–13步严格续跑通过；正常4 GiB Prettier七实例189个动作和全部新增曲线通过。Sphinx完整新增指标及两对开关已通过，但正式五对因关闭采样时第11步find返回1而中断。旧未回收采样器问题已修复，本轮另证明普通进程退出也能触发find错误，两者不混称；新版稳定采集开销仍待验证，见[最新记录和曲线](docs/records/SANDBOX_MONITORING.md)。


2026-10-10重新核验：固定Exp1三项复算通过，20次正式和4次历史记录一致。当前新增功能曲线、恢复分支和七实例已有证据，新增采集影响仍部分完成，不能沿用旧开销百分比。Sphinx获批的精确管道规则不覆盖find退出1；短诊断证据也不改变原严格失败。未暂存，下一步先讨论新版开销的测量任务，见[验收复查与计划](docs/records/ACCEPTANCE_REVIEW_20261001.md)。

guest指沙箱内部，host指宿主机。精细分析会暂停VM并增加用时；历史guest曲线只覆盖runner窗口，新入口从采集器安装后持续记录，保存/恢复周围的空缺明确保留。物理指标为暂停点file-LRU（内存回收队列中的文件页）及另列的指定backing缓存，不覆盖完整DAX内容或全部共享服务归属。

- [固定验收交付及复算](docs/records/ACCEPTANCE_REVIEW_20261001.md#固定初步验收交付)：判定、指标、证据和不支持的范围。
- [平台当前状态](docs/STATUS.md)：通用能力和PPT实验的未完成项。
- [下一步计划](docs/EXPERIMENT_PLAN.md)：不重复已通过的Exp1长测。
- [协作分工](docs/COLLABORATION.md)：后端、控制器、任务与采集分析。

从具备本地原始数据的工作区复算：

```bash
bash scripts/verify-initial-acceptance.sh
```

入口最多运行240秒；只调用离线审计，禁止审计阶段的Python文件写入和子进程，核对已有输入的元数据不变。每次输出到新建的`.artifacts/initial-acceptance-delivery/<run_id>/`，拒绝已有目录。`status=PASS`及退出码0表示复算与固定证据一致；缺数据、超时或缺少最终结果都不能计通过。

GitHub不包含运行时凭据、原始日志、RAM/PFN数据或镜像，只有公开摘要不能完成独立复算。全新服务器部署未验收，复算入口不会安装依赖或准备服务；真实运行入口和前提见[收尾记录](docs/records/CLOSEOUT_STATUS.md)，合作者可按[另一台机器验证说明](docs/SECOND_MACHINE_VALIDATION.md)验证部署和单实例闭环。

仓库：<https://github.com/Cierra628/AgentSandboxBench>。本轮不自动暂存、提交或推送。下方保留旧版README，所有旧日期结论均按当时范围理解。

<details>
<summary>历史状态与计划（点击展开；不代表当前结论）</summary>

## 本机第一版验收（2026-10-07）

已准备好的本机、单条Prettier #14400冻结轨迹范围内，完整重放、指定步骤恢复续跑，以及限定范围的内存/时延/采集影响三项初步验收通过。最终方案有两后端各五对、20次成功运行和原始数据独立复算；精细分析有明显耗时，测量范围、恢复语义及后续研究缺口均保留。详见[最终验收](docs/records/ACCEPTANCE_REVIEW_20261001.md#2026-10-07本机exp1第三项验收收尾)。不代表PPT三个任务、多VM共享收益、在线轨迹或全新服务器部署已完成。

2026-10-07 已修正内核内部LRU位号混用，并完成两后端短校准、完整重放、扫描后官方任务验证、原VM恢复、独立复算与清理。准确file-LRU物理量为223.969/37.031 MiB，host未知零；撤回旧115.953 MiB完整缓存判断。两后端DAX与普通缓存范围不同，该差值不代表内存收益。第三项仍部分完成，完整文件归属和新方案匹配开销待补。见[当前状态](docs/STATUS.md)和[错误修正及实测报告](docs/records/FILE_PHYSICAL_MEMORY.md#2026-10-07内核位号修正与两后端同范围复验)。以下为历史进展。



2026-10-06 新进展：AgentENV完整重放后，暂停原VM并读取准确内核数据，核实全部SystemRAM中29,684个file-LRU页的host物理量115.953 MiB；普通文件91.223 MiB、块设备24.633 MiB和未知类型0.098 MiB分开报告。26步、扫描前后官方四断言、恢复计数、原始页独立复算和清理通过。第三项仍部分完成：全任务内容归属、非LRU范围和TrEnv-X匹配方法待补；新分析不是零开销，具体阶段成本已记录。见[当前状态](docs/STATUS.md)、[物理范围及复现命令](docs/records/FILE_PHYSICAL_MEMORY.md#2026-10-06暂停后的内核文件缓存归属与物理页核对)。以下日期段落为历史进展。

单机 Agent 沙箱实验平台：对冻结的真实工具调用轨迹进行可复现重放，在指定步骤执行 checkpoint/restore，并记录任务正确性、内存和阶段时延。AgentENV 是主要 baseline，固定 TrEnv-X / Incremental DAX 实现作为对照。

第一版本机 Exp1 基础闭环已完成：两个后端从新建代码目录完整重放；AgentENV 第 013 步保存并恢复进程，TrEnv-X 第 022 步继承已修改文件并续跑；官方任务专属评测、内存曲线、阶段耗时及清理核对通过。**范围为已有环境、单条冻结轨迹和已约定的宿主机采样；全新机器部署、完整生命周期开销及跨后端共享内存收益尚未验收。**

按完整要求检查，第三项初步验收仍为部分完成。AgentENV 此前 100 ms 双采集对照的任务窗口增幅为 2.02%（95% 区间 0.04%～4.04%）。2026-10-02 新 TrEnv-X 八次短验证和五组 60 次正式对照全部通过正确性、清理及复算；双采集任务窗口增幅为 1.12%（−0.88%～3.15%），控制器全程为 0.58%（0.08%～1.08%）。相同命令的关闭/关闭窗口也有 0.69%（0.04%～1.33%）差异，但后续分组交换标签的尾部比例为 22.3%，不足以证明稳定偏差；工具波动仍未解释，不能把变化全部归因监控。两后端配置不同，不作公平速度比较。PPT 没有性能门槛，不能据此宣称无影响或第三项全部通过。

2026-10-06 最新：两后端原 VM 暂停扫描的短验证及恢复后任务、官方断言、独立复算和清理通过，暂停期间选定页映射稳定且未知为零。实际暂停4.08/6.56秒，属于有中断成本的内存分析。恢复后仍新增24/2页，区间和全任务内容归属保持部分完成，第三项整体尚未全部通过。此前各五对扫描开销对照已完成，不重测。详见[本次范围、数据和下一步](docs/records/FILE_PHYSICAL_MEMORY.md#2026-10-06两后端原-vm-暂停期间的缓存核对通过)。

2026-10-02 内部计时校准及四次新诊断通过：TrEnv-X runner 调用平均 23.78 秒，Git 准备和整理各约 10.2 秒，工具步骤之和约 1.99 秒。初始 reset 主要在等待，023/024 是工具波动的主要位置；当时原因尚未确认，后续身份诊断已定位 Git 的 DNS 等待；这些诊断不替代监控开关对照。详见[阶段报告](docs/records/EXP1_METRICS_AUDIT.md#2026-10-02trenv-x-内部计时校准和四次诊断)。

2026-10-05 已确认 Git 身份生成的 DNS 等待，固定身份重放也已通过。最新生命周期诊断的 guest 短校准和五次完整重放均通过：采集器启动/停止平均约 76.5/6.9 毫秒；四次控制器调用余量约 1.15–1.22 秒，尚不能归于网络或 SDK。新诊断开启采集的窗口差值为 −2.44%，与上批 +23.83% 方向相反，工具波动会掩盖采集成本，不能判为监控无影响。已分别报告冻结轨迹窗口和包含任务测试的 host 峰值；第三项仍部分完成，旧统计保留。详见[分段结果、曲线和限制](docs/records/EXP1_METRICS_AUDIT.md#2026-10-05guest-执行和采集生命周期的分段诊断)。

2026-10-05 随后的固定计算量采集对照已完成：一个 VM 内先四次短验证，再 32 次正式调用，最后完整 Exp1 功能检查，全部通过。关闭 host 周期采样后，固定计算本身平均 +0.078%，整个 guest 执行 +4.621%（80.817 毫秒），采集启停合计约 82.443 毫秒；主要差值位于启停范围。结果仅覆盖这一合成负载，不能代表真实任务或恢复阶段开销。第三项仍部分完成，接下来补文件页计数范围和归属。详见[结果、原始证据与限制](docs/records/EXP1_METRICS_AUDIT.md#2026-10-05固定计算量的-guest-采集对照)。

2026-10-05 已确认修改源码来自可写层，并通过私有 KVM 注册捕获与命名空间隔离验证。后续两次完整 Exp1 和官方四断言通过，但新的物理测量分别在探针清单和覆盖层设备号检查处失败；修复及 18 项回归、实际日志的离线来源核对通过，尚未完成修正版真实扫描。第三项仍部分完成，没有新增 host 物理缓存计数，历史 16/36 KiB 不变。失败归档和日志已保留，资源清理通过；空闲约 38.00 GiB，低于单次 40.125 GiB 启动条件。下一步补足空间后只复验一个 VM。详见[失败证据、修复与接续条件](docs/records/FILE_PHYSICAL_MEMORY.md#2026-10-05私有-kvm-捕获与可写文件测量的失败边界)。

两后端同一组三个指定文件的归属与内容核对通过，文件哈希一致：AgentENV 为 9 个文件缓存页，TrEnv-X 为 8 个文件缓存页加 1 个内容一致的匿名副本，各计 36 KiB。这只代表声明文件的驻留映射；完整任务文件准确归属、跨 VM 去重和未归因内存仍有限；最新恢复组合及扫描影响已有独立数据。详见[当前状态](docs/STATUS.md)和[最新采集报告](docs/records/EXP1_METRICS_AUDIT.md#2026-10-02trenv-x-持续采集对照)。

- [当前进度与验收缺口](docs/STATUS.md)：项目状态的统一入口。
- [第一版收尾与复现](docs/records/CLOSEOUT_STATUS.md)：四次新目录运行、官方任务评测、恢复语义、图表及组合入口。
- [TrEnv-X Exp1 验收](docs/records/TRENVX_EXP1.md)：26 步完整重放、第 013、022 步文件恢复续跑及正确性证据。
- [TrEnv-X 服务恢复验证](docs/records/TRENVX_SERVICE_SMOKE.md)：完整服务 smoke、复制抛错/子进程超时恢复、大文件复制缺陷及修复证据。
- [Exp1 指标与归档](docs/records/EXP1_METRICS_AUDIT.md)：两后端指标审计、阶段耗时、归档清理；AgentENV 与 TrEnv-X 各五组比较通过各自指定范围内的 5% 监控开销规则。
- [文件内容物理页](docs/records/FILE_PHYSICAL_MEMORY.md)：已复现冷页读取产生匿名副本；第 022 步恢复的完整 Exp1 已通过单个封存源码的文件页与匿名副本采集，任务级共享收益尚未判定。
- [本轮平台进展](docs/records/PLATFORM_PROGRESS_20260926.md)：原有能力、本轮改动、实际验证和集成要求。
- [协作分工与接口](docs/COLLABORATION.md)：后端、控制器、任务和采集分析的交付边界。

## 平台目前完成了什么

| 部分 | 已有能力与本轮交付 | 尚未完成的验收 |
| --- | --- | --- |
| 沙箱运行与轨迹重放 | 两后端 Exp1 的 26 步冻结重放、预期失败和独立 SVG 检查通过；manifest、初始 HEAD 和最终 patch 一致。 | 更多任务、整个 Prettier 测试集及新 trajectory 的自动生成。官方任务专属评测已通过。 |
| checkpoint/restore 管理 | 两后端第 013 步保存并重建续跑通过；TrEnv-X 还在第 022 步保存并继承已修改源码，复制抛错/子进程超时后父 guest 可继续。 | 其他服务异常路径及更多文件状态。TrEnv-X 不恢复任意父进程。 |
| 指标采集与分析 | 两后端阶段耗时、内存曲线及清理可复算；持续采样、TrEnv-X 当前恢复组合和两后端整沙箱分析影响均有独立对照。命名文件及整 guest file-LRU/backing 的物理观测已验证。 | 缓存变化区间、全任务物理内容及未分类内存归属仍有限；多 VM 共享收益和公平后端比较未完成。精细分析有明确成本。 |

历史 AgentENV、DAX 和 upper-layer fixture 结果见[阶段交接记录](docs/records/PLATFORM_HANDOFF.md)及[收尾记录](docs/records/CLOSEOUT_STATUS.md)。它们与本轮普通用户隔离验证分别记录，不能用 fixture 的成功证明完整 TrEnv-X controller 已通过。

## 当前可运行的短验证

从已准备好环境的仓库根目录执行：

```bash
bash scripts/41-verify-trenvx-server.sh
```

2026-09-26，隔离验证复跑 `20260926T091827Z-1959097` 为 PASS：固定补丁、两个 Python SDK/CLI、8 个 Go 契约（含 race）、4 个 Python 契约、实际 envd 包检查和编译，以及无网络 QEMU guest 中的真实 cgroup/启动路径测试全部通过。父 shell 退出后的 setsid 后代归属、冻结停止写入、解冻继续及定向清理已覆盖。

`status=PASS` 表示这套短检查全部通过；`stage=isolated-guest` 表示最后完成的阶段；`result_dir` 是本次日志位置。该入口不会部署完整服务或重放真实任务，也没有验证完整文件 checkpoint/restore。详细阶段解释见[本轮平台进展](docs/records/PLATFORM_PROGRESS_20260926.md)。

完整服务入口为 `bash scripts/43-build-trenvx-service.sh` 和 `bash scripts/44-smoke-trenvx-service.sh`，复用本机已有模板，要求 root 及文档列出的依赖。真实重放入口为 `bash scripts/45-replay-trenvx-exp1.sh`；完整运行及第 013、022 步恢复模式均已通过，命令与范围见 [Exp1 验收记录](docs/records/TRENVX_EXP1.md)。

TrEnv-X 运行结束后默认用本机 `pigz` 将本次私有镜像 `data/` 压缩、校验后移除，保留归档与轨迹、样本和日志；可用 `--archive-compressor gzip` 选择旧方式，或用 `--keep-data` 保留解包镜像。入口设有空间门槛、写入互斥和每 0.25 秒的空闲空间检查；达到 20 GiB 停止本次受保护进程组。这是轮询保护，不能替代共享文件系统的硬配额。归档仅在本机，不等于异机备份。当前磁盘余量与保留边界见[指标记录](docs/records/EXP1_METRICS_AUDIT.md)。批量前可运行 `python3 scripts/plan_trenvx_storage.py --pairs 5` 只读估算所需空间；空间不足返回退出码 2，配对入口也会在每次运行前检查剩余整批预算。

## 固定源码与复现边界

本仓库管理脚本、配置、后端实现、可复现补丁和文档。AgentENV 与 IncrementalDAX_moti 的独立源码目录、运行时依赖/凭据及 `.artifacts/` 原始数据被忽略，不随 Git 上传。版本固定于 [`configs/source-revisions.json`](configs/source-revisions.json)，上游改动保存在 `patches/`。

```bash
# 获取固定源码并应用平台基础补丁。
bash scripts/14-fetch-research-sources.sh
# 应用 TrEnv-X 后端及 controller 补丁。
python3 scripts/40-apply-trenvx-task-cgroup.py
# 准备两个独立 Python 客户端环境。
bash scripts/42-prepare-python-clients.sh all
```

运行 `41` 还需要兼容 guest kernel、静态 BusyBox、Go/gcc/QEMU 和固定 aenv CLI；复现前提和客户端准备命令见[平台进展记录](docs/records/PLATFORM_PROGRESS_20260926.md)。这些入口尚未完成全新机器的完整服务复现验收。

既有 AgentENV 入口为 `bash scripts/13-run-exp1.sh`、`bash scripts/16-run-exp1-checkpoint.sh` 和 `bash scripts/37-closeout-exp1.sh`。它们依赖已部署服务、私有凭据及配置中的服务结果目录，运行前需对齐当前实例；不能直接沿用历史 `.artifacts/` 路径。后端接口与恢复边界见[TrEnv-X 后端记录](docs/records/TRENVX_TASK_CGROUP_BACKEND.md)。

本仓库 GitHub 地址为 <https://github.com/Cierra628/AgentSandboxBench>。

</details>
