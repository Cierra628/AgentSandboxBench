# 本机平台使用说明

2026-10-09起，用户已持续授权常规实验，不再逐次确认空闲窗口。隔离入口及统一入口可省略`--confirmed-window`和起止时间，默认创建20分钟运行时间范围；这是超时预算，不表示主机独占或其他人不写盘。旧示例的显式时间参数仍兼容；隔离验证脚本如显式设置运行时间而非人工窗口，可加`--automatic-window`。磁盘预算、20 GiB保护量、失败保留及只清理本次资源的规则继续执行。

本入口用于已准备好的实验机，复用固定AgentENV镜像缓存、TrEnv-X模板、CLI和任务测试输入。可选择原服务上的共享盘模式，或容量固定的临时服务模式。当前支持Prettier #14400的26步冻结轨迹；它不是PPT中的Prettier #6604任务，已有冻结轨迹之外，新增工具接口支持实际调用采集、冻结和重放；在线模型接入及全新机器安装另行验证。

Prettier #6604、Sphinx #7590和Xarray #6992现可通过下面的版本2配置接入同一个`use-platform`入口。默认不带配置仍为Exp1。平台重放结果与原模型任务测试结果分开报告；原模型修复不完整，不修改冻结动作或答案。

## 任务配置和支持范围

版本2配置直接调用已有控制器，使用固定容量临时卷，保留每次独立run_id、生效配置、日志、任务原始结果和清理回执。镜像准备在有界RAM中完成，正式执行仍按现有磁盘预算检查；成功临时卷自动回收，失败卷保留。

| 任务与配置 | 后端及调度 | 恢复边界 | 已有运行证据与本轮入口状态 |
| --- | --- | --- | --- |
| `platform-exp1*.json`：Prettier #14400 | AgentENV、TrEnv-X；单路径 | 隔离模式022；共享模式范围见下文 | 原统一入口已验证。AgentENV为完整VM状态；TrEnv-X仅继承文件状态 |
| `platform-prettier6604.json` | AgentENV；单路径 | 不保存 | 原29步完整路径和持续指标已通过；版本2离线检查通过，同一入口的附带物理测量配置已完整运行 |
| `platform-prettier6604-checkpoint.json` | AgentENV；单路径 | 第24步后，完整VM恢复 | 原恢复与续跑已有成功证据；版本2离线检查通过，未重复真实恢复 |
| `platform-prettier6604-grpo.json` | AgentENV；七个新实例 | 不保存 | 原七实例189动作及持续指标通过；版本2离线检查通过 |
| `platform-prettier6604-bpo.json`、`platform-prettier6604-tvcache.json` | AgentENV；原BPO、确定顺序前缀树 | 调度内两处保存和六个恢复分支 | 原调度功能通过；版本2离线检查通过。32 GiB卷要求大于58 GiB主机可用空间，当前可能受预算限制 |
| `platform-sphinx7590.json` | AgentENV；单路径 | 可另配29，但须使用strict策略及完整评测参考 | 原完整路径、恢复与曲线已有证据；此配置明确采用已批准的管道诊断版本2，不能报告严格重放通过 |
| `platform-xarray6992.json` | AgentENV；单路径 | 可另配22，须提供完整评测参考 | 原完整诊断、恢复与曲线已有证据；明确采用已批准的SIGPIPE诊断规则，严格重放状态保留FAIL |
| `platform-prettier6604-cache.json` | AgentENV；单路径、持续采样和精细分析 | 不保存；第5、24、29步后暂停测量 | 版本2真实29步、三点物理测量、自动报告和离线复算通过，见[证据](records/evidence/platform-task-entry-cache-20261011.json) |
| `platform-prettier6604-shared-1.json`、`platform-prettier6604-shared-2.json`、`platform-prettier6604-shared-7.json` | AgentENV；1/2/7个GRPO新实例 | 不保存；相同前11步后全部暂停测量，再继续原后缀 | 三份离线检查通过；1/2/7实例完整任务、物理测量、持续曲线及清理通过，见[共享缓存记录](records/SHARED_TASK_CACHE.md) |

新增`platform-sphinx7590-cache.json`和`platform-xarray6992-cache.json`均已完成新入口真实35步运行、持续曲线、物理测量、报告和清理。Sphinx在第13/29/35步后测量，Xarray在第13/22/35步后测量；Xarray后两点各有一页未知，保存已确认值及最多4 KiB缺口。两个任务均沿用已批准诊断规则，严格重放状态仍FAIL，模型测试原失败保持。详见[完整任务缓存报告](records/PYTHON_TASK_CACHE.md)。

这些调度沿用现有实验语义，不代表RL训练、自动熵选点或线上并发缓存系统。CPU和内存沿用各任务固定配置，目前2 CPU/4096 MiB；采样约100 ms，PSS约1秒。不通过版本2字段擅自改变原轨迹、测试和缓存策略。

```bash
# 配置、冻结输入及资源检查；--offline时不访问服务。
bash scripts/use-platform.sh check configs/platform-prettier6604.json --offline
bash scripts/use-platform.sh check configs/platform-prettier6604-cache.json

# 完整重放，并在三个步骤边界测量物理缓存、自动生成报告。
bash scripts/use-platform.sh run configs/platform-prettier6604-cache.json

# 从保存的原始数据重新生成报告；输出目录必须不存在。
bash scripts/use-platform.sh report .artifacts/platform-tasks/<run_id> \
  --output .artifacts/platform-tasks/report-recomputed
```

版本2的`task_id`、`backend`、`rollout`、`checkpoint_after_step`选择现有适配；`inputs`为准备好的任务输入，`reference`为原任务结果参考，`frozen_export`为修正后的冻结分支包，Python任务另需`cache_backup_directory`。所有输入仍须准备在本机，不是克隆后即可下载完成的部署工具。Sphinx的`replay_policy=sphinx-pipeline-v2`和Xarray的`xarray-approved-sigpipe`显式保留诊断语义；不允许在恢复模式中自动扩大例外。

物理分析是独立测量配置：持续曲线记录逻辑`Cached`、内存组成及CPU/I/O，暂停点分析按实际guest内核和RAM映射核验文件页，并在host物理页编号上去重。普通文件内容与目录等元数据分开报告，三个校准文件页计入结果；不等于全部host镜像缓存或跨VM共享收益。报告同时保存暂停和分析耗时，不将精细分析配置用于轻量监控开销比较。

共享测量使用三份`shared-N`配置，各运行一次。所有实例先完成原`read_repo`及`read_typescript`共11步，全部暂停后测量guest文件页和实际镜像digest对应的host层、私有upper及附加盘，再按host物理页编号去重。shared-1只运行backbone，shared-2运行backbone及early_1，shared-7运行原七条路径；完整动作分别29/50/189个。持续采样保持，暂停前的等待及精细处理时间计入这次配置，不作为原GRPO时延比较。镜像层从验证过的RAM准备缓存复制到本次ext4临时卷；测量的是卷内层文件驻留，不含另外的RAM准备缓存。共享层不能报告为每个任务独占内存，provider匿名内存也未计入。运行命令例如`bash scripts/use-platform.sh run configs/platform-prettier6604-shared-2.json`，单次16 GiB固定卷要求主机至少42 GiB可用，不同时运行三份配置。 新复验固定每VM三个校准文件页（12 KiB），使用共享映射和mlock，测量结束后释放；任务文件及缓存策略不变。这部分计入物理量，旧成功配置未固定，因此不直接比较采集开销。修正版本已过14项回归及七实例正式复验。

2026-10-11用户授权本轮七实例校准修复的同类清理自动执行：失败资源回收、完整卷备份解压SHA-256核验通过后，可删除失败源卷；本轮旧完整失败备份不作为输入时可回收，只保留最新一份。全部原始日志、配置、轨迹、代码版本和FAIL记录保留。其他归档按本轮用户“后续也可自动处理”的授权逐份核验；只回收已被后续证据取代且不是当前输入的旧失败镜像，保留日志、配置和原始结果。当前任务输入镜像继续保留。

## 部署检查、资源查询和结果保留

统一入口增加以下操作。查询只读取原生AgentENV资源，模板、snapshot（保存的VM状态）与本地镜像归档分开；输出过滤访问令牌和自定义metadata。每个输出目录须是新的项目`.artifacts`子目录。

```bash
bash scripts/use-platform.sh deployment --output .artifacts/platform-management/deployment-mycheck
bash scripts/use-platform.sh resources --output .artifacts/platform-management/resources-mycheck
bash scripts/use-platform.sh retain .artifacts/platform-tasks/<run_id> --reason '保留此参考运行'
```

`deployment`核对固定源码和子模块版本、15份版本2配置的输入、KVM、ublk控制设备、页大小、host物理页读取权限、cgroup和采集工具；默认读取已准备服务的镜像身份，`--offline`不查询服务。它不安装依赖或重启服务。本机检查已通过；新机器仍需准备`configs/source-revisions.json`固定版本、`runtime/bin/aenv`、依赖、服务及报告列出的本地输入和镜像，凭据在新机自行配置，不复制到Git。配置中的任务路径可改成新准备目录，但输入哈希和原诊断语义保持。

`retain`给运行及关联的隔离结果目录写入`retention.json`。定向失败源卷清理入口会检查这些标记，保留现有结果和归档。此操作在实验完成后执行，不能恢复已回收的成功卷，也不会让原生checkpoint跨私有服务清理后继续存在。新增原生checkpoint离线保存/重新加载入口见下一节；本地结果标记与原生snapshot保存是两个操作。现有任务控制器的成功snapshot仍按各自清理规则回收。

## 原生checkpoint离线保存与重新加载

```bash
# 检查保存入口；不启动VM。
bash scripts/use-platform.sh checkpoint-demo --check-only

# 保存两步边界、归档、移除原私有服务，再在新服务恢复及执行第3、4步。
bash scripts/use-platform.sh checkpoint-demo

# 以后从已保存的包重新加载；不重复保存或准备旧镜像缓存。
bash scripts/use-platform.sh checkpoint-demo --bundle \
  .artifacts/storage-isolated-service/20261011T123631Z-703748/checkpoint-bundle
```

每个阶段使用8 GiB固定卷。新保存还预留最多2.5 GiB离线包，启动前须大于36.5 GiB主机可用空间；读取已有包只需现有8 GiB卷预算，即大于34 GiB。归档以固定服务镜像的POSIX snapshot仓库为单位，保留目录记录、VM状态、内存和文件层；运行依赖须与固定服务镜像一致。归档写入有2.5 GiB上限；导入核对压缩包和全部文件SHA-256，路径及成员检查后写入新卷。原生VM状态可能含凭据，包和状态数据库仅留本机，不能上传Git。

本次实际完成两步保存、只读归档恢复和`--bundle`新服务恢复。旧合并入口因仓库布局识别失败中断；修正后直接复用其已成功的snapshot，未重跑保存，也未把旧FAIL改为PASS。新包共21个文件，压缩0.944 GiB；恢复保留原guest进程、256 KiB匿名内存、已删除文件的打开描述符和步骤2，随后只执行3、4，进度到4。原模型评测未运行，Sphinx镜像只是工具接口负载的运行环境。本次已有包恢复工作流和两次离线报告通过，新的不间断合并入口未重复运行。

成功保存的包保留在原隔离结果目录`checkpoint-bundle/`，新成功恢复运行会写本地保留标记，保护关联包及参考数据。成功临时VM、导入后的snapshot、服务和卷均回收，长期保存对象是离线包。失败源卷依既有规则保留；完整备份核验后可按用户持续授权定向清理，原FAIL和日志保留。当前同一host和固定版本已验证，跨host/内核及三项研究任务的大checkpoint尚未用此归档接口验证。

离线重新报告仍用`use-platform report <durable_run_dir> --output <新的.artifacts目录>`。通用归档函数在`native_checkpoint_bundle.py`，演示控制器在`run_native_checkpoint.py`，失败导出现场复用工具在`recover_native_checkpoint_bundle.py`。恢复报告包含进程/匿名内存/打开文件和下一步证明，以及默认持续曲线；包导出、导入、恢复接口及控制器耗时分开保存。

## 实际工具调用采集与冻结重放

```bash
# 仅核对本机固定镜像输入，不启动沙箱。
bash scripts/use-platform.sh collect-demo --check-only

# 一次真实采集和重放，自动生成报告。
bash scripts/use-platform.sh collect-demo
```

此入口复用Sphinx的已准备镜像，顺序建立两个2 CPU/512 MiB新沙箱，在8 GiB隔离卷中运行。它执行4次新工具调用，保存命令、cwd、完整显式环境、会话语义、stdout/stderr、退出码及guest/控制器用时，冻结全部记录的SHA-256，再在相同镜像的新沙箱中重放并检查最终文件。第2步故意退出7，失败探索必须保持。这里是工具接口功能验证，没有执行Sphinx的35步任务或官方评测，也没有请求模型。

实际成功证据为`.artifacts/platform-tasks/20261011T115548Z-690247`；两实例13/10个持续采样点、内存/CPU/I/O和host进程曲线、清理均通过。两次离线报告10份文件一致，175份raw未变。最终报告是`.artifacts/platform-tasks/report-tool-capture-recomputed-1`；离线补全工具计时和明确`official_task_status=NOT_RUN`，原自动报告与运行记录保留。详见[公开摘要](records/evidence/platform-tool-capture-20261011.json)及[内存曲线](records/evidence/tool-capture-memory-20261011.svg)。

可复用接口在`scripts/tool_trajectory.py`：`begin`记录镜像/资源、初始文件哈希和会话语义，`capture`执行并记录实际工具请求，`freeze`封存，`replay`核对起点和续序调用。调用方提供现有CLI桥接函数`cli(label, *argv, timeout=...)`，格式与原控制器一致；先上传`guest_tool_capture.py`到`/workspace/asb-tool-capture.py`。示例控制器为`run_tool_capture.py`。接入在线Agent时，在其工具执行处调用`capture`；当前没有模型SDK连接、持久终端会话或其他工具类型的适配。原始环境和输出可能包含敏感内容，只留本机，不加入Git。

## 检查、运行、生成报告

新实验优先使用下方固定容量配置；本节不带配置的命令仍保留原共享盘行为。

从仓库根目录执行：

```bash
# 只检查配置和冻结输入，不查询服务，也不创建输出目录。
bash scripts/use-platform.sh check --offline

# 只读检查服务、凭据指向、已有实例、依赖和磁盘空间。
bash scripts/use-platform.sh check

# 按配置先完整重放，再保存、恢复、续跑，最后自动审计及生成报告。
bash scripts/use-platform.sh run
```

默认使用[platform-exp1.json](../configs/platform-exp1.json)，选择AgentENV及第022步保存。输出为`.artifacts/platform-use/<run_id>/`；每次创建新目录，任何阶段失败即停止，不自动重试。成功必须同时有退出码0及该目录`result.json`中的`status=PASS`，不能只看子步骤日志。

可选择后端、指定保存步骤：

```bash
bash scripts/use-platform.sh check --backend both
bash scripts/use-platform.sh run --backend agentenv --checkpoint-after 025
```

AgentENV可指定001至025步；配置中设`checkpoint_after_step: null`表示只完整重放。TrEnv-X目前只支持013和022步的文件状态继承；两个后端一起运行只能选择两者共同支持的步骤。

默认完整重放作为恢复前的功能验证。AgentENV第022步保存后的恢复必须核对进程PID、启动时间和boot ID保持，计数增长，001–022进度及输出保留，已修改源码和Git状态一致，然后执行023–026。其他保存步骤按相同规则验证，未实际运行的边界不能标为已验证。TrEnv-X不能恢复任意父进程，仍使用原后端的文件状态及进度检查。

成功运行后可以再次从原始数据生成报告，输出目录必须是新的、独立的项目产物目录：

```bash
bash scripts/use-platform.sh report .artifacts/platform-use/<成功运行的run_id> \
  --output .artifacts/platform-use-report-new
```

复算阶段禁止当前Python路径写入输入或启动子进程；报告和图表仅写新输出目录。失败或不完整的组合运行不能通过这个入口生成PASS。此保护不是系统级只读挂载；公开摘要不能替代本地原始数据。

## 最小输入约定与生效配置

配置相对路径以仓库根目录解释，未知字段会报错，不悄悄忽略。传入另一份配置：`bash scripts/use-platform.sh check /路径/config.json`，运行时将`check`换成`run`。

| 字段 | 含义与限制 |
| --- | --- |
| `version`、`task_id` | 当前固定为1及`prettier__prettier-14400`，不将其他任务套用本任务测试 |
| `storage` | 可选`shared`或`isolated`；省略时保留原共享盘行为 |
| `backends` | 非空、不重复的`agentenv`、`trenvx`列表 |
| `checkpoint_after_step` | 保存发生在该工具步骤结束之后；001–025，或null只完整重放 |
| `repository`、`trajectory_dir` | 固定研究代码及冻结输入；轨迹包含`actions.tsv`和`actions/001.sh`至`026.sh` |
| `runtime_dir`、`server_result` | 已部署CLI/本地凭据目录及项目服务定位记录；凭据不复制进产物或上传 |
| `official_task` | 已准备的固定官方任务测试及解析器，哈希必须匹配 |
| `output_root` | 必须在项目`.artifacts/`下，每次创建新的run_id目录 |
| CPU、内存 | 当前适配固定2 CPU、4096 MiB，不扩大历史精细探针的布局假设 |
| 采样周期、动作超时 | 周期至少0.1秒；动作超时固定60秒，不改变冻结任务策略 |

`actions.tsv`每行是步骤编号、脚本SHA-256和原命令描述，用tab分隔。执行的是冻结脚本，不重新调用模型；008、016退出127属于预期探索失败。检查器核对固定manifest哈希、26个文件及逐个动作哈希，拒绝漏项、多余动作和改写的轨迹。AgentENV允许从其他目录接入相同冻结包；其他任务需要独立适配及测试，当前明确拒绝。TrEnv-X现有代码仍使用固定仓库路径，配置不能假装改变该路径。

运行保存`effective-config.json`、源码副本和哈希、冻结动作、各次调用日志、原始raw/audit路径、正确性、官方测试、内存曲线、阶段耗时及资源检查。实际镜像digest、guest环境、研究代码版本和源码改动由后端记录。失败与重试各用新run_id，不能只凭Git HEAD还原大量未提交代码。

## 指标、资源与边界

本入口验证使用流程，不重新估计监控开销。AgentENV使用原guest按步骤采样、host服务cgroup轻量采样；TrEnv-X使用原guest采样和本次沙箱cgroup采样。内存单位bytes，图表换算MiB；时延原始单位ns，表中换算秒。曲线只覆盖采样窗口，保存/恢复中的缺口不补造样本，峰值是观测最大值。

guest、host cgroup、VMM PSS和物理文件页不能相加。这个普通使用入口不额外做file-LRU物理扫描，也不把逻辑Cached等同于物理page cache。完整第三项验收范围及采集成本仍以[固定验收报告](records/ACCEPTANCE_REVIEW_20261001.md#固定初步验收交付)为准。

共享盘模式启动前AgentENV沿用32 GiB空间要求；新入口对TrEnv-X统一要求超过60 GiB。底层旧完整重放/恢复入口分别仍是36/48 GiB，但本轮从42 GiB启动仍触发保护线，故不直接沿用旧门槛。60 GiB覆盖此次约36 GiB的暂时空闲下降、20 GiB保留量和余量；这不是共享盘的硬保证，额外空间变化来源仍待定位。运行中保留20 GiB，并只停止本次拥有的进程组，先给清理代码执行时间；轮询不代替硬配额。两后端组合在整批启动前按较高要求检查，不先跑一半再发现恢复预算不足。

不安装或重启共享服务，不清host/guest缓存，不修改CPU策略。共享盘模式的成功TrEnv-X普通重放沿用已授权规则删除本次私有镜像；该模式恢复及失败镜像仍保留。固定容量模式在全部验证和清理通过后回收本次临时卷及其中的镜像，保留原始数据和哈希；失败卷保留。两种模式都不清理旧归档。

## 磁盘保护与诊断

新入口达到20 GiB保留量或收到停止信号时，先停止本次写入。已进入资源清理或成功镜像回收的TrEnv-X子程序可继续完成，最多等待180秒；超时只终止本次进程组。子程序退出后，独立程序核对本次成功结果、显式删除配置、固定模板哈希及cgroup、挂载和loop清理，符合条件才回收普通重放镜像，并单独记录`*-recovery.json`。失败、恢复实验、未授权或已有不完整回收记录的镜像保留；停止后的失败数据不再新建压缩归档。达到保护线的组合仍记FAIL，回收成功不改写它。

磁盘诊断默认关闭。待空间条件满足后，可用`bash scripts/use-platform.sh run --backend trenvx --storage-diagnostics`随运行记录；它不是性能对照模式。`*-storage/samples.jsonl`每秒记录文件系统总量、全部空闲与普通用户可用空间、Dirty/Writeback，以及进程写入量变化；每5秒扫描本次目录中文件的已分配块数，按inode去重，保留扫描时间。TrEnv-X另写`storage-events.jsonl`标记复制、模板构建、重放和镜像处理阶段。记录器保存源码及哈希，日志只留本机。

只观察当前磁盘，不启动沙箱：

```bash
bash scripts/observe-storage.sh \
  --output ".artifacts/storage-check-$(date -u +%Y%m%dT%H%M%SZ)" --duration 15
```

可附加`--root <本次实验目录>`记录该目录的文件占用；不扫描整盘或所有历史归档。扫描不跟随符号链接，也不跨文件系统读取挂载的guest文件，避免把镜像和其中的文件重复计算。目录值不含目录inode自身的块，不能解析reflink共享，不是各任务独占物理量。进程I/O跨所有磁盘，不等同于空间增减；短命进程和采样间的写入可能漏记，来源不明的变化不归因于其他任务。采样本身的读取耗时有记录，但对任务的影响尚未测量，不把诊断结果混入监控开销结论。

小文件及模拟进程验证用`bash scripts/test-storage-safety.sh`，不启动VM、不操作设备或旧归档。当前实现与验证见[磁盘保护证据](records/evidence/storage-safety-20261007.json)。真实VM异常停止后的整套清理仍未实测；60 GiB启动预算、20 GiB轮询保护和独立回收都不能代替共享盘硬配额。

资源核对区分运行中的VM和服务预启动进程池。只允许同一项目服务中经Firecracker API确认`Not started`的空闲池进程替换，数量保持不变；运行中的VM及其他服务的VM身份必须不变。本次实例和snapshot必须按后端证据清理。没有清理证据不能PASS；不因PID变化擅自杀进程。

## 当前验证记录

本轮真实运行、复算和空间限制见[收尾记录](records/CLOSEOUT_STATUS.md)。AgentENV保存步骤参数及状态核对已接入；TrEnv-X恢复的完整组合验证仍受新入口60 GiB空间要求约束，不能用旧单项成功或模拟测试替代新组合成功。

## 固定容量存储验证

统一入口现已支持[platform-exp1-isolated.json](../configs/platform-exp1-isolated.json)，原组合三项通过、TrEnv-X第022步恢复失败；现已修复并独立补齐该恢复项，见[复验证据](records/evidence/trenvx-checkpoint-journal-fix-20261008.json)。原组合FAIL不改写，没有重复四项整批实验。AgentENV用20 GiB卷，TrEnv-X用24 GiB卷，两者完全预分配、禁止自动扩容，顺序使用且每次成功后先回收卷。不迁移或重启原服务。固定卷限制本次大文件写入量，不是独立物理磁盘，也不能约束其他用户写入。

只有在其他使用者明确确认新的约20分钟窗口后，才设置实际起止时间并运行：

```bash
bash scripts/use-platform.sh check configs/platform-exp1-isolated.json

# 已确认可立即开始窗口后，设置一次；失败后不能自行续期。
ASB_WINDOW_START=$(date -u +%Y-%m-%dT%H:%M:%S+00:00)
ASB_WINDOW_END=$(date -u -d '+20 minutes' +%Y-%m-%dT%H:%M:%S+00:00)
bash scripts/use-platform.sh run configs/platform-exp1-isolated.json \
  --confirmed-window --window-start-utc "$ASB_WINDOW_START" --window-end-utc "$ASB_WINDOW_END"
```

该配置依次执行AgentENV完整重放、第022步VM恢复、TrEnv-X完整重放及第022步文件恢复，然后自动生成四份报告。恢复运行引用同批完整重放的原始数据，保留001–022并从023续跑。采样间隔实际传入两后端，默认0.1秒。仅支持固定2 CPU、4096 MiB、已准备的runtime/仓库/源服务，以及完整重放或第022步边界；不支持的设置在分配卷前拒绝。可用`--backend agentenv`只运行该后端，或配置中设`checkpoint_after_step: null`只完整重放。

AgentENV单独运行启动前须超过46 GiB可用空间，含TrEnv-X则须超过60 GiB。镜像、checkpoint、服务缓存、临时文件及大文件日志进入本次卷；输入、源码副本、磁盘记录和成功后导出的raw/audit、图表保留在主机，属于未受卷容量限制的小量实验记录。主机20 GiB与卷内清理预留分别检查；固定卷不保证主机始终留下20 GiB。成功后临时checkpoint镜像也删除，不能再加载该现场；需要长期保留checkpoint时须另行选择持久存储方案。

上次TrEnv-X读取复制层目录返回`Bad message`。现改为保留原捕获件，在新私有副本上执行ext4日志回放，再只读导出文件层；暂停期间复制哈希和日志处理记录会保存。日志回放仅修复私有副本的日志状态，空闲计数差异明确记录为警告，不声称文件系统全面一致。第022步恢复、进度、续跑、任务测试、报告和清理已独立通过。失败捕获件及副本放在data目录，由失败卷/归档保留，未生成的镜像不能凭空保存；成功临时镜像按原规则随验证卷回收。此前24 GiB失败卷经确认删除，卷外镜像备份和历史记录保留。继续运行仍须重新核验超过60 GiB空间和新的共享写入窗口。

每次最终`result.json`需同时验证任务、正确性、官方测试、资源清理及原有VM身份不变才记PASS。`report`复算命令与共享盘模式相同。下方专用脚本保留作诊断入口，不需要在每次组合运行前重复其长验证。


取得一次新的共享盘窗口确认后，明确设置`ASB_WINDOW_START`和`ASB_WINDOW_END`为带时区的起止时间，间隔不超过20分钟，再以root调用：

```bash
bash scripts/validate-isolated-storage.sh \
  --confirmed-window \
  --window-start-utc "$ASB_WINDOW_START" \
  --window-end-utc "$ASB_WINDOW_END" \
  --exp1-checkpoint
```

去掉`--exp1-checkpoint`只运行短生命周期验证。没有用户确认时不要使用`--confirmed-window`；多个命令使用同一个起止时间，不能自动延长窗口。启动要求主机可用空间超过46 GiB（20 GiB固定卷、20 GiB主机保留量、6 GiB余量），不改原有共享盘入口门槛。缺失挂载或目标身份不符即失败，不能回落到父目录继续写。

本轮完整重放的146条磁盘样本观测峰值5.15 GiB，包含512 MiB清理预留，并非瞬时精确峰值或page cache内存；卷总容量仍为20 GiB。成功验证卷及其镜像会删除，保留原始轨迹、stdout/stderr、采样、配置和哈希；失败卷保留供定位，不能直接重试累积镜像。结束只清理本次拥有的容器、挂载和loop。

功能验证关闭新服务的进程池与缓存GC，并使用复制的不可变缓存；没有复制运行中的数据库、旧snapshot或原服务密钥。新I/O路径和缓存状态使它不能直接加入旧性能统计。真实VM写满后的清理、长期服务迁移及多任务容量仍未验证。协商窗口不能约束其他用户，固定卷也不覆盖特权设备操作、少量Docker元数据及主机控制记录。原始证据见[本轮结果](records/evidence/storage-isolated-agentenv-20261008.json)。


### TrEnv-X临时验证入口

本机已通过短验证及完整Prettier #14400轨迹的第022步文件状态恢复。取得新的20分钟窗口确认后，设置带时区的`ASB_WINDOW_START`和`ASB_WINDOW_END`，以同一窗口依次执行；短验证PASS后才运行恢复：

```bash
bash scripts/validate-isolated-trenvx.sh --mode smoke \
  --confirmed-window --window-start-utc "$ASB_WINDOW_START" --window-end-utc "$ASB_WINDOW_END"

bash scripts/validate-isolated-trenvx.sh --mode checkpoint \
  --reference .artifacts/trenvx-service-smoke/20260927T110632Z-2094910 \
  --confirmed-window --window-start-utc "$ASB_WINDOW_START" --window-end-utc "$ASB_WINDOW_END"
```

参考目录必须是本项目已有成功完整冻结重放；启动前检查其运行状态、动作与patch哈希。Bash入口使用固定`runtime/trenvx-venv`，SDK导入和底层参数解析均在分配空间前执行。没有窗口确认不要使用`--confirmed-window`，窗口不足以容纳下一次运行时重新协调，不能自动续期。

使用24 GiB完全预分配卷，主机启动前仍须超过60 GiB。私有镜像、模板缓存、文件checkpoint、归档和本次大文件日志位于卷内；主机20 GiB与卷内512 MiB预留分别检查，开始归档另需卷内6 GiB余量。缺失挂载、镜像身份或分配块变化即失败，不能回落到共享父目录。固定卷不保证其他用户写入后主机还剩20 GiB。私有tmpfs占用内存，少量主机控制记录和成功后的raw导出不在卷内。

成功时删除本次临时镜像和验证卷，保留原始数据、配置、日志、哈希及归档清理证明；生成的checkpoint不能在删除后直接重载。失败实验镜像保留，先诊断再处理，不能自动清掉或连续重试。仅参数解析失败、未启动后端且没有实验镜像的空卷，核验控制文件备份后可回收，FAIL记录保留。第022步恢复仅继承文件状态，不保留任意父进程。

当前完整恢复运行的1526条磁盘样本观测占用峰值18.604 GiB，包含清理预留；成功清理后主机约67.14 GiB可用。后端总耗时378.954秒，其中镜像归档校验250.827秒；这不是冻结工具耗时。既有内存及阶段报告已只读复算两次一致，新路径不用于历史性能对照或监控开销判断。该数据是专用入口的历史验证；统一组合的实际结果另行记录。真实VM写满清理和多任务容量仍未验证。见[运行证据](records/evidence/storage-isolated-trenvx-20261008.json)。

## 文件层日志诊断

只用于本项目诊断副本，不接触运行中的镜像，不启动沙箱：

```bash
bash scripts/diagnose-ext4-journal.sh \
  .artifacts/checkpoint-image-diagnostic/20261008T061239Z-3582812/probe-sources/built_template_upper.ext4 \
  .artifacts/checkpoint-image-diagnostic/<新的诊断目录> --check-only
```

`--check-only`只核对路径；需要真实诊断时删除该参数。真实诊断会先建立内容相同的新副本，只在副本上执行日志回放，再以私有挂载和只读loop检查前后目录。输入目录限定在本项目`checkpoint-image-diagnostic`，拒绝原实验卷、已挂载镜像和已有输出目录，先核对主机空间。原输入、归档和失败记录不改写。空闲计数警告单独记录，不能仅凭fsck退出0判定全面健康。共享脚本尚未整套实测；本轮真实对照的实际脚本和原始结果完整保留。

## Prettier #6604单路径试验入口

Xarray #6992的新单路径入口见文末，不能用Prettier的Jest评测输入替代SWE-bench测试。

配置为`configs/prettier6604-backbone.json`，固定学长Exp3的5段29步`backbone`，复用原controller与guest-runner。冻结动作、预期失败、参考patch和输入均核对哈希；采样时的本地镜像tag不能当作本次源镜像digest的证明，本次另查base commit、关键源码及测试。创建HTTP等待600秒与沙箱存活1800秒分别记录。既有路径重放一致，但官方测试失败；不自动改轨迹或启动多轮。

只读检查已准备输入：

```bash
python3 -B scripts/run_prettier6604.py --check-only \
  --official-task .artifacts/prettier6604/20261008T082850Z/shared-prepare-check
```

任务输入可通过`exp1_official_task.py prepare --instance prettier__prettier-6604`准备，使用固定数据集和评测parser，已有缓存可复用；不能用#14400测试输入替代。真实运行由`validate-isolated-storage.sh --prettier6604-task <输入目录>`进入，仍须显式确认窗口、超过46 GiB主机可用空间及20 GiB固定卷。成功卷回收、失败卷保留；现有失败尚未解决前不重复运行。

从本次保存的测试输出独立复核，再生成新报告，两个输出位置必须不存在：

```bash
python3 -B scripts/audit_prettier6604_official.py \
  .artifacts/prettier6604/20261008T082850Z/shared-prepare-check \
  .artifacts/storage-isolated-service/20261008T102942Z-3642885/prettier6604/official-task-tests.stdout \
  .artifacts/prettier6604/<新目录>/official-audit.json
python3 -B scripts/report_prettier6604.py \
  .artifacts/storage-isolated-service/20261008T102942Z-3642885/prettier6604 \
  .artifacts/prettier6604/<新目录>/report \
  --official-audit .artifacts/prettier6604/<新目录>/official-audit.json
```

先建立上述新目录。两个命令对该普通重放数据都会输出`status=FAIL`、退出1：这是官方测试失败的正确判定，报告仍保留`replay_consistency=PASS`及实际指标，不能当作重新运行沙箱。报告含内存曲线、阶段ns耗时、指标范围和输入哈希，拒绝写入raw目录或覆盖旧输出。该普通重放记录没有checkpoint/restore；物理page cache、VMM PSS、监控影响及其他rollout未验收。

### 第24步保存、恢复和续跑（本机已通过）

先验证同一条完整重放留下的官方结果及原stdout哈希：

```bash
python3 -B scripts/run_prettier6604.py --check-only --checkpoint \
  --official-task .artifacts/prettier6604/20261008T082850Z/shared-prepare-check \
  --expected-official-audit .artifacts/prettier6604/20261008T102942Z-retry/official-audit-recheck.json
```

真实入口增加`--prettier6604-checkpoint --expected-official-audit <上述参考结果>`，仍需明确窗口。此模式使用24 GiB卷，启动前超过50 GiB可用空间；VM配额不变。保存边界为`core_verify/006`后（已完成全局24步），恢复后只执行`backbone_finish`（全局25–29）。完整VM恢复必须核对源码、测试、临时输入及前缀输出，且原后台进程身份不变、计数继续增长；snapshot创建成功本身不能计PASS。

原轨迹的官方测试失败属于本次固定参考结果。恢复后必须有同一patch、相同失败集合和计数，无跳过、超时或新增失败。成功时`result.status=PASS`只表示平台恢复及续跑验证通过，`official_task_status=FAIL`单独保留；报告也必须分别显示这两项。新的失败snapshot和失败卷保留，成功临时snapshot随卷回收。缺少原始参考stdout时预检拒绝，公开摘要不能替代参考证据。

2026-10-08真实验证及两次只读报告复算通过。已保存85个所选文件与24步进度，原进程身份不变且计数82→89；恢复后只执行25–29，官方2项失败与原结果一致。原始证据为`.artifacts/storage-isolated-service/20261008T113738Z-3660710/prettier6604`，成功临时snapshot已回收。只读复算到新目录：

```bash
python3 -B scripts/report_prettier6604.py \
  .artifacts/storage-isolated-service/20261008T113738Z-3660710/prettier6604 \
  .artifacts/prettier6604/<新恢复报告目录>
```

对此记录，命令输出`status=PASS`、退出0表示平台恢复审计通过，`summary.json`仍含`official_task_status=FAIL`。它不会启动沙箱或重新运行任务。范围及实际指标见[本轮证据](records/evidence/prettier6604-checkpoint-20261008.json)。
## Prettier #6604多路径输入准备（2026-10-08）

原仓库四条分支的检查动作被同名文件覆盖。完整多路径实验前，先从原采样生成独立修正版，不直接以原`plan.py`通过判定输入正确。这个命令只检查和导出文件，不启动沙箱；输出目录必须不存在：

```bash
python3 -B scripts/prepare_prettier6604_rollouts.py \
  --output .artifacts/prettier6604/branch-inputs-local
```

本机原输入预期显示`original_status=FAIL`，正确导出显示`exported_status=PASS`。`original-audit.json`列出差异，`exported-audit.json`核对189个动作，`provenance.json`记录原输入及导出哈希，`workload/`是独立修正版。此PASS仅指动作与采样相同。2026-10-08另有真实`early_1`及完整GRPO平台PASS，但七条路径官方均0通过/2失败，见[证据](records/evidence/prettier6604-grpo-20261008.json)。原采样记录保留在本地，不上传原始对话或运行镜像。修正版TVCache的执行段数为17，与历史13段区分。

多路径入口复用`bash scripts/validate-isolated-storage.sh`，在原`--prettier6604-task`和`--expected-official-audit`之外增加`--prettier6604-export <导出目录>`。默认GRPO：添加`--prettier6604-role early_1`运行单分支，省略role运行7个独立沙箱；分别使用20/32 GiB卷，开始前必须超过46/58 GiB可用空间。增加`--prettier6604-schedule bpo`选择BPO：同一role选项运行第11步保存及一个恢复分支，省略role运行主干、两处保存及六个恢复分支；分别使用24/32 GiB卷，开始前必须超过50/58 GiB可用空间。BPO短验证仅支持`early_1`。所有真实运行必须传入已确认的窗口起止时间，成功后只回收本次资源，失败卷保留。BPO短验证及完整功能已通过，见[证据](records/evidence/prettier6604-bpo-20261008.json)；TVCache短验证及完整功能也已通过，见[证据](records/evidence/prettier6604-tvcache-20261008.json)。增加`--prettier6604-schedule tvcache`选择确定顺序前缀树：`--prettier6604-role early_1`短验证运行完整主干及一个恢复分支，7段39个实际动作；省略role计划运行17段84个实际动作。分别使用24/32 GiB卷及50/58 GiB开始门槛，同样需要新确认窗口。

复用已验证旧备份缓存时，可先执行`python3 -B scripts/prepare_prettier6604_cache.py <新本地产物目录>`，然后给上述入口增加`--immutable-cache-seed <该目录>`。此辅助工具依赖本机固定旧备份及其核验记录，要求至少64 GiB可用RAM；它在私有tmpfs解压并核对哈希，只读挂载后仅复制不可变缓存和依赖，不复制VM可写层、数据库或凭据。使用结束必须执行`python3 -B scripts/prepare_prettier6604_cache.py <该目录> --release`，并核对`resources.json`中的`released=true`。辅助工具不能根据公开摘要凭空生成镜像；它的时间和内存另计，不能将此缓存状态与下载组作公平性能比较。

报告脚本也支持BPO原始数据，核对两处状态及各恢复分支；只读复算到新目录，例如GRPO：

```bash
python3 -B scripts/report_prettier6604_grpo.py \
  .artifacts/storage-isolated-service/20261008T132220Z-3722480/prettier6604 \
  .artifacts/prettier6604/<新GRPO报告目录> \
  --official-task .artifacts/prettier6604/20261008T082850Z/shared-prepare-check
```

退出0及`status=PASS`表示平台动作、进度、来源源码、完整官方结果及沙箱清理审计通过，任务正确性在`per_role.*.official_task_status`另列FAIL。`summary.json`和`memory.svg`分别报告各guest及单个服务cgroup，不能把并发动作之和、guest峰值之和或guest/host内存当端到端时间或物理总量。物理缓存、VMM PSS、Slab及本配置监控影响仍未验证。

BPO报告可将上述raw参数替换为`.artifacts/storage-isolated-service/20261008T141932Z-3737154/prettier6604`。BPO的`actions=84`是实际执行次数，`logical_action_count=189`包含复用前缀的逻辑路径长度；子分支样本只覆盖剩余动作。`checkpoint_call_ns`和`restore_request_per_role_ns`是接口耗时；首个工具至最后下载的跨度包含保存/恢复间隔，不是工具独占时间。此次临时snapshot按成功清理规则删除，长期checkpoint管理不能据此验收。

TVCache的完整raw为`.artifacts/storage-isolated-service/20261008T144041Z-3754218/prettier6604`，也使用同一只读报告脚本。`actions=84`与`logical_action_count=189`分别表示实际执行和包含复用前缀的逻辑长度；实际顺序、分叉checkpoint及父子实例对应都会核对。七条官方结果仍为FAIL，不能把调度功能通过写成任务修复成功。

## Xarray #6992单路径入口

配置为`configs/xarray6992-backbone.json`。复用学长Exp3控制器及原guest-runner，执行原5段35个动作，先核对首段，再在同一VM继续其余四段。原轨迹的失败探索及最终自测失败保留；最终完整patch由固定base文件与原三次编辑重建，不使用评测答案。

必须使用新配置中的`frozen_shell_environment`：每段runner显式恢复镜像原PATH，并清除Conda激活变量。2026-10-09首段实测发现`bash -lc`自动启用testbed环境，让原本缺numpy的基础Python动作变成成功；原采样`which`输出使用基础Python。此处固定执行上下文，不改动作、不放宽预期退出码；其他未采集环境变量不宣称完全相同。

准备新的本地输入目录并检查（只下载任务测试行和固定版本文件，不启动服务）：

```bash
python3 -B scripts/prepare_xarray6992.py \
  .artifacts/python-backbone/<新输入目录> --fetch
python3 -B scripts/run_xarray6992.py \
  --inputs .artifacts/python-backbone/<输入目录> --check-only
```

下载依赖代理和Hugging Face数据服务。该服务的filter响应没有在本工具中固定parquet版本；所选任务行须与公开配置中的SHA256完全一致，变化或网络错误会停止。基线源文件按完整commit和哈希核对。已有输入可直接检查，不必重复下载。

真实运行要求已确认的20分钟共享盘窗口、超过58 GiB主机空闲量、32 GiB固定临时卷；窗口变量采用带时区的实际起止时间，沿用上文设置方法：

```bash
bash scripts/validate-isolated-storage.sh --confirmed-window \
  --window-start-utc "$ASB_WINDOW_START" --window-end-utc "$ASB_WINDOW_END" \
  --python-backbone-inputs .artifacts/python-backbone/<输入目录>
```

镜像、服务缓存、guest磁盘和临时文件均在固定卷内。创建HTTP等待600秒，入口总超时900秒；下载或转换超时不能计运行通过。成功卷自动回收，失败卷保留。此模式没有checkpoint或分支调度，也尚未纳入默认`use-platform`配置。

如大镜像层下载阻塞，可先按固定manifest分段下载并核对每个blob的digest。下面入口要求实际确认的窗口和至少64 GiB可用RAM，使用2 GiB上限的私有tmpfs；镜像缓存是临时内存文件，不是持久结果：

```bash
python3 -B scripts/prepare_xarray6992_image.py \
  --manifest configs/xarray6992-image-manifest.json \
  --output .artifacts/python-image-cache/<新目录> \
  --window-start-utc "$ASB_WINDOW_START" --window-end-utc "$ASB_WINDOW_END"
```

成功后只读挂载，固定13层全部校验，输出实际解包文件量。给真实入口增加`--python-backbone-oci <上述目录>`，仅在临时服务私有复制件中让`regctl image copy`从本地OCI布局读取；其他操作仍调用原版regctl，原生overlaybd转换保持不变，镜像及配置digest不变。下载/缓存准备时间和RAM成本另列，不能与普通远程下载作公平冷启动比较。

加入`--python-backbone-capacity-gib 24`或`28`可选择较小卷，但必须通过基于实际层体积的预算检查，仍保留20 GiB主机空间和6 GiB额外余量。24/28/32 GiB卷分别要求超过50/54/58 GiB可用空间。新镜像的第一次本地转换实测卷峰值13.12 GiB；这只证明该阶段占用，不保证其他任务、恢复或多分支同样足够。

本地缓存用完执行`python3 -B scripts/prepare_xarray6992_image.py --output <上述目录> --release`，只卸载本次成功缓存，并核对`resources.json`的`released=true`。下载失败会保留缓存和事件，不启动VM；失败临时服务卷可用`backup_isolated_failure.py <run_dir>`建立完整备份。删除必须另行授权，然后才可调用`remove_approved_isolated_volume.py <run_dir> --approval-text '<实际授权>'`；工具不会自行获得授权，也不删除备份或日志。

完整重放后先归档最终patch及文件哈希，再单独恢复三份基线测试文件并应用SWE-bench测试补丁，使用镜像内已有pytest执行12项F2P和945项P2P，共957个指定测试。评测不请求模型、不安装依赖、不加入官方修复代码。F2P表示原问题应由失败变为通过的测试，P2P表示应保持通过的测试。缺失、重复、跳过、超时或基础设施错误均不能计任务通过；完整结果中的真实断言失败与平台故障分别判定。该阶段直接调用镜像pytest，尚未运行完整官方Docker harness。

成功结果的只读复算（输出须是新目录）：

```bash
python3 -B scripts/report_xarray6992.py \
  .artifacts/storage-isolated-service/<run_id>/python-backbone \
  .artifacts/python-backbone/<新报告目录>
```

guest逻辑内存与host服务cgroup分列，单位为bytes；时延为ns。Cached不是跨VM去重物理量，Shmem与Cached重叠，Slab和VMM PSS未采。新任务的监控影响和物理page cache仍未验证；指定边界的恢复结果见文末，不套用Exp1验收结论。

2026-10-09已完成35个冻结动作的完整诊断，最终patch与两份文件哈希一致；第8步管道退出码为141，严格重放仍FAIL。独立957项测试已完整执行，945通过、12失败、0跳过，任务正确性FAIL。首次完整诊断运行因3个参数化测试名称被截短而在收集阶段失败，原raw的FAIL保留；随后独立评测使用该次归档的实际patch，没有重复35步，也没有恢复原VM。见[完整证据](records/evidence/xarray6992-complete-diagnostic-20261009.json)。早先失败运行仍可只读核对，输出须是不存在的新文件：

```bash
python3 -B scripts/audit_xarray6992_failure.py \
  .artifacts/storage-isolated-service/20261008T165515Z-3834076/python-backbone \
  .artifacts/python-backbone/<新失败核对文件>.json
```

诊断入口的`--python-diagnostic-only --python-readonly-fixture <已停止失败目录>`使用2 GiB私有卷，从只读loop挂载复用完整转换缓存，清理后核对源卷mtime未变。它不执行35步、不计trajectory通过。改进诊断已确认进程目录消失和僵尸`sleep`的影响；单进程采样使原`find`返回0，但另一个管道退出码仍不稳定，详见[当前状态](STATUS.md)。诊断要求超过28 GiB空闲及确认的新窗口；只有元数据而无完整转换层的失败卷不能作为缓存源。

已备份并获准删除源卷时，可复用缓存准备代码恢复只读RAM缓存，不必把24 GiB镜像重新写入磁盘：

```bash
python3 -B scripts/prepare_prettier6604_cache.py \
  .artifacts/python-backbone/<新缓存目录> \
  --source-backup-directory .artifacts/storage-isolated-service/<24GiB备份目录>
```

此参数仅接受哈希正确、标记为Xarray任务的24 GiB备份；需要超过64 GiB可用RAM，稀疏恢复在28 GiB有界tmpfs内，解压哈希与文件系统clean检查通过后只读挂载。当前实测文件分配10.39 GiB，不是任务page cache测量结果。结束后以同一脚本加`--release`回收挂载、loop及RAM缓存；备份和元数据保留。

短诊断增加`--immutable-cache-seed <缓存目录>`，与`--python-readonly-fixture`二选一；`--python-diagnostic-probe pipeline`仅针对管道退出码采集12次证据。诊断捕获PASS不表示trajectory通过。完整冻结执行可用`--python-readonly-replay --python-persistent-sampling --immutable-cache-seed <缓存目录>`，全部写入8 GiB固定卷，要求超过34 GiB主机空闲；复制并预留清理空间后还需超过6 GiB运行余量，转换层始终只读。冻结动作、预期退出码和最终参考不变，变体runner和采集回执另存。默认模式遇到141仍严格失败，不自动忽略。

本轮已获用户授权的完整诊断增加`--python-diagnostic-continuation`，仅在`trace_index_state/002`的命令及955字节stdout哈希与固定参考一致时记录0→141并继续；其他动作、退出码和输出变化仍停止。诊断PASS不改变严格重放FAIL，也不表示模型修复任务通过。该模式已完成35步和最终状态核验。

若完整诊断已经归档实际patch，而评测在收集阶段失败，可在新确认窗口内独立评测，避免重复冻结动作：

```bash
bash scripts/validate-isolated-storage.sh --confirmed-window \
  --window-start-utc "$ASB_WINDOW_START" --window-end-utc "$ASB_WINDOW_END" \
  --python-backbone-inputs .artifacts/python-backbone/<原输入目录> \
  --immutable-cache-seed .artifacts/python-backbone/<已准备缓存目录> \
  --python-evaluation-patch .artifacts/storage-isolated-service/<完整35步run_id>/python-backbone/final.patch
```

该模式使用2 GiB固定卷，要求超过28 GiB主机空闲。入口核对实际patch来自已完成最终状态检查的原始运行；新VM应用此patch后核对文件，再进行独立测试，不是checkpoint/restore。参数化测试通过pytest收集真实完整名称，为原清单中的截短名称建立唯一对应；清单、别名和收集结果均归档，缺失、重复或歧义不接受。运行PASS表示评测和清理完整，`official_task_status`单独判定任务结果。

本轮两次运行的配对报告可只读复算到新目录：

```bash
python3 -B scripts/report_xarray6992.py \
  .artifacts/storage-isolated-service/20261009T032655Z-3886078/python-backbone \
  .artifacts/python-backbone/<新报告目录> \
  --evaluation-raw .artifacts/storage-isolated-service/20261009T033359Z-3893120/python-backbone
```

报告核对两次运行的配置、实际patch来源、最终文件、测试名称对应、全部JUnit结果及清理记录。报告验证和完整诊断PASS、严格重放FAIL、任务FAIL分别列出；不会改写首次运行的收集失败。这两次原始运行的报告与曲线逐字节一致，337份raw哈希未变。Cached仍是guest逻辑指标；物理缓存和本配置的监控影响尚未验证。

### 第22步保存、恢复并从第23步续跑

已验证`core_patch`结束后保存完整VM：99份文件及前22步进度一致，下一段尚未执行；删除原实例，在不同实例中restore。恢复后文件和进度一致，进程探针身份保持且计数增长，原第23–35步只执行一次。最终patch及957项测试结果与原诊断相同：945通过、12失败、0跳过。平台恢复PASS，严格重放FAIL、模型任务FAIL分别保留，见[本轮证据](records/evidence/xarray6992-checkpoint-20261009.json)。

使用已准备的只读缓存及完整评测raw作对照，新增两个参数：

```bash
bash scripts/validate-isolated-storage.sh --confirmed-window \
  --window-start-utc "$ASB_WINDOW_START" --window-end-utc "$ASB_WINDOW_END" \
  --python-backbone-inputs .artifacts/python-backbone/<输入目录> \
  --python-readonly-replay --python-persistent-sampling --python-diagnostic-continuation \
  --immutable-cache-seed .artifacts/python-backbone/<已准备缓存目录> \
  --python-checkpoint \
  --python-expected-evaluation-raw .artifacts/storage-isolated-service/20261009T033359Z-3893120/python-backbone
```

恢复入口使用16 GiB固定卷，要求超过42 GiB主机空闲量；20 GiB保护量和6 GiB余量不变。复制后还需超过14 GiB卷内空间，覆盖4 GiB内存快照、可写层、恢复临时数据及余量，另预留512 MiB清理空间。只读转换层位于有界RAM缓存中，准备时间和RAM文件分配量另列。入口在分配前核对原完整诊断及独立评测的原始证据，测试缺失、跳过或失败集合变化均不能计恢复通过。

保存/恢复接口、状态核对、续跑、任务测试及资源清理分别判定。成功后删除本次VM和临时快照，确认本服务实例列表为空，再回收服务、卷和RAM缓存；失败卷按原约定保留。一个探针的连续性不代表检查了所有进程、匿名内存和打开文件。长期快照保留、远端持久化、后来重载及本任务监控影响尚未验证。

只读复算本轮结果，输出须是新目录：

```bash
python3 -B scripts/report_xarray6992.py \
  .artifacts/storage-isolated-service/20261009T035819Z-3909078/python-backbone \
  .artifacts/python-backbone/<新恢复报告目录>
```

恢复报告核对两实例的实际段顺序、保存前后文件和进度、探针连续性、原测试失败集合和清理，并单独列出checkpoint、restore及工具通道检查耗时。本轮两次复算一致，272份raw哈希未变；临时快照已回收，复算报告不表示仍可加载该快照。

## Sphinx #7590单路径入口（完整重放、恢复及独立评测通过，任务修复失败）

固定配置为`configs/sphinx7590-backbone.json`。复用相同的AgentENV隔离服务、Exp3控制器和持续采样，严格重放35步；原采样的141等非零退出码保留，不接受Xarray的特殊诊断规则。参考patch由固定base和五段原编辑重建；SWE-bench测试仅含1项F2P和24项P2P，没有官方源码修复。

准备或核对输入：

```bash
python3 -B scripts/prepare_sphinx7590.py \
  .artifacts/sphinx-backbone/<新输入目录> --fetch
python3 -B scripts/run_xarray6992.py --task-profile sphinx7590 \
  --inputs .artifacts/sphinx-backbone/<输入目录> --check-only
```

`--fetch`仅保存选中测试材料，使用只读rows接口；接口没有强制固定parquet revision，选中行和base文件必须与公共配置的哈希一致。已有输入可直接核对，不必重复下载。

先完成只写有界RAM的本地OCI缓存准备，再确认真实写盘窗口：

```bash
python3 -B scripts/prepare_xarray6992_image.py \
  --image 'ghcr.io/epoch-research/swe-bench.eval.x86_64.sphinx-doc__sphinx-7590@sha256:577ebfcce825e4c9621e066dc1c8375443be94bbc6de30b9368e422b8f123731' \
  --manifest configs/sphinx7590-image-manifest.json \
  --output .artifacts/python-image-cache/<新Sphinx缓存目录> \
  --ram-only-preparation
```

`--ram-only-preparation`不得同时传入窗口时间；最多850秒下载预算及有限收尾，只写私有RAM镜像和小量控制记录，不分配磁盘卷、不启动VM。原带窗口的准备方式仍可使用，但不要把下载时间挤进真实重放窗口。成功缓存保持只读，等待窗口确认时保留；完成真实运行并清理后再显式`--release`。真实入口仍必须有新确认的窗口，不能自动续期。

下载超时且失败RAM缓存仍挂载时，在同一准备命令中加入`--resume-incomplete --download-timeout-seconds 180`，可用`timeout -k 20s 240s`限制整次续传。脚本核对归属、挂载和固定manifest，只补未完成分块；已完成整层及续传后的所有整层均核对digest，不仅信任分块记录。原失败结果保存到`preparation-attempts/`，日志追加；累计准备耗时和本次请求量分别记录。成功或已释放的缓存不能按失败缓存续传。本次实际仅续传11块、约17.82 MiB，35.105秒通过。

需要64 GiB以上可用RAM，私有tmpfs最多2 GiB；13个镜像层及配置全部核对digest，统计实际tar文件量后才选择固定卷。18/20/22/24/28/32 GiB卷分别要求超过44/46/48/50/54/58 GiB主机空闲量，并且必须通过原转换预算、2 GiB卷内余量和复制后余量检查；不能只根据压缩镜像体积选卷。此次18 GiB已真实通过，解包1.89 GiB、转换估算15.59 GiB、卷内观测峰值8.38 GiB；20 GiB主机保护量和6 GiB额外余量不变。该容量不含checkpoint，不能直接沿用于恢复。

确认容量及窗口剩余时间后，先首段短验证，再同次执行剩余路径：

```bash
bash scripts/validate-isolated-storage.sh --confirmed-window \
  --window-start-utc "$ASB_WINDOW_START" --window-end-utc "$ASB_WINDOW_END" \
  --python-task-profile sphinx7590 \
  --python-backbone-inputs .artifacts/sphinx-backbone/<输入目录> \
  --python-backbone-oci .artifacts/python-image-cache/<已准备Sphinx缓存目录> \
  --python-backbone-capacity-gib <预算通过的容量> \
  --python-persistent-sampling
```

入口会在分配前拒绝容量不符及Xarray诊断例外；Sphinx保存/恢复须使用下文完整参数组合，不能与原生OCI转换选项混用。首个退出码或状态不符即失败。固定镜像本身已修改`setup.py`、`tox.ini`，对应patch冻结在`configs/sphinx7590-image-background.patch`，新入口核对已知patch哈希、工具前后两文件哈希和patch完全相同；额外改动仍拒绝，不修改这两文件。完整执行后核对任务源码及任务范围patch、七个原UDL小例子，再应用独立测试补丁执行全部25项测试；任务失败与平台运行失败分别判定。成功卷回收，失败卷保留，源服务不重启。

首次运行因最终工作区检查误判为FAIL，原raw保持不变，见[历史诊断](records/evidence/sphinx7590-final-state-failure-20261009.json)。新普通运行已通过35步、工具前后镜像文件、最终源码/patch、七个UDL小例子、全部25项独立测试执行及清理。任务测试24通过/1失败/0跳过，任务FAIL与平台PASS分别记录，见[实测证据](records/evidence/sphinx7590-backbone-20261009.json)。完整报告与曲线两次复算一致，242份raw未变。第29步保存/恢复现已另行验证，见下文；物理缓存、Slab、VMM PSS及本任务采集影响仍未验证，不能沿用旧Exp1结论。

成功raw的只读复算仍使用`report_xarray6992.py <raw目录>/python-backbone <新报告目录>`，根据冻结任务ID核对Sphinx范围。使用完缓存后，以相同的`prepare_xarray6992_image.py`和`--image`参数加`--release`回收成功缓存；失败缓存保留。脚本名称沿用既有入口，实际任务由参数和固定配置决定。准备证据见[记录](records/evidence/sphinx7590-preparation-20261009.json)。

### Sphinx第29步完整VM保存、恢复及续跑

真实运行`20261009T061949Z-3968260`已经通过保存、删除原VM、恢复到新VM、状态核对及第30–35步续跑。保存点为`core_verify`结束，核对三次原源码编辑、两份镜像背景文件、124份文件及准确的29步前缀，确认`backbone_finish`尚未执行；探针进程pid、启动时间和boot_id相同，计数5→16。最终patch与普通重放一致，独立25项测试仍24通过、1失败、0跳过。见[恢复证据](records/evidence/sphinx7590-checkpoint-20261009.json)及[曲线](records/evidence/sphinx7590-checkpoint-20261009.svg)。只验证一个探针和一处边界，成功后已删除临时快照，长期重载未验证。

本机已校验的2.67 GiB备份可提供不可变转换缓存；备份不随Git上传，全新机器不能只复制下列命令假称部署完成。准备在确认写盘窗口之前进行：有界RAM只读挂载完整备份，仅复制依赖及不可变缓存，不继承旧进程或可写层；需要超过64 GiB可用RAM，准备上限24 GiB，本次实际分配8.504 GiB、耗时81.948秒。

```bash
python3 -B scripts/prepare_prettier6604_cache.py \
  .artifacts/sphinx-backbone/<新恢复缓存目录> \
  --task-profile sphinx7590 \
  --source-backup-directory .artifacts/storage-isolated-service/20261009T050703Z-3934019
python3 -B scripts/run_xarray6992.py --task-profile sphinx7590 \
  --inputs .artifacts/sphinx-backbone/prepare-20261009T041720Z \
  --checkpoint --persistent-sampling \
  --expected-evaluation-raw .artifacts/storage-isolated-service/20261009T055637Z-3955483/python-backbone \
  --check-only
```

取得新确认窗口后，使用同一个起止时间，不能自动延长。写卷16 GiB，主机须超过42 GiB可用，复制缓存后卷内须超过14 GiB；4 GiB内存快照、可写层、恢复临时数据及清理余量已计入预算，不可变层绑定在RAM。原生OCI选项和手动容量选项不用于此恢复模式。

```bash
bash scripts/validate-isolated-storage.sh --confirmed-window \
  --window-start-utc "$ASB_WINDOW_START" --window-end-utc "$ASB_WINDOW_END" \
  --python-task-profile sphinx7590 \
  --python-backbone-inputs .artifacts/sphinx-backbone/prepare-20261009T041720Z \
  --python-readonly-replay --python-persistent-sampling \
  --immutable-cache-seed .artifacts/sphinx-backbone/<已准备恢复缓存目录> \
  --python-checkpoint \
  --python-expected-evaluation-raw .artifacts/storage-isolated-service/20261009T055637Z-3955483/python-backbone
```

入口先验证首段，再保存及恢复，原退出码严格保留，不添加`--python-diagnostic-continuation`。本次写卷观测峰值3.823 GiB、主机空闲观测最低29.403 GiB；成功卷、VM、服务和快照全部回收。成功后释放本次RAM缓存，若失败先诊断并保留失败卷及必要缓存，不覆盖原结果。

```bash
python3 -B scripts/prepare_prettier6604_cache.py \
  .artifacts/sphinx-backbone/<已准备恢复缓存目录> --release
python3 -B scripts/report_xarray6992.py \
  .artifacts/storage-isolated-service/20261009T061949Z-3968260/python-backbone \
  .artifacts/sphinx-backbone/<新恢复报告目录>
```

恢复报告及SVG两次一致，277份raw哈希未变。保存调用3.129秒，恢复调用0.142秒，恢复请求至工具检查结束0.402秒；控制器30.392秒包含恢复及评测，不包含RAM准备。guest观测峰值261.14 MiB仅覆盖工具段，host服务cgroup观测峰值7.15 GiB；两者及RAM准备缓存不能直接相加。本次复用已转换缓存，启动3.627秒不表示公平冷启动比较或缓存收益。

### 新任务缓存与采集短验证

现有短入口仅支持Sphinx前6个冻结动作，顺序创建三个VM，分别关闭周期采样、开启100 ms采样、开启采样及末尾精细分析。每次核对原命令、顺序、退出码、stdout/stderr和patch，精细分析后核对源码及未修复UDL结果保持不变。复用上述镜像备份准备入口生成新的`--task-profile sphinx7590`缓存后执行：

```bash
bash scripts/validate-isolated-storage.sh \
  --python-task-profile sphinx7590 \
  --python-backbone-inputs .artifacts/sphinx-backbone/prepare-20261009T041720Z \
  --python-readonly-replay --python-persistent-sampling \
  --immutable-cache-seed .artifacts/sphinx-backbone/<新短验证缓存目录> \
  --python-cache-smoke \
  --python-cache-reference .artifacts/storage-isolated-service/20261009T055637Z-3955483/python-backbone
```

写卷固定8 GiB，主机要求超过34 GiB，复制后卷内超过6 GiB；三VM顺序运行并逐个清理，raw预算384 MiB。成功后释放本次RAM缓存，失败源卷及日志保留。默认自动运行时间范围已通过离线检查，尚未单独启动真实VM验证该默认分支；本轮成功短验证使用当时已确认的显式时间，结果不改写。

本轮`20261009T064745Z-3982475`通过，暂停点24,066个4 KiB file-LRU页对应94.01 MiB宿主机去重物理量，普通文件、块设备及ext4分配器元数据分别列出，未知零。已知三页12 KiB计入总量；非LRU、tmpfs、匿名页、DAX内容和全部任务逐文件内容不在该范围。指定镜像backing缓存为另一个区间指标，不与冻结点相加。VMM PSS、服务cgroup和暂停前guest逻辑组成单列。精细分析8.633秒、暂停4.148秒，状态不变不代表不增加用时；三个短组没有顺序平衡或统计判定，不设置性能阈值。

只读复算和曲线生成：

```bash
python3 -B scripts/report_task_metrics.py \
  .artifacts/storage-isolated-service/20261009T064745Z-3982475/python-backbone \
  .artifacts/task-metrics/<新报告目录>
```

两次报告、哈希表和SVG逐字节一致，596份raw未变，见[证据](records/evidence/sphinx7590-short-cache-20261009.json)。没有执行其余29步或再次做checkpoint；现有完整任务和恢复证据仍以原记录为准。首次入口依赖加载失败的8 GiB源卷及约80 MiB验证备份保留，删除失败源卷需要对应清理授权。

## 持续采样与默认内存曲线

持续采集器新增guest CPU、逐块设备读写速率及对应host VM进程CPU、I/O、RSS/PSS。普通计数约100毫秒，PSS约1秒；默认报告增加 `performance-<sandbox_id>.svg` 和JSON中的 `performance`。host VM身份按本次私有服务cgroup及实际instanceID核对，历史结果缺失的新指标标为NOT_MEASURED。CPU分别使用VM总容量百分比和单核百分比，I/O为MiB/s，内存为MiB；VM进程I/O不覆盖块设备服务，PSS不是去重物理cache，guest和host不相加。整个主机CPU/I/O暂未加入这些曲线。

最新四VM、五会话的短功能验证通过；修正采样器退出回收后，两个恢复子实例的原第7–13步严格续跑通过，旧find失败保留。正常4096 MiB Prettier七实例189个动作及新增指标通过，Sphinx完整诊断变体35步及指标通过。各次范围和采集影响见[监控记录](records/SANDBOX_MONITORING.md)，不沿用旧内存采样的开销百分比。


慢命令短诊断入口为`bash scripts/validate-runtime-command.sh`，只运行Sphinx原前13个动作、6次原命令重复及4次pip版本检查对照。8 GiB固定卷要求超过34 GiB主机空间，镜像准备要求64 GiB RAM余量；失败卷保留。版本检查开关仅作用于诊断子进程，不修改正式冻结轨迹或默认采样环境。这是25毫秒进程观察与热缓存诊断，不是正式监控开销实验。

find短诊断使用`bash scripts/validate-runtime-command.sh --find-diagnostic`，关闭性能采样，执行原前13步并严格核对输出和文件状态，随后8次原find/错误捕获，以及进程活动前3次、受控活动时8次、回收后3次对照。受控活动只在自有VM内，所有短时子进程有限等待回收；不改冻结命令或正式退出码规则。这是错误机制诊断，不是监控开销实验；原前缀若有退出码差异会明确记录，诊断完成的PASS不能授予完整轨迹PASS。

`python3 -B scripts/report_sphinx_find_diagnostic.py <python-backbone目录> <新的.artifacts输出目录>`可独立复核原13步、输出、patch、任务状态、受控数据和资源清理；核对输入哈希不变，只写新报告目录。当前真实对照已验证扫描期间消失的自有`/proc/PID`能使find返回1，原正式失败的具体原因仍未确定。证据见[短对照](records/evidence/runtime-find-transient-control-20261010.json)。


诊断成功后的只读复算使用`python3 -B scripts/report_sphinx_command_diagnostic.py <运行目录/python-backbone> <新的.artifacts输出目录>`，导出经过时间、CPU、进程等待及网络套接字观察。`validate-runtime-pairs.sh --report`生成的正式五对报告另包含`tool_timing_variability`，列出全部35步各组用时、范围和配对差值，保留所有慢组。实测与结论边界见[诊断记录](records/SANDBOX_MONITORING.md)。

Prettier单路径及GRPO/BPO/TVCache、Xarray和Sphinx入口默认按实际沙箱ID持续采集guest内存。成功运行后，运行目录的`report/`自动生成page cache、非空闲内存和各实例组成曲线；完整任务报告保留原正确性和阶段时延。保存前停止采样，恢复后重新启动；暂停和工具通道可用之前没有guest数据，不插值。Cached是逻辑计数，不是跨VM去重的物理量。历史数据会明确标为仅工具执行片段。

当前段前/段后标签由guest runner本地更新，避免每段两次同步工具通道调用。持续标签waiting保留刚完成的段名和步骤999；原工具边界样本仍写complete。新变体记录`phase_updates=guest runner`，旧报告按原版本复算。Sphinx五对及优化版七实例功能复验通过；整体开销仍受单条conda/pip命令波动影响，见[优化记录](records/SANDBOX_MONITORING.md)。

仅从已结束的运行生成内存报告：

```bash
python3 scripts/report_sandbox_runtime.py <原始数据目录> <新的报告目录>
```

该报告只验证指标，不把原运行失败改成任务通过。恢复与新采样的短验证使用`bash scripts/validate-runtime-monitor.sh`，需超过42 GiB可用空间；短恢复、Sphinx完整任务和Prettier七实例GRPO均已真实通过。能力范围、实际结果和仍未验证项见[持续采样记录](records/SANDBOX_MONITORING.md)。

本机固定输入的新采样验证入口：

```bash
bash scripts/validate-runtime-monitor.sh --full-task
bash scripts/validate-runtime-monitor.sh --full-sphinx-diagnostic
bash scripts/validate-runtime-grpo.sh
bash scripts/validate-runtime-pairs.sh
bash scripts/validate-runtime-pairs.sh --sphinx-pipeline-diagnostic
bash scripts/validate-runtime-pairs.sh --sphinx-pipeline-diagnostic --sphinx-pipeline-policy-version 2
bash scripts/validate-runtime-monitor.sh --full-xarray
```

以上命令顺序运行。完整Sphinx和Xarray使用8 GiB卷，要求超过34 GiB可用；七实例GRPO用16 GiB卷，要求超过42 GiB，并在复制镜像后检查超过8 GiB卷内余量，仅支持已转换镜像及不创建快照的GRPO。不会安装依赖、重启共享服务或清空host/guest缓存；成功的本次卷自动回收，失败卷保留。Xarray为保留原0/141差异的完整诊断，不授予严格重放PASS。

这些命令是不同验证入口，不要求每次全部重跑。`--full-task`和不加诊断参数的开销入口保持严格退出码判定，已观察到首步参考141、实际0的失败。已批准的Sphinx诊断入口只对`read_repo/001`允许0或141，命令、完整stdout/stderr和首段patch须符合固定参考哈希；其余34步仍严格检查，最终状态及25项独立测试另行核对。诊断通过不会覆盖历史严格FAIL，也不授予严格重放PASS。默认规则版本1保持仅第1步；已批准的`--sphinx-pipeline-policy-version 2`另外仅对`core_patch/001`（第14步）做同样的逐项哈希核对，其余33步保持严格。版本2在独立新批次统计，不把旧未完成批次或试运行合并为五对。当前版本2新批次只完成两对，第三对关闭组第11步find失败而停止，尚未完成五对；原因诊断和容量准备完成前不建议重复完整开销命令，见[最新计划](EXPERIMENT_PLAN.md)。底层隔离入口使用`--python-sphinx-pipeline-diagnostic --python-sphinx-pipeline-policy-version 2`，任务入口使用`--sphinx-pipeline-diagnostic --sphinx-pipeline-policy-version 2`。新机器须先准备固定输入及经过验证的镜像备份，入口不是在线下载部署命令。

开销入口默认5对，交替开关顺序，每次新建一个2 CPU/4096 MiB VM并保持同一个RAM不可变镜像缓存。关闭组不运行guest边界/周期内存读取及host内存监控，两组均保留工具计时、正确性检查和空间保护。它不测试精细暂停扫描，也不预设开销阈值。仅复算已完成开销结果：

```bash
python3 scripts/run_runtime_monitor_pairs.py \
  --report .artifacts/runtime-monitor-pairs/<运行ID> \
  --output .artifacts/runtime-monitor-offline/<新报告目录>
```
