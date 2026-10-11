# 实验平台逐项验收检查

## 2026-10-10最新补充：短find机制对照完成，新增开销仍部分完成

固定Exp1三项结论不变，新版完整指标、恢复子实例和Prettier七实例功能证据有效。Sphinx正式五对依然只完成两对；第三对关闭组的原find失败保留，不增加退出码例外。本轮只做两次8 GiB短诊断：原13步严格匹配，各8次普通find重复无错误，不计完整轨迹或任务测试。

同一VM关闭性能采样时的受控对照验证了原find的环境敏感性：普通自有子进程活动时8次原命令均返回1，诊断错误全部指向扫描期间消失的自有`/proc/PID`；活动前后各3次均0，197个子进程全部等待回收，输出及任务状态不变。这证明采样器不是触发此类错误的必要条件，**不证明旧失败的具体原因**，也不代表可以忽略find退出1。先前未回收采样器的EINVAL仍是另一条已修复证据。

旧首步失败源卷已获批删除，完整备份和日志保留；两次成功资源全部回收、共享服务不变，约41.13 GiB可用。两次raw与参考409/412份文件哈希不变，重复报告相同；68项相关本地检查通过。完整任务中失败后自动捕获分支本轮未触发，明确未验证。

建议下一步改用已有Prettier冻结路径对新版采集开关先严格检查新五对中的第一对，通过后再完成其余四对；结果只覆盖该任务，需讨论测量范围，不能补记Sphinx五对通过。当前新增第三项仍部分完成，未暂存、提交或推送。见[本次证据](evidence/runtime-find-transient-control-20261010.json)及[计划](../EXPERIMENT_PLAN.md)。下方为此前状态和历史范围。

## 2026-10-10补充：完整新增指标通过，采集影响对照尚未完成

固定Exp1三项已验证通过的结论不变。新增范围已补正常Sphinx35步、两个恢复子实例原续跑、Prettier七实例189个动作及持续CPU/I/O/内存和host VMM RSS/PSS曲线。一对Sphinx采样开关通过，独立测试仍24通过/1失败，两组最终源码/patch相同；诊断模式保留原严格失败。原始数据与两次报告不变。

正式五对只完成第一对，第二对开启组通过，但关闭组第14步`core_patch/001`参考141、实际0，获批首步例外不覆盖该步，仍FAIL并自动停止。关闭组没有采样器，22步全部输出/段patch相同不代表已证明该管道原因或允许忽略退出码；剩余13步和任务测试未运行。**第三项新增范围仍部分完成，不能报告五对稳定开销或无性能影响。**无需为此撤回固定Exp1结果。

下一步仅讨论第二条固定管道的精确诊断范围，再继续未完成开销验证；不是继续增加曲线、重复恢复或七实例。首步诊断输入不改，第14步未经允许不放宽，新失败源卷和完整哈希验证备份均保留。当前暂存条件未满足，未暂存、提交、推送。详见[本轮实测及失败证据](evidence/runtime-expanded-pairs-failure-20261010.json)和[计划](../EXPERIMENT_PLAN.md)。下方各段为此前日期和范围的记录。


## 2026-10-10重新检查：固定Exp1可验收，当前扩展版尚未全部通过

**原固定Exp1三项初步验收仍已验证通过；最近增加CPU/I/O/PSS后的默认采样范围部分完成，不能据此宣布当前平台全部目标完成。** 两个结论针对不同版本和测量范围，不撤回已经核实的旧结果，也不将旧采集开销沿用到新配置。本次按照当前扩展版判断，暂存条件未满足，未暂存、提交或推送。

最初的复查只读已有服务、代码、配置及结果，离线报告写入新目录。随后按计划运行私有短诊断和恢复验证、修正采样器退出方式，实际结果补充如下；没有重启共享服务或清空缓存。当前root、host内核6.19.9+，两个研究仓库HEAD与固定配置一致。AgentENV共享盘及固定卷入口检查READY；双后端固定卷入口因约57.47 GiB低于既有60 GiB门槛拒绝，不视为恢复功能失败。它不会阻塞预算42 GiB的新采样短验证。

### 三项初步验收

| 要求 | 固定Exp1交付 | 当前新增范围 |
| --- | --- | --- |
| 完整重放单条轨迹 | 已验证通过 | 新版Prettier七实例含完整backbone的189个动作通过；Sphinx新普通运行首步严格失败，历史35步通过保留；Xarray严格FAIL |
| 指定步骤保存、恢复状态并从下一步续跑 | 已验证通过 | Sphinx、Xarray等旧指定边界已通过；原find失败记录保留；修正采样器退出后，两个恢复子实例原第7–13步严格续跑及持续采样已验证通过 |
| 内存变化、观测峰值、组成、物理缓存、时延及采集影响 | 已验证通过，限定既有Exp1计量范围 | 部分完成：新曲线及两个恢复子实例原续跑采样通过；正常Sphinx完整任务首步严格失败，正常Prettier/七实例新指标通过，新采集影响尚待验证 |

固定Exp1物理量是暂停点file-LRU集合中的host去重页，排除非LRU和guest DAX，并单列backing缓存；不是连续物理缓存曲线或完整任务/provider内容。日常Cached变化曲线为guest逻辑量。两者和VMM PSS、服务cgroup均不相加。采集允许有用时成本，必须量化且保持任务语义；这里没有新增5%或其他性能门槛。

### 本次实际核验

| 命令或检查 | 实际结果与证据 |
| --- | --- |
| `bash scripts/verify-initial-acceptance.sh` | PASS，41.456秒；20次正式、4次历史完整/恢复；8,244份输入元数据不变，复算与固定结果一致。结果：`.artifacts/initial-acceptance-delivery/20261010T040904Z-71549/result.json` |
| `bash scripts/use-platform.sh check` | READY；固定26步输入和当前AgentENV服务前提通过，不代表本次重新在线运行 |
| `bash scripts/use-platform.sh check configs/platform-exp1-isolated.json --backend agentenv` | READY；20 GiB卷与46 GiB启动门槛通过，不实际分配卷 |
| `bash scripts/use-platform.sh check configs/platform-exp1-isolated.json` | 返回1，空间保护拒绝：约57.47 GiB，要求超过60 GiB；其他前提未全部执行，不能称双后端当前READY |
| `report_xarray6992.audit()`核对Sphinx与Xarray原恢复raw | 两份离线审计PASS；指定边界、文件、进度、原探针进程与续跑检查有效。Sphinx严格重放PASS，Xarray严格FAIL；各自原模型任务FAIL仍保留 |
| `report_prettier6604_grpo.audit()`核对BPO与TVCache | 两份离线审计PASS；每组7角色、84个实际工具动作、两处保存与恢复状态核对；原七条路径任务均FAIL，不能算模型修复成功 |
| 从新指标成功及失败raw重新运行 `report_sandbox_runtime.py` | 两份指标报告PASS，来源分别PASS和FAIL；四个VM的数据完整，296/315份raw哈希未变。输出在 `.artifacts/runtime-monitor-offline/acceptance-review-20261010-success/` 和 `acceptance-review-20261010-failure/` |
| 新续跑失败的原始steps、actions及stdout/stderr | 原第11步 `trace_cpp_parser/005` 退出码1，参考0；七步输出及动作一致，另一个子实例的原续跑目录不存在。此行为本轮开始前的历史失败；随后诊断指向已停止采样器的/proc/PID/net，修正后两个子实例严格续跑通过 |
| 四组相关本地回归 | 42项通过：采集器9+8、固定交付6、平台入口19；均不操作真实服务或设备。测试中故意拒绝旧目录及模拟失败不计实际实验失败 |
| `bash scripts/validate-runtime-monitor.sh --find-diagnostic`，修正前后 | 两次诊断完成；四种前/后恢复及采样开关情况的原命令从均1变为均0，新五会话均确认采样器回收。公开摘要：`runtime-find-diagnostic-20261010.json` |
| `bash scripts/validate-runtime-monitor.sh` | `20261010T043724Z-93404` PASS：两个子实例各原7步严格通过，文件/进度/进程及四VM五会话有效；372份raw不变，两次报告一致，本次资源全部回收 |
| `bash scripts/validate-runtime-monitor.sh --full-task` | `20261010T044006Z-98974` FAIL：原首步grep/head退出0、参考141，首段6步全部输出/patch相同；其余29步及评测未运行。8 GiB失败卷与80.10 MiB验证备份保留 |
| `python3 -B scripts/diagnose_sphinx_pipe.py --raw ... --reference ... --output ...` | 本地自有管道在受控容量/顺序下，同源码及grep/head参数复现同输出对应0/141；不是guest PIPESTATUS或监控因果证据。通过输出：`.artifacts/runtime-monitor-offline/sphinx-pipeline-20261010/attempt-03` |
| 新修正相关本地回归 | 54项通过：持续采样10、CPU/I/O10、隔离入口25、Xarray9；没有操作共享服务或设备 |
| `bash scripts/validate-runtime-grpo.sh` | `20261010T044652Z-104008` PASS，2 CPU/4096 MiB×7、189个原动作及七份新增曲线完整；原任务失败保持，1,194份raw未变，两次报告一致，所有本次资源回收 |

指标报告中的PASS只表示采样数据可审计，不会把来源FAIL改成任务PASS。新成功短验证使用512 MiB小VM及独立CPU/64 MiB文件负载，原轨迹两个子实例续跑已单独严格验证；不能替代正常4096 MiB完整任务或七实例验证；本轮另有正常Prettier七实例实际证据。本次没有重跑已经通过的正式Exp1对照。

### 平台总体目标

| 目标 | 状态 | 实际能力及缺口 |
| --- | --- | --- |
| 本机沙箱启动、模板与checkpoint管理 | 部分完成 | 已有AgentENV完整VM恢复与模板子实例成功证据；TrEnv-X仅文件继承。全新机器部署、任意任务统一入口未验收 |
| 接入和重放已有轨迹 | 部分完成 | 固定Exp1与三个PPT任务已有适配及真实数据；任意新任务仍需输入、环境及正确性适配 |
| 在线轨迹收集 | 未实现 | 现有测量执行冻结工具，不重新请求模型；没有在线收集闭环的运行证据 |
| GRPO/BPO/TVCache | 部分完成 | Prettier三种调度形状都有真实功能证据；新CPU/I/O/PSS七实例GRPO完整路径通过，BPO/TVCache重复保存覆盖未验证，不表示RL训练或完整线上缓存系统 |
| PPT三任务总内存、物理缓存和时延比较 | 部分完成 | 三任务有执行、逻辑内存与阶段耗时；统一范围的完整物理缓存和匹配比较未完成，模型修复失败单列 |
| page cache控制及因果时延比较 | 部分完成 | 历史Exp1控制与观察保留；三任务同任务、轨迹、资源、rollout、冷热状态的因果对照尚未完成，不能从不同组差异直接归因缓存 |

初始只读复查见[本次核验摘要](evidence/platform-acceptance-review-20261010.json)；本轮后续真实验证以[恢复成功](evidence/runtime-managed-restore-20261010.json)和[正常任务失败](evidence/sphinx-runtime-prefix-failure-20261010.json)为准。这些后续平台和研究目标分别列状态，不以在线采集、新机器部署或RL训练作为拖延固定初步验收的条件。

### 当前验收前最少需要补齐的工作

1. **find退出码差异已定位并修正。** 保持原动作、退出码规则和输出，另用短诊断副本记录stderr和find/head各自退出码。比较相同VM配置下恢复前后和采样开关，先检查是否为临时进程目录或真实文件/I/O错误；没有证据前不归因，不忽略异常码，不无改动重复长测。
2. **两个恢复子实例原轨迹续跑及持续采样已完成。** 文件哈希、进度、原探针进程身份及计数、下一段执行位置、输出/退出码和各自采样归属都须通过。父实例保存后采样重启、子实例各一份采样及定向清理分别检查。
3. **先确认正常Sphinx首步0/141的解释规则，并完成独立并发验证。** 新首步严格失败已保留；是否新增仅此固定动作、状态和输出完全相同的诊断变体待确认。不得把诊断通过写成原严格通过。 正常Sphinx完整任务和一次七实例GRPO分别只跑一组功能验证；通过后先做一对开关短验证，再按此前约定五对报告任务、生命周期和采集自身用时。冻结环境、镜像、资源和缓存设置在两组相同，原模型失败集合必须保持。给出实际变化及不确定范围，不要求得到零开销或某个百分比。
4. **达到上述范围后再准备提交。** 复算、原始输入不变及资源清理通过，更新证据和说明，再检查并暂存必要源码、配置、测试和公开报告。排除AGENTS、凭据、镜像、状态数据库和未脱敏日志，由用户提交、推送。在线收集和跨任务物理共享收益后续单独推进。

下方为原固定交付及历史检查，日期和范围以各段标注为准。


## 固定初步验收交付

本次交付不新增VM实验，只将已有本机Exp1结论、范围、输入及复算方法固定下来。当前三项判定见下表；平台总体进度见[STATUS](../STATUS.md)。全新机器部署、PPT三个任务、在线采集和完整DAX/provider归属不计为通过。

```bash
bash scripts/verify-initial-acceptance.sh
```

也可指定专用目录下的新一级输出目录：

```bash
bash scripts/verify-initial-acceptance.sh \
  --output .artifacts/initial-acceptance-delivery/my-new-check
```

固定配置为[initial-acceptance-delivery.json](../../configs/initial-acceptance-delivery.json)。它绑定两个后端的最终五对实验结果目录、四次历史完整/恢复配置、针对性短验证及资源收尾记录，并锁定输入摘要和统一公开结果的SHA-256。原始RAM/PFN、日志及镜像仍在本机，不上传GitHub。

入口调用`report_kernel_acceptance.acceptance()`，重新解码物理页、核对26步轨迹及任务检查、重算曲线和时延，并重新审计四次历史运行。预期20次正式运行和4次历史运行；短验证摘要另行核对，故意触发的严格区间FAIL不会改为成功实验。本次不是重新在线checkpoint或跨服务恢复。

审计阶段的Python写入、目录修改和子进程会被拒绝，同时核对已有输入的inode、大小、mtime、ctime和权限不变。此保护针对当前Python离线路径，不替代系统级只读挂载。不得改用会重写报告的历史CLI或报告函数来冒充只读检查。

每次只写新目录中的`recomputed.json`和`result.json`；重算结果必须与固定公开证据逐项一致才返回退出码0。已有目录、输入哈希变化、缺失数据、写入尝试或结果不一致均失败；超时上限240秒，缺少最终PASS也失败。原始数据不随仓库同步，没有本机raw时应失败，不能仅凭README或公开摘要通过。

交付验证的实际run_id、回归检查和保护范围记录在下方“交付入口验证”；精细分析的耗时、原始失败及测量范围继续以下述原始实验报告为准。

## 交付入口验证

`bash scripts/verify-initial-acceptance.sh`实际退出0，结果目录为`.artifacts/initial-acceptance-delivery/20261007T053525Z-3420756`。已离线核对20次正式运行及4次历史完整/恢复运行；8244个已有输入文件的标识、大小、时间和权限保持不变，重算JSON与固定证据逐项一致。复算耗时41.75秒，是本机读数，不是沙箱性能结果。详见[交付验证证据](evidence/initial-acceptance-delivery-20261007.json)。

针对保护行为的6项回归及原验收失败判据的3项回归通过，bash语法检查通过。再次用相同`--output`调用实际返回2，旧结果内容与mtime/ctime不变；禁止向历史raw目录创建输出的回归也通过。每次仍使用新目录，不重新运行旧实验。

本次仅新增复算入口、固定配置和交付记录，原始轨迹、checkpoint、日志、样本及旧验收JSON不改写；当前和历史文档已分开显示，历史内容保留。AGENTS.md仍只在本地，不暂存、提交或推送。

## 2026-10-07：本机Exp1第三项验收收尾

**按本轮固定的“已准备好的本机、单条冻结Exp1及明确计量范围”，三项初步验收已验证通过。** 第一、二项复用并重新离线核对原始恢复/续跑证据；第三项完成4个针对性短验证、两后端各五对共20次正式完整重放、独立复算、曲线与阶段成本报告及资源收尾。详见[统一验收证据](evidence/third-acceptance-final-20261007.json)。该结论不代表全新服务器部署、PPT三个任务或多VM共享收益已验证。

校准协议为`exact-kernel-frozen-v1`：暂停点核对全SystemRAM内file-LRU（位于内存回收队列中的文件页，排除匿名和tmpfs页）及实际host物理页，已知文件内容/设备/inode/偏移校准，恢复后记录缓存变化并检查内容与任务。只回收本次3个校准页的短验证中，旧区间检查FAIL，但源码/patch和扫描后官方4个断言PASS；新冻结点协议也通过该短验证。**旧FAIL原样保留，恢复后的缓存变化仍报告，不要求缓存永久驻留。** 非LRU页和guest DAX内容不包含在file-LRU点中；host镜像、RAM快照与元数据缓存另列，provider匿名内存及全部任务逐文件归属仍未测，不相加或外推共享收益。

| 验收内容 | 状态与实际证据 |
| --- | --- |
| 完整单轨迹 | 已验证通过：20次均为Prettier #14400的26个冻结动作，008/016预期失败、关键输出、源码/patch、独立功能和官方4个断言均检查；不是重新请求模型 |
| 指定步骤恢复续跑 | 已验证通过：原始AgentENV 013后续跑、进程连续性及TrEnv-X 022文件继承/续跑证据重新审计；本批无新增checkpoint，不能当作本批重新在线恢复 |
| 内存曲线/组成/观测峰值 | 已验证通过：guest runner窗口与host分开采样，目标100ms；同点组成及余量复算，guest停止采样区间不插值；峰值为样本最大值 |
| 物理page cache | 已验证通过，限定集合：两后端各5个最终暂停点、类型和host未知均0，逐hostPFN去重；AgentENV范围151.188–310.723 MiB，TrEnv-X 36.938–37.043 MiB；普通文件/块设备/分配器元数据分开，DAX/backing集合另列 |
| 阶段时延和采集影响 | 已验证通过：保留工具/创建/保存/恢复/分析/检查/清理已有阶段；每次最终任务与清理通过，新增分析五对影响实际量化；缺失阶段填null，不宣称零成本 |

以下取每个后端第一对的开启分析运行为例。内存单位为MiB（1 MiB = 2²⁰字节），guest总量是`MemTotal - MemFree`的观测峰值，组成及占比取同一采样点。其余运行逐项保留在JSON中。

| 运行 | guest有效样本数 | guest观测峰值 | 文件缓存及buffer | 匿名内存 | slab | 其余及未分类余量 | host观测峰值 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| AgentENV | 23 | 388.30 | 229.76（59.17%） | 89.98（23.17%） | 41.18（10.60%） | 27.39（7.05%） | VMM PSS 760.44 |
| TrEnv-X | 25 | 298.27 | 30.67（10.28%） | 89.21（29.91%） | 60.24（20.20%） | 118.16（39.61%） | 沙箱cgroup 410.37 |

“其余”包括共享内存、内核栈、页表、percpu和未分类余量，不能再把其中细项加到合计。host分别来自VMM的`smaps_rollup`及本次沙箱cgroup，范围不同，不作直接比较；AgentENV共享服务cgroup单列，不能算作单个沙箱用量。guest逻辑缓存、暂停点物理缓存和host用量不相加，也不以各自不同时间的峰值计算占比。

| 最终精细分析对照（秒） | AgentENV关闭/开启 | TrEnv-X关闭/开启 |
| --- | ---: | ---: |
| guest动作合计均值 | 1.789 / 1.780 | 1.918 / 1.859 |
| runner调用均值 | 2.279 / 2.296 | 3.692 / 3.633 |
| 任务及最终检查窗口均值 | 11.648 / 24.475 | 40.714 / 63.466 |
| 全程均值 | 17.896 / 31.791 | 128.098 / 149.607 |
| 开启组精细分析阶段均值 | 12.023 | 21.951 |

五对用时比先取对数求平均，再换算为百分比（几何平均）：AgentENV全程+77.19%（95%区间+48.27%至+111.76%），TrEnv-X全程+16.81%（95%区间+12.39%至+21.39%）；任务及检查窗口分别+110.38%（95%区间+92.24%至+130.23%）、+55.92%（95%区间+46.20%至+66.27%）。五对全部纳入，没有因波动剔除或加轮。两组都开周期采样，TrEnv-X两组均有同一重放前校准；本批只量化新增最终分析。轻量采样、恢复组合与旧扫描的原有开销证据保留，不相加或用旧百分比替代。PPT无性能阈值；任务正确及成本已量化，不表示零开销或缓存不变化。两后端流程/host内存范围不同，不作跨后端速度或共享收益比较。

AgentENV实际boot开启DAMON，VMM开启free-page reporting；TrEnv-X实际cmdline/DAX、内存和balloon配置见摘要。仅验证配置与两组一致，未隔离它们的因果作用。host缓存保留，创建新guest不证明host冷缓存；CPU策略相同但动态频率/其他负载仍未控制。

AgentENV末尾原复算因两次磁盘分配空间读数各增加4KiB退出1；功能、计时、原始物理数据及配置一致。修正版仅将变化的分配空间读数单列，并重新检查留存预算；其他结果和输入仍须逐项完全一致，3项回归及原始数据复算通过。`offline-audit-corrected.json`独立记录，不覆盖原结果或重跑。TrEnv-X正式离线审计通过。收尾脚本首次将VMM相对路径当绝对路径而FAIL，原记录保留；修正后使用已捕获PID及剩余目录的原VMM归属复核通过。41项相关回归、Python/bash语法验证通过，失败/缺失结果不能计PASS。

收尾`.artifacts/final-kernel-closeout/20261007/verified/result.json` PASS：20次正式运行及4个短验证的任务/清理证据核对；项目API为空，原12个Firecracker/CPU策略不变，无本批VMM/工作目录/cgroup/loop遗留。成功非checkpoint镜像按授权回收，原始数据、配置和哈希保留，参考checkpoint和前次失败归档哈希正确。正式raw 3.073 GiB、短验证raw 0.468 GiB，正式连续最低观测空闲31.58 GiB、最终42.65 GiB，高于20GiB保留线；未全局清缓存、重启服务、修改内核、暂存或提交。

逐后端摘要及示例曲线：[AgentENV](evidence/agentenv-kernel-controls-20261007.json)、[曲线](evidence/agentenv-kernel-controls-20261007.svg)；[TrEnv-X](evidence/trenvx-kernel-controls-20261007.json)、[曲线](evidence/trenvx-kernel-controls-20261007.svg)。每个后端10次运行的阶段汇总、样本统计和物理点在相应JSON；完整逐样本原始数据保留在本地raw目录。复算入口为`scripts/report_whole_analysis_controls.py`和`scripts/report_kernel_acceptance.py`，拒绝覆盖已有输出；正式入口为`bash scripts/run-kernel-analysis-controls.sh agentenv|trenvx`，已完成，本批不重跑。

后续研究：PPT三个任务、已有rollout真实多路径运行、全任务/DAX/provider完整归属、多VM去重与收益、在线轨迹采集及全新机器部署。它们分别保留未验证/部分完成状态，不扩大本轮初步验收结论。以下为历史过程。

<details>
<summary>历史验收过程（点击展开；以下不是当前判定）</summary>

## 历史记录（2026-10-07）：元数据来源通过，区间复查失败单列

E33新增[实际指针/字节证据](evidence/allocator-cache-followup-20261007.json)与[详细记录](FILE_PHYSICAL_MEMORY.md#2026-10-07类型0页已确认新任务复查出现缓存回收)。两后端inode类型0确认是ext4分配器元数据；新TrEnv-X完整26步、扫描后官方4个断言、物理复算与清理PASS。AgentENV短校准PASS，新完整任务暂停点核对PASS但恢复后3页变为不驻留，严格区间复查FAIL，扫描后任务检查未执行；原因未隔离，不能归因DAMON或监控。第三项仍部分完成，新方案匹配影响未测；先补失败后的独立任务检查与明确校准范围，再运行五对。旧成功及旧FAIL原样保留。

## 历史记录（2026-10-07）：内核选择范围修正

E29/E30旧新内核选择器混用了位号：内部位5为活跃页，不是LRU；旧115.953 MiB“完整缓存”范围判FAIL，原始数据和其余任务检查不改写。修正版已在两后端短校准和完整任务成功，准确file-LRU物理范围PASS，见E31/E32。第三项仍部分完成，剩余类型/非LRU/DAX及全部任务归属和新方案匹配影响尚未补齐；不再把TrEnv-X内核桥接列为未验证，也不把两后端file-LRU差值当作共享收益。



检查日期：2026-10-01，服务器 `cpu-15`，工作目录 `/home/yyxie/agentenv_experiment`。

**三项初步验收：完整单轨迹重放已验证通过；指定边界保存、恢复并续跑已验证通过；完整内存指标、阶段时延及采集影响只部分完成。** 先前“第一版本机闭环完成”的范围较窄，不代表本次列出的全部要求均已通过。

下文 C1–C8 的初始检查只执行服务查询、输入校验和离线复算，没有启动 VM 或修改平台代码。后续按用户授权补充的在线实验见 E7–E32 及对应报告；最新补充日期为 2026-10-06；未重启共享服务、终止其他实验或清空主机缓存，未暂存、提交或推送。

## 1. 检查依据与本次实际验证

项目 Git HEAD 为 `de4c80658a9a7abeaddc844f98ec06265953347d`，工作区有大量既有未提交修改和未跟踪脚本；运行结果还包含对应源码快照，不能仅用 HEAD 代表所有实际运行代码。固定研究源码为 IncrementalDAX `a9bc7deadb68a6e24b7242eab343dfedf953ea38`，Cloud Hypervisor 为 `9e0056eb750096f3bf8ed491c345ef9241fd527e`。

当前 root 权限、host 6.19.9+、4 KiB 页、KVM 设备打开、pagemap 物理页编号可见、kpageflags/kpagecount 可读及固定 TrEnv-X 依赖检查通过。`/dev/ublk-control` 存在，cgroup v2 有 memory/cpu/io 等控制器。本次没有新 guest，不能用 host 版本代替 guest 功能检查。历史 AgentENV guest 为 6.1.175，TrEnv-X guest 为 6.1.134。

项目 AgentENV 服务仍运行，镜像 ID 为 `sha256:db9eb044ae3d85f9b276b9d2e5dea28cb0be95263d217ea4989f095e1d828681`。当前项目 API 中沙箱和模板列表均为空，查询退出码均为 0。进程检查有 12 个 Firecracker：4 个属于项目服务 cgroup，8 个属于其他 cgroup。前者与 API 活跃沙箱不是同一计数，可能包含池实例；本次没有逐个认定为空闲，也未操作任何 VM。未找到正在运行的本项目 Exp1/Exp2/Exp3 控制器，不代表整机没有其他任务。

初始检查时磁盘空闲约 51.6 GiB；E9 收尾为约 47.83 GiB，低于下一次完整 checkpoint 实验既有 48 GiB 启动要求。本次小型只读检查可执行，不据此声称正式多实例批次空间足够。

本次结果保存于本机 `.artifacts/platform-review-20261001T115030Z/`：

| 编号 | 实际执行命令或检查 | 实际结果 |
| --- | --- | --- |
| C1 | `date -Is`、`uname -a`、`df -h .`、`ps`、`git status --short`、`git rev-parse HEAD` | 核对机器、空间、现有进程和未提交内容；未修改 |
| C2 | 下方 `review.py` 的 `live`；调用 `check_file_physical_environment.preflight()` 及 `runtime/bin/aenv list/template list --output json` | 环境 READY；服务运行；两个 API 列表均为空 |
| C3 | `review.py` 的 `closeout`；调用 `report_v1_closeout.audit()` | 四次成功运行重新审计 PASS，与本机及公开摘要完全一致 |
| C4 | `review.py` 的 `historical_exp1`；调用 `audit_exp1_metrics.audit_run()` | 配置列出的 23 次既有成功 Exp1 离线审计全部 PASS |
| C5 | `review.py` 的 `monitors`；TrEnv-X 审计函数及 AgentENV 原始动作、patch、计时和五组重新计算 | TrEnv-X 40 次及 AgentENV 两种配置各 40 次核对通过，区间与已保存结果一致；默认配置结论仍不确定 |
| C6 | 三个 workload 的 `python3 -B …/plan.py` | 均退出 0，输出 `frozen BPO trace valid`；只证明冻结输入结构和命令哈希一致 |
| C7 | `review.py` 的 `physical_recounts`；调用 `measure_file_physical.audit_run()` | 真实 Exp1 两个边界、冷/热双 VM 两个目标文件结果共四份复算 PASS |
| C8 | 从原始 guest/host TSV 重算峰值和同一采样点组成；核对 DAMON raw、六批目录缓存诊断哈希 | 重算 PASS；DAMON 12 次缓存计数与摘要一致；缓存检查另存 `cache-review.json` |

本次主要检查的可重跑命令如下；输出文件必须换新名称，入口拒绝覆盖已有输出。脚本只位于本机检查目录，不是平台部署入口：

```bash
PYTHONDONTWRITEBYTECODE=1 timeout 45s python3 -B \
  .artifacts/platform-review-20261001T115030Z/review.py \
  .artifacts/platform-review-20261001T115030Z/review-new.json
```

七个检查分项均输出 PASS，实际退出码 0。原始文件和读数能复算，不等于本次重新进行了在线实验。部分旧报告生成函数会重写原报告，本次没有执行这些写入入口。

## 2. 逐项验收表

证据位置：

- E1：[本机四次收尾摘要](evidence/v1-closeout-20261001.json)及 `.artifacts/v1-closeout-20261001/audit-config.json` 指向的 raw/audit；恢复事件、进程记录、步骤 TSV、任务测试和清理均在对应目录。
- E2：[指标审计](EXP1_METRICS_AUDIT.md)、`configs/exp1-metrics-audit.json` 指向的 23 次 raw；本次 C4/C8 重新读取。
- E3：[AgentENV 关闭 DAMON 监控数据](evidence/exp1-monitor-damon-balanced-20261001.json)、[默认配置数据](evidence/exp1-monitor-default-balanced-20261001.json)、[TrEnv-X 数据](evidence/trenvx-monitor-balanced-20261001.json)；本次 C5 复算。
- E4：[文件页记录](FILE_PHYSICAL_MEMORY.md)、[真实任务单文件结果](evidence/trenvx-exp1-file-diagnostic-20260929.json)、[冷/热计数](evidence/file-content-accounting-20260929.json)；对应 raw 位于 `.artifacts/trenvx-service-smoke/20260929T031621Z-2369653/` 和 `.artifacts/dax-restore-diagnostic/20260929T030746Z-2366128/`、`20260929T030848Z-2366619/`。
- E5：`IncrementalDAX_moti/motivation/experiments/exp3-RL-fork/` 的 controller、采集器、采样脚本及三个 workload；历史派生结果在 `IncrementalDAX_moti/motivation/results/exp3-RL-fork/`，raw 不在本机 checkout。
- E6：`IncrementalDAX_moti/motivation/experiments/exp2-multi-rounds/`；历史比较在 `motivation/results/exp2-multi-rounds/summary.md`，raw 缺失。
- E7：后续实测[guest 采样与内存摘要](evidence/guest-memory-20261001.json)、[指标说明](EXP1_METRICS_AUDIT.md#2026-10-01guest-周期采样及第三项初步验收)。新增 45 次成功运行；五组采样对照和物理地址映射已复算，当前物理文件页分类仍 PARTIAL。
- E8：后续实测[持续采集摘要](evidence/guest-persistent-memory-20261001.json)及[最新指标报告](EXP1_METRICS_AUDIT.md#2026-10-01持续采集的五组对照和页类型复查)。四次短验证、60 次同批对照、物理复查、恢复及 TrEnv-X 接入，共 67 次成功运行；新报告 CLI 离线复算通过。

- E9：后续实测[命名文件摘要](evidence/exp1-named-files-20261001.json)及[归属报告](FILE_PHYSICAL_MEMORY.md#2026-10-01命名任务文件的归属验证)。AgentENV 两次子集验证通过；TrEnv-X 文件来源检查失败，修正版仅回归通过，真实 VM 未复验。失败归档约 3.01 GiB、清理通过；收尾空闲 47.83 GiB，低于既有 48 GiB 启动要求。

- E10：修正版[三文件复验摘要](evidence/exp1-named-files-followup-20261001.json)及[完整报告](FILE_PHYSICAL_MEMORY.md#2026-10-01trenv-x-三文件真实复验通过)，raw 为 `.artifacts/trenvx-service-smoke/20261001T153457Z-3020904/`。26 步、文件恢复与续跑、三个文件来源/内容、两次物理扫描、官方四断言、归档及清理通过；guest 为四个边界，host 1123 条。本轮未开展新的监控影响对照。

- E11：2026-10-02 补充[TrEnv-X 持续采集摘要](evidence/trenvx-persistent-monitor-20261002.json)、[顺序图](evidence/trenvx-persistent-monitor-order-20261002.svg)、[内存曲线](evidence/guest-persistent-trenvx-composition-20261002.svg)及[指标报告](EXP1_METRICS_AUDIT.md#2026-10-02trenv-x-持续采集对照)。八次短验证、60 次正式运行及独立复算通过；关闭/关闭仍有用时差异，不判为监控无影响。收尾资源检查 PASS、空闲 50.88 GiB；无新增 checkpoint 或物理扫描。

- E12：2026-10-02 [关闭对照复查](evidence/trenvx-off-controls-20261002.json)及[统计分布图](evidence/trenvx-off-controls-20261002.svg)。重读 60 次 raw 和 30 次关闭配置，初始树及有效环境一致；7,776 种分组标签分配与已有 SciPy 的三个分布一致，五项测试通过。任务窗口尾部比例 22.3%、工具之和 3.9%，属事后分析，未作多重比较校正；不判为稳定偏差或无影响。没有新增 VM、checkpoint 或物理测量；计时缺口和下一项校准见[报告](EXP1_METRICS_AUDIT.md#2026-10-02关闭对照的统计复查)。

- E13：2026-10-02 [TrEnv-X 内部阶段计时摘要](evidence/trenvx-runner-phases-20261002.json)、[阶段图](evidence/trenvx-runner-phases-20261002.svg)及[报告](EXP1_METRICS_AUDIT.md#2026-10-02trenv-x-内部计时校准和四次诊断)。32 次校准、四次新重放、正确性/官方任务四断言、独立复算和清理通过。初始 reset 约 10.05 s、CPU 0.049–0.060 s；准备与整理合计约占 runner 调用 85.72%。023/024 是工具差值的主要位置，等待及波动原因未明；不是新增开关监控对照，无新增内存/物理页/恢复证据。

- E14：2026-10-05 补齐[Git 等待诊断和失败收尾](EXP1_METRICS_AUDIT.md#2026-10-05git-等待诊断与中断后的收尾)。10 月 2 日成功的一次重放、官方四断言及[八个 Git 命令计时](evidence/trenvx-git-wait-20261002.json)已复算；两个 reset 的 poll 等待已定位。追加身份对照 FAIL，guest 命令细节未下载，不能证明 DNS 原因。模板归档哈希与两次资源清理复核通过；第三项仍部分完成。

- E15：2026-10-05 [身份诊断修正版](evidence/trenvx-git-identity-20261005.json)及[报告](EXP1_METRICS_AUDIT.md#2026-10-05身份诊断修正版通过确认-git-的-dns-等待)。七项回归、一次新完整重放/官方四断言、数据下载、独立复算和清理通过。原配置 reset 平均 10.056710 s、指定身份 0.047966 s，等待描述符指向 UDP DNS 端口 53。默认身份拒绝 128 已正确保存；旧 FAIL 不改。等待原因已确认，但新固定身份重放配置及其监控影响尚未实现/验证，物理缓存和恢复影响仍缺。

- E16：2026-10-05 [固定身份配置五次重放摘要](evidence/trenvx-fixed-identity-20261005.json)及[报告](EXP1_METRICS_AUDIT.md#2026-10-05固定身份重放及四次采集对照)。三项新回归、五项持续采集回归、一次功能 smoke、四次双采集开关对照均通过正确性、官方四断言、独立复算和清理。窗口差值 +23.83%，关闭/关闭自身 +36.83%，仅为一组小样本描述，不判监控稳定成本。两次新逻辑内存曲线与同点组成保留；物理页和恢复未测，旧统计不改。

- E17：2026-10-05 [生命周期诊断摘要](evidence/trenvx-runner-lifecycle-20261005.json)及[分段报告](EXP1_METRICS_AUDIT.md#2026-10-05guest-执行和采集生命周期的分段诊断)。两项回归、guest 32 次小负载校准、一个完整 smoke 和四次诊断通过全部正确性、官方四断言、复算及清理。采集启动/退出平均 76.524/6.878 ms，控制器余量 1.152–1.215 s；新窗口差值 −2.44%，仅为诊断小样本，不判无影响。已分别统计冻结轨迹窗口与包含任务测试的 host 观测峰值；旧统计和 E16 结果不改。

- E18：2026-10-05 [固定计算量摘要](evidence/guest-collector-calibration-20261005.json)、[图表](evidence/guest-collector-calibration-20261005.svg)及[详细报告](EXP1_METRICS_AUDIT.md#2026-10-05固定计算量的-guest-采集对照)。三项回归、一个 VM 内四次短验证和 32 次正式对照、随后一次完整 Exp1 功能重放、官方四断言、独立复算及清理通过。host 周期采样关闭，计算本身 +0.078%，整个 guest +4.621%，控制器调用 +2.491%；启停平均合计 82.443 ms。正式 305 个 guest 逻辑样本组成可复算。单 VM 合成负载仅说明此配置成本，不代表真实任务或恢复/扫描影响，第三项仍部分完成。

- E19：2026-10-05 [文件来源及复算摘要](evidence/file-origins-20261005.json)与[详细报告](FILE_PHYSICAL_MEMORY.md#2026-10-05多层文件来源真实可写层与隐藏-pfn-检查)。七项来源回归、修正版真实 host 三层覆盖/删除验证，以及一条完整 Exp1、官方四断言和三文件来源验证通过。首轮 host UUID 拒绝保留 FAIL；普通重放的修改源码来自 upper，另两文件来自 base。另修正隐藏 host PFN 的离线复算防护，七项相关回归通过；两个历史 raw 无零 PFN，复算仍 16/36 KiB。没有新增物理计数、guest 多封存层或恢复影响证据，第三项仍部分完成。

- E20：2026-10-05 [私有 KVM 捕获及真实失败摘要](evidence/trenvx-upper-file-physical-20261005.json)与[详细报告](FILE_PHYSICAL_MEMORY.md#2026-10-05私有-kvm-捕获与可写文件测量的失败边界)。4 KiB 注册 fixture 的身份和命名空间排除验证通过，不含 guest 启动。两次真实 26 步和官方四断言通过，但物理测量分别在页大小遗漏、覆盖层映射设备号检查处 FAIL；修复及 18 项回归、实际 raw 离线关联通过，尚未新 VM 复验。host 物理量 NOT_MEASURED，第三项仍部分完成。清理和失败归档通过，空闲约 38.00 GiB，单次 40.125 GiB 启动受阻。
- E21：2026-10-06 [真实可写文件摘要](evidence/trenvx-upper-file-physical-20261006.json)及[报告](FILE_PHYSICAL_MEMORY.md#2026-10-06可写源码文件的真实物理页验证通过)。完整 26 步、patch、独立功能、官方四断言、实际 RAM 注册、guest/host 页核对、独立复算及清理通过；一个 upper 文件 4 页、16 KiB，未知页零。周期采样关闭，无 checkpoint，不代表全任务物理量或采集影响验证。旧失败保留。
- E22：2026-10-06 [三文件组合监控](evidence/trenvx-three-file-monitoring-20261006.json)及[文件报告](FILE_PHYSICAL_MEMORY.md#2026-10-06周期采样与三文件联合测量通过)。20/626 样本的 smoke 及 23/623 样本的扩展均 PASS；三文件 9 个去重文件页、36 KiB、未知零，扫描后补丁与官方四断言复检通过。不是全任务缓存或性能影响对照。
- E23：2026-10-06 [恢复组合摘要](evidence/trenvx-checkpoint-monitoring-20261006.json)及[指标报告](EXP1_METRICS_AUDIT.md#2026-10-06组合监控与恢复验证完成)。第 022 步保存，源码/进度保留，023–026 续跑、最终 patch、官方断言、独立复算及清理 PASS；231 个 guest / 1115 个 host 样本，保存 11.83 s，恢复至工具可用 2.30 s；三文件为 32 KiB 文件页 + 4 KiB 匿名副本。新归档保留，授权清理旧三份失败镜像，收尾约 46.29 GiB。guest 停止区间标明；本组合性能对照尚未执行。
- E24：2026-10-06 [整沙箱缓存摘要](evidence/trenvx-whole-cache-20261006.json)及[范围报告](FILE_PHYSICAL_MEMORY.md#2026-10-06扩大到整台沙箱的缓存观测)。扩展到实际 guest RAM、私有 rootfs/upper 及内存镜像 backing；最新完整重放、扫描后功能、官方四断言、复算和清理 PASS。末次 guest 9,403 页、36.73 MiB、未知零；host 文件系统镜像约 3.51 GiB，内存镜像 cache 另列约 4 GiB。区间有 9 个变化页，元数据和全部任务内容未逐项归属，不是原子快照。分析阶段 12.91 s，自身匹配影响仍未测；恢复组合的对照已完成，见 E25。
- E25：2026-10-06 [五对恢复组合脱敏摘要](evidence/trenvx-combined-checkpoint-controls-20261006.json)及[详细报告](EXP1_METRICS_AUDIT.md#2026-10-06五对恢复组合对照完成)。实际 `bash scripts/run-trenvx-combined-controls.sh --mode checkpoint` 退出 0，10 次恢复/进度/续跑/最终任务/清理及独立复算 PASS。task+checks +11.34%（近似区间 +10.13%～+12.56%），全程 +6.97%，不是纯周期采样成本；额外检查和扫描平均 8.72 s。guest 1,148 / host 5,628 样本，三文件未知零。十次成功镜像按授权回收，raw 12.15 MiB，收尾空闲约 48.39 GiB；保留参考恢复归档且哈希再检 PASS，原 12 个 Firecracker 不变。当前组合的匹配对照缺口已补齐。
- E26：2026-10-06 [AgentENV 五对分析对照](evidence/agentenv-whole-analysis-controls-20261006.json)、[曲线](evidence/agentenv-whole-analysis-controls-20261006.svg)及[范围报告](FILE_PHYSICAL_MEMORY.md#2026-10-06agentenv-同范围缓存观测与五对分析对照通过)。实际 RAM、digest 绑定的26个文件、完整26步和扫描前后源码/patch/官方断言/清理均通过；十次对照及复算退出0。前后 guest 未知页零，host 新增/消失驻留页零；guest 集合变化保留 PARTIAL。只开关分析、周期采样相同：平均6.05 s，任务窗口+52.16%，全程+33.82%；控制器最大 RSS 约990–993 MiB，不含在服务 cgroup 中。三个更早真实失败保留，不改写 PASS。
- E27：2026-10-06 [TrEnv-X 五对分析对照](evidence/trenvx-whole-analysis-controls-20261006.json)、[曲线](evidence/trenvx-whole-analysis-controls-20261006.svg)及[分析成本和收尾](EXP1_METRICS_AUDIT.md#2026-10-06整沙箱分析自身的匹配开销)。十次完整重放、源码/patch、扫描后官方四断言、物理复算和清理 PASS，实际入口与报告退出0。只开关整沙箱分析，双方保留相同三文件核对和周期采样；平均11.95 s，窗口+32.36%，全程+12.31%。guest 末次36.727–36.734 MiB，未知零，变化页排除，非原子区间保留 PARTIAL。两批合计615.51 MiB raw，最低空闲36.53 GiB、收尾51.16 GiB，本次资源无遗留、原12个 Firecracker 和参考恢复归档保留。精细扫描影响的独立证据缺口已补。

- E28：2026-10-06 [原 VM 暂停扫描摘要](evidence/paused-cache-boundaries-20261006.json)与[详细报告](FILE_PHYSICAL_MEMORY.md#2026-10-06两后端原-vm-暂停期间的缓存核对通过)。两后端各一次实际入口退出0，暂停4.08/6.56秒，选定缓存308.95/36.73 MiB，变化/未知页均零；原计数恢复、源码/patch、官方四断言、独立复算、10项回归与清理通过。恢复后新增24/2页，区间仍 PARTIAL；全任务内容归属仍null。本轮raw约125.34 MiB、收尾51.01 GiB，旧VM、CPU策略及参考镜像保留。没有新增配对开销或checkpoint批次。

- E29：**完整file-LRU范围已在E31撤回，原始所选集合数据保留。** 2026-10-06 [可见文件与内核冻结点脱敏摘要](evidence/file-inventory-kernel-cache-20261006.json)、[范围报告](FILE_PHYSICAL_MEMORY.md#2026-10-06暂停后的内核文件缓存归属与物理页核对)。已知冷热/硬链接校准、完整根目录调查、准确内核校准和完整26步共四次PASS，两次API/符号FAIL保留。新冻结点29,684页、115.953 MiB，普通文件91.223、块设备24.633、未解释0.098 MiB；host映射未知零，原VM计数恢复、源码/patch、官方四断言、清理及六次独立复算PASS。17项离线回归通过。根目录仅核实稳定区间16.94%，不外推全缓存；旧暂停前分类区间PARTIAL不改写，全任务内容归属仍null。
- E30：2026-10-06 [新暂停分析成本和组成](EXP1_METRICS_AUDIT.md#2026-10-06新的暂停分析成本与内存组成)：全程29.287秒、完整分析10.138秒、暂停窗口6.112秒、内核子阶段1.816秒，有包含关系；guest23/host227样本，同点组成和观测峰值复算PASS，非新方案配对成本。实际guest启用DAMON，未隔离因果。收尾raw541.48 MiB、空闲50.47 GiB，原VM/CPU/恢复参考镜像保留，本次资源无遗留，未暂存或提交。

- E31：2026-10-07 [内核位号修正与两后端脱敏摘要](evidence/kernel-cache-scope-correction-20261007.json)、[实际范围/失败](FILE_PHYSICAL_MEMORY.md#2026-10-07内核位号修正与两后端同范围复验)。准确机器码/DWARF验证PG_lru=4；旧E29两点范围FAIL。新AgentENV短校准及完整26步、TrEnv-X预校准及完整26步、扫描后官方4/4、原计数恢复、独立页复算和清理PASS。物理量223.969/37.031 MiB，host未知零；TrEnv-X最终449页从驻留RAM快照回退核对。类型0为259/14页，完整任务内容仍null；首轮TrEnv-X路径别名拒绝FAIL及归档保留。
- E32：2026-10-07 [新成本与组成](EXP1_METRICS_AUDIT.md#2026-10-07修正版内核分析的成本与组成)、[AgentENV曲线](evidence/kernel-cache-agentenv-20261007.svg)与[TrEnv-X曲线](evidence/kernel-cache-trenvx-20261007.svg)。新方案全程30.645/148.885秒，最终分析11.074/22.458秒，包含暂停窗口7.181/8.568秒；非新开关对照。27项回归、实际布局检查和收尾PASS，raw/失败归档3.60 GiB、最低观测空闲35.72 GiB、收尾46.80 GiB，原资源与参考镜像保留。新物理范围已补，新协议匹配影响尚未测。

| 目标 | 状态 | 证据位置 | 实际验证命令与结果 | 缺口 | 下一步 |
| --- | --- | --- | --- | --- | --- |
| 1. 完整重放单条 trajectory | 已验证通过 | E1；Exp1 `actions.tsv`、26 个冻结 shell、raw `steps.tsv/stdout/stderr/patch.diff` | C3/C4：26 步顺序、哈希、008/016 退出 127、关键输出与 patch 通过；三个已修复运行的官方四断言通过 | 当前成功主任务为 Prettier #14400；非通用任意轨迹导入 | 保留此基线，为下一个任务完成同样检查 |
| 2. 指定工具边界 checkpoint、restore、续跑 | 已验证通过 | E1；AgentENV `process-continuity.json/events.json`；TrEnv-X `checkpoint.json/source-*.json/prefix` | C3：AgentENV 013 后从 014 续跑，进程身份连续；TrEnv-X 022 后从 023 续跑，源码及前缀保持，最终功能通过。E2 另有 TrEnv-X 013 成功 | AgentENV 当前入口硬编码 013；未验收任意边界、服务重启/异机恢复。TrEnv-X 仅文件继承 | 参数化边界，再验证一个新边界和状态隔离 |
| 3a. 内存变化、观测峰值、组成与占比 | 已验证通过 | E1/E2/E23/E26/E27；原始 TSV 与脱敏曲线 | 两批20次周期采样及同点组成复算通过，分析另有两个 guest 边界，host 连续采样；恢复曲线证据保留 | 观测峰值不是瞬时峰值；短动作可能无样本；余量保留，guest/host/分析程序分别报告 | 保持范围说明，不伪造 runner 退出或 guest 停止期间的样本 |
| 3b. page cache对应物理量及去重 | 部分完成 | E4/E9/E10/E22/E24/E26–E32 | 内部位号修正后两后端全SystemRAM的file-LRU物理核对及独立复算PASS，223.969/37.031 MiB，host未知零；TrEnv-X449页驻留快照回退包含在合计中；旧E29完整范围FAIL | E33已确认类型0为ext4分配器元数据；AgentENV新完整运行区间校准FAIL，扫描后任务检查未运行；非LRU、全部任务内容/provider归属未完整，DAX不在普通file-LRU | 先补缓存回收后的文件/任务检查，明确冻结点与区间判据；保留范围限制，固定协议后再测新方案影响 |
| 3c. 工具、保存、启动及恢复 breakdown | 已验证通过 | E1/E2/E23/E25/E26/E27；事件、步骤与归档计时 | 已记录的工具、runner、启动、保存、恢复、分析、任务验证和清理分别报告；新批分析均值6.05/11.95 s；此前保存11.756 s、恢复至工具可用2.289 s | 已验证范围为本机Exp1；AgentENV 模板单独计时、远端持久化仍未测，列空值；不同协议不能相减 | 不重做有效恢复证据，新增阶段另建协议和原始事件 |
| 3d. 采集不影响任务运行并量化影响 | 已验证通过 | E23/E25/E26/E27/E31/E32及历史对照 | 两后端新20次正常执行、源码/patch、扫描后官方断言和清理通过；保持周期采样相同，只开关精细分析，成本独立量化；此前恢复状态/进度证据保留 | 只证明任务结果保持正确及该配置成本已测；不代表零成本、缓存不被扰动、适用所有任务或达到自定5% | 新方案两后端功能PASS、单次阶段成本已记，其匹配开关尚未测；先固定范围后做五对，旧统计不外推 |
| 4. 部署、模板/checkpoint 管理、模板启动子沙箱 | 部分完成 | 官方示例 raw、E1；`10/13/16/40–47` 脚本与 SDK/CLI | C2 查询通过；已有创建、模板和恢复运行证据；双实例校准有同 snapshot 启动 A/B 的记录 | 依赖既有服务、凭据、固定 binaries/本地 seed；全新机器部署未验收；47 组合入口整体未运行 | 执行一次组合入口；另在独立环境验收部署 |
| 5a. 已有 trajectory 接入 | 部分完成 | Exp1 固定任务 wrapper；E5/E6 输入清单 | C3 完整实际重放；C6 三个 Exp3 清单校验通过 | 接入固定目录和格式，不是通用导入工具；不同会话语义不能自动转换 | 定义最小输入规范并接入第二任务 |
| 5b. 在线 trajectory 收集 | 已实现但未验证 | E5 `sample_task.py`、`sample_rollouts.py`、`agent-sampling.json` | 本次静态阅读：host 请求 DeepSeek、Docker 执行并冻结消息/命令/输出；历史采样记录存在；本次未调用模型 | 这些是已有 Docker 采样代码，尚无本机 AgentENV 在线采集运行证据；统一平台的 AgentENV 在线桥接未找到实现 | 单独接入 AgentENV 工具桥接，保存新轨迹，再冻结重放 |
| 5c. 冻结后重放 | 已验证通过 | E1/E2 | C3/C4 实际原始记录审计通过 | 只覆盖当前主任务和已验证输入 | 不重新请求模型，保留失败探索，逐任务扩展 |
| 6. 不同 rollout 方案 | 部分完成 | E5 controller `do_grpo/do_bpo/do_tvcache`；历史报告；E1 | 单路径和 checkpoint 后单路径有本机成功证据；C6 七路径图校验通过。GRPO/BPO/TVCache 未找到本项目本机 raw | 多路径运行只有历史派生报告，不能复算；无 RL 训练、熵选点或完整在线缓存 | 一个任务一个方案先 smoke，成功后再扩展 |
| 7. PPT 三任务的总内存、物理 cache 和阶段时延 | 部分完成 | E5 三个任务的 task/rollouts/segments 与历史派生表 | C6 输入通过；所有历史 `latest-run.txt` 所指 raw 均不存在；未找到本项目三任务运行目录 | 三任务本机测量受阻/无法判断；Exp1 #14400 不替代 PPT #6604；历史物理指标含 DAX PSS | 先补一个 PPT 任务的原始数据闭环，再做其余两个 |
| 8. cache 控制与配置对时延的影响 | 部分完成 | E6；本机 DAMON、guest 冷文件/目录预读、DAX 冷/热诊断 | C8 核对真实缓存计数变化；C7 冷/热物理量复算；历史 drop 组 raw 缺失 | 没有独立 page-cache 容量限制；冷热、预读成本、slab、运行时及 CPU 是混杂因素 | 明确对象与层级，固定其余变量，用同轨迹、同方案作配对比较 |

## 3. 重放与恢复的实际判据

最近 AgentENV 完整运行为 `20261001T063906Z-2885739`，013 保存/恢复为 `20261001T064059Z-2886802`。后者删除原实例后恢复，guest 后台进程 PID `705`、start ticks `81` 和 boot ID 相同，计数从 `126` 增至 `132`；随后确实执行 014–026，完整步骤表无重复或缺失，最终 patch、独立 SVG/nestedSVG/HTML 功能及官方任务测试通过。这比“保存 API 返回成功”多验证了进程状态和实际执行进度。

最近 TrEnv-X 完整运行为 `20261001T064940Z-2889203`，022 文件恢复为 `20261001T065156Z-2889585`。`checkpoint.json` 明确 `after_step=022`、`next_step=023`、新父子实例 ID 和 `file-state-only`。源码哈希恢复前后均为 `51d07fd7752ca36d8f059b7897ee6c8a77e9946c1ce22f129ad8f7dc6a1c1ea5`，已修改源码 diff 相同；控制器还检查前缀步骤表和 baseline tree 不变，再执行 023–026。完整 patch 与上述 AgentENV 一致，任务测试通过。**这证明文件状态恢复，不证明任意父进程恢复。**

三个已修复版本的官方任务评测均为 1 个测试文件、4 个断言、4 个预期输出快照通过；独立未修复对照四断言失败，说明测试能区分修复前后。本次 AgentENV 完整运行没有单独执行官方测试，不能把其任务测试字段改成 PASS；整个 Prettier 测试集未运行，官方 P2P 清单为空。

当前成功实例和 AgentENV 测试 snapshot 已按所有权清理，不存在一个可以马上重启的保留实例。TrEnv-X 恢复镜像有校验归档，使用前需要解包和检查；未重新解包做跨服务恢复。

## 4. 已有实验结果与指标范围

### 4.1 本机 Exp1 最新阶段时延

任务均为 Prettier #14400 SVG script 格式化；每 VM 2 vCPU、4096 MiB；完整 26 步或指定边界后单路径续跑。保留缓存，不做 host/guest 清缓存；“新 VM”不自动代表 host 冷缓存。AgentENV 使用临时固定频率、指定 CPU 和关闭 DAMON 的配置，TrEnv-X 保持原 CPU 策略，两组不是公平性能对照。

以下单位均为 **秒**，由 ns 除以 10⁹。本机 E1 原始数据经 C3 复算；“—”表示未测或不适用，不是零。

| 后端/模式 | guest 26 动作之和 | 控制器 runner 调用 | 创建至首工具成功 | checkpoint | restore 接口 | restore 至首工具成功 | 模板构建 | 删除父实例 | 本次总时间 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| AgentENV 完整 | 2.016 | 3.948 | — | — | — | — | — | — | 17.883 |
| AgentENV 013 恢复 | 2.299 | 4.170 | 4.757 | 0.788 | 0.088 | 0.149 | — | 0.118 | 38.378 |
| TrEnv-X 完整 | 1.893 | 23.555 | 1.044 | — | — | — | 12.381 | — | 104.724 |
| TrEnv-X 022 恢复 | 1.704 | 24.591 | 1.049 | 11.617 | 1.143 | 2.342 | 12.431 | 16.593 | 376.438 |

guest 动作来自 `replay/steps.tsv`；控制器调用包含原始 runner 准备及通信，不能把两者差值称为纯 RPC 开销。guest 原始动作使用其运行脚本的时间源，诊断单调时钟另记；不把 guest 与 host 时间戳相减。

创建/恢复时间来自成功工具边界；TrEnv-X 初次工具是已有 mkdir，AgentENV 是 true，定义不完全相同。AgentENV checkpoint 是 CLI snapshot 调用；TrEnv-X checkpoint 包括捕获、封存和模板构建，不能直接作同功能速度比较。总时间为控制器墙钟记录，包括本次检查和清理；TrEnv-X 恢复还含镜像归档，不是纯任务运行时间。远端持久化完成时间未测。

### 4.2 本机内存变化、峰值及组成

**最新 host 曲线：** E1 四次运行的观测峰值如下，单位 MiB（1 MiB=2²⁰ bytes）。组成取总量最高的同一采样点；不是分别取每项峰值后相加。

| 后端/模式 | 样本数 | memory.current 观测峰值 MiB | 同点 file MiB / 占比 | 同点 anon MiB / 占比 | 同点 kernel MiB / 占比 |
| --- | ---: | ---: | ---: | ---: | ---: |
| AgentENV 完整 | 4 | 8631.09 | 7719.32 / 89.44% | 628.81 / 7.29% | 273.23 / 3.17% |
| AgentENV 013 恢复 | 33 | 9581.54 | 8799.60 / 91.84% | 481.95 / 5.03% | 288.79 / 3.01% |
| TrEnv-X 完整 | 66 | 339.64 | 7.48 / 2.20% | 319.13 / 93.96% | 5.59 / 1.65% |
| TrEnv-X 022 恢复 | 103 | 317.75 | 3.34 / 1.05% | 305.03 / 96.00% | 6.04 / 1.90% |

AgentENV 读的是服务 cgroup，包含池和共享服务；TrEnv-X 读的是本次沙箱父 cgroup，不含 orchestrator。**不能据此说 TrEnv-X 节省了两列的差值。** cgroup 统计有归属及读取时序差异，分项比例不保证严格合计 100%。file 也不是单个 guest 的 page cache。曲线见[host 图](evidence/v1-host-memory-20261001.svg)；图存在及 C3/C8 复算支持其数据来源，但短任务的少量样本可能漏峰。

**AgentENV guest 历史曲线：** 两次本机 raw 仍存在，C4/C8 重算通过。都是 #14400、2 CPU/4096 MiB、缓存保留、周期采样的诊断运行，监控影响未验收。

| 运行 | guest 样本数/曲线时间跨度 | 观测最大非空闲内存 MiB | 同点缓存与 buffer MiB / 占比 | 同点匿名 MiB / 占比 | 同点 slab MiB / 占比 |
| --- | --- | ---: | ---: | ---: | ---: |
| `20260924T035910Z-1677178` 完整 | 95 / 2.570 s | 394.77 | 228.95 / 58.00% | 97.86 / 24.79% | 41.67 / 10.56% |
| `20260924T072030Z-1690521` 013 恢复 | 101 / 4.936 s | 396.77 | 233.69 / 58.90% | 95.77 / 24.14% | 41.25 / 10.40% |

非空闲定义为 `MemTotal-MemFree`；缓存与 buffer 为 `Cached+Buffers-Shmem`；占比分母为同点非空闲值。另列 shmem、kernel stack、page tables、percpu 及无法细分的余量，原值在本次 `review.json/compositions`；两次余量分别为 18.42/19.40 MiB。`SReclaimable` 已包含在 slab，不再次相加。

已有审计中的“used estimate”使用另一定义 `MemTotal-MemAvailable`，两次观测峰值为 351.81/348.88 MiB；它是压力估计，不能替代上述非空闲内存或物理文件页。这两类数值本身不矛盾，必须保留字段定义。

**TrEnv-X guest 与 PSS：** `20260929T075239Z-2407857` 有四个 guest 边界，`MemTotal-MemAvailable` 最大 317.27 MiB，Cached 最大 4.45 MiB、AnonPages 最大 54.98 MiB、Slab 最大 59.99 MiB。分项峰值不同点，不能相加；仅四个边界不能称为周期曲线。其解析结果没有完整的 MemFree/Buffers/Shmem 等组成，无法用同一方式闭合组成比例。

另一次 `20260928T081219Z-2242073` 的已核对 VMM PSS：重放前 388.77 MiB、保存前 404.44 MiB、恢复后 175.10 MiB、续跑后 335.52 MiB。PSS 是按共享映射比例分摊的进程内存，不是跨 VM 文件 PFN 去重，也不含全部辅助服务及 VMM 外缓存；这些四个点不是精确峰值。最新收尾没有采 PSS。

### 4.3 物理文件页与重复计数

物理页以 host PFN 合并去重，每页 4096 bytes。同一个 PFN 在声明的多个 VM/文件中只算一次；文件页、经内容校验的匿名副本和未知分别报告。VMM PSS、cgroup、guest 内存与文件物理量相互重叠，不能相加。

| 本机负载/阶段 | 文件页 | 内容一致的匿名副本 | 未知页 | 目标映射物理量 |
| --- | ---: | ---: | ---: | ---: |
| 真实 #14400 022 恢复后、目标未读 | 0 | 0 | 0 | 0 KiB，四页映射未驻留 |
| 同次完整续跑结束 | 3 | 1 | 0 | 16 KiB，其中文件页 12 KiB |
| 真实封存文件、双 VM、冷缓存先读 | 4 | 1 | 0 | 20 KiB |
| 相同文件、双 VM、热缓存 | 4 | 0 | 0 | 16 KiB |

C7 本次复算全部 PASS。范围仅为一个约 16 KiB 源码文件的声明 VMM 映射，不覆盖 upper、全部任务文件或未映射 host 缓存。首行零不意味着 host 文件缓存为空。真实任务扫描分别约 19.47/20.29 ms，仅是扫描部分，不能称为完整监控成本；内容核验读取 backing 可能改变缓存。

其他已知负载校准有原始目录：AgentENV 16 MiB 双实例写入前 4075/4096 个同位置页共享，PFN 并集 4117 页；一侧改写 2048 页后，并集 6145 页，另一侧内容不变。DAX 16 MiB 双实例 lower 映射曾去重为 4096 页。它们证明各自已知负载存在共享和隔离，不是完整任务收益；本次未重跑，仍按[双实例历史记录](SNAPSHOT_SIBLING_CALIBRATION.md)和[文件校准](FILE_PHYSICAL_MEMORY.md)限定范围。

### 4.4 PPT 三任务与 rollout 覆盖

PPT 对应 Prettier **#6604**、Sphinx **#7590**、Xarray **#6992**。本机成功的 #14400 是另一个任务。

三个 Exp3 workload 均有固定 `task.json`、`rollouts.json`、模型消息与工具输出、segment 动作哈希；C6 均通过。每任务七条逻辑路径、35 个逻辑 segment。GRPO 独立推进七路径，执行 35 个物理 segment；BPO 主干优先保存后执行分支，17 个；TVCache 按前缀树确定顺序，Prettier 13 个、Xarray/Sphinx 17 个。它们不是完整 RL 训练或线上缓存实现。

以下数值**仅为学长历史派生报告记载，未取得 raw，不能视为本机验收通过或复算结果**。历史 AgentENV 主组关闭 free-page reporting；每 VM 2 CPU/4096 MiB。缓存与层配置以历史 run 为准，当前补丁后的 controller 记录不进行 guest 全局清缓存，不能用现代码推断旧 run 实际策略。

| 任务 | rollout | 历史“文件物理峰值” AgentENV / TrEnv-X MiB | 历史 AgentENV VMM PSS 峰值 MiB | 历史工具时间 AgentENV / TrEnv-X s |
| --- | --- | --- | ---: | --- |
| Prettier #6604 TypeScript 括号修复 | BPO | 579.5 / 411.6 | 3114.7 | 43.99 / 44.35 |
| 同上 | GRPO | 1597.7 / 432.1 | 7652.2 | 71.91 / 60.99 |
| 同上 | TVCache | 800.9 / 384.7 | 3208.7 | 37.98 / 40.80 |
| Sphinx #7590 C++ 用户定义字面量 | BPO | 348.8 / 198.1 | 2451.6 | 30.28 / 27.14 |
| 同上 | GRPO | 1035.3 / 452.7 | 6471.9 | 65.53 / 43.93 |
| 同上 | TVCache | 364.6 / 198.3 | 2678.2 | 31.84 / 28.79 |
| Xarray #6992 Dataset 坐标集合不一致 | BPO | 1126.6 / 442.6 | 3879.7 | 130.48 / 115.72 |
| 同上 | GRPO | 2554.4 / 731.5 | 8528.4 | 280.33 / 224.57 |
| 同上 | TVCache | 1144.9 / 440.8 | 3814.8 | 124.79 / 116.27 |

历史工具时间是汇总工具执行量，仅供审计，不是全部并发路径的墙钟完成时间。历史“文件物理量”把 guest file-LRU 的 host PFN 并集与整个 DAX 镜像映射 PSS 相加；DAX 项可能包含文件系统元数据，比例分摊也不等于纯文件内容逐页去重。因此不能把这张表当作统一物理 page cache 的公平比较，也不能将文件峰值/PSS 峰值的比值当作同点内存组成。三任务总 host 内存及完整阶段 breakdown 本机未验证。

其他任务覆盖：#14400 有本机完整闭环；#6604 Exp2 有四轮、每轮八个整理后的固定命令与历史结果，不能称为原始 OpenHands 轨迹逐字重放。官方 Ubuntu Python 示例、Alpine 暂停恢复、合成文件/DAX/写入隔离是功能或校准负载，不计作 PPT 三个修复任务完成。

在线采样历史产物存在，但采样工具运行在 Docker。Prettier 采样元数据镜像为本地标签 `mixfs/prettier-6604:exp2`，当前 replay task 则指定 digest；重新采样前需核对标签构建来源。未找到在本机 AgentENV 在线调用模型并冻结新轨迹的成功证据。

### 4.5 监控影响

本次 C5 重新核对了以下数据；差值是开启采样相对关闭采样的几何平均，区间是五组对数比值均值的 95% t 区间。

| 配置 | 正式重放数 | 采样差值 | 95% 区间 | 关闭/关闭采样对照区间 | 结论范围 |
| --- | ---: | ---: | --- | --- | --- |
| AgentENV 关闭 DAMON，host 1 Hz，guest 周期采样关 | 40 | −0.94% | −2.63% 至 +0.78% | −2.48% 至 +0.96% | 此任务、此配置的工具窗口有量化证据 |
| AgentENV 默认 DAMON，其余对应配置 | 40 | −2.12% | −7.01% 至 +3.02% | −6.33% 至 +1.40% | 对照波动较大，受阻/无法判断稳定开销 |
| TrEnv-X host 1 Hz，guest 周期采样关 | 40 | +0.63% | −0.69% 至 +1.97% | −2.25% 至 +3.72% | 此任务、原 CPU/缓存条件的工具调用有量化证据 |

开启采样组的动作、输出、patch、独立功能和清理与预期一致，支持未改变这条任务的执行结果。负差值不是监控加速的证明。五组区间依赖近似独立与对数比值分布假设；共享 CPU/主机负载没有独占保证。

已有报告曾使用项目自行约定的 5% 规则；**PPT 没有规定性能门槛，本次不新增门槛，也不把该历史规则当成 PPT 强制要求。** 范围不包含采样启停、实例创建/删除、guest 周期采样、恢复、精细扫描或新增进程探针/阶段记录的完整影响。

## 5. page cache 控制核对

| 方法 | 对象和层级 | 实际占用/时延证据 | 限制和混杂因素 |
| --- | --- | --- | --- |
| 保留缓存，`DROP_GUEST_CACHES=0` | guest 不主动清缓存；host 也不清 | 当前 #14400 完整重放及恢复配置和日志匹配 | 新实例不等于 host 冷缓存；DAMON、后台回收和模板缓存可变化 |
| `sync; echo 3 > drop_caches` | 在 guest 内清文件 cache 及可回收 slab | Exp2 历史 preserve/drop 表显示轮 2–4 cache 明显不同；raw 缺失 | 不控制 host；同时改变 slab，不是精确 cache 容量；TrEnv-X 还重建文件系统 |
| 第 025 步前 guest `drop_caches=1`，无 sync | 新建自有 guest 的可回收干净文件页 | 本机冷目录对照：扫描均值 334.16 ms；加目录预读后 30.01 ms | 脏页/slab 未全清，不能称为全部缓存冷；预读改变访问历史 |
| 目录预读 | guest 工具读目录，间接影响 guest/host 缓存 | 上述含预读的整步均值从 413.35 ms 增至 1041.48 ms；六批诊断哈希本次核对 | 扫描更快但整步更慢；预读遍历 5653 个目录，Git 只访问 2743 个，另有 Python 启动成本 |
| 关闭新 guest 的 DAMON | guest 内存回收策略 | 本机三批共 12 次：默认 Cached 读前约 117.27–202.95 MiB，关闭约 202.77–203.54 MiB；refault 由部分非零变为全零 | 不是 cache 容量设置；同一模板继承状态、准备过程和主机负载仍影响结果 |
| 对自有层 `fsync+FADV_DONTNEED` 并检查 mincore | host 指定 backing 文件 | 真实四页文件读前冷/热证据及 C7：双 VM 为 20/16 KiB | 只作用指定文件；挂载可能重热，必须在实际读取前检查；不覆盖整个镜像 |
| `memory.high/max` | cgroup 总内存预算 | 未找到本项目以其控制专属 page cache 容量的有效实验结果 | 即使配置也限制总预算，不是 page-cache 独立上限 |
| OverlayBD Direct I/O | host lower 文件 I/O 路径 | 当前 #14400 metadata 记录 `ioEngine=0`、direct_io=false；有切换代码 | 本次未找到固定其他变量、可复算的本机 Direct I/O 配对结论；切换共享服务不在本次范围 |

DAMON 对照的完整 runner 窗口均值：默认 3.850864 s，关闭 3.865816 s，差约 +0.388%；目录扫描均值却为 53.38/30.15 ms。这证明局部阶段变化不代表端到端一定改善，且没有证明所有历史慢调用由 cache 导致。原始 guest Cached、vmstat refault 已重新读取一致；它们仍不是 per-file 的 host 物理 I/O。

Exp2 历史同镜像、同 manifest、2 CPU/4096 MiB、四轮/32 动作：AgentENV preserve 工具总和 12.130265 s，guest drop 为 22.108421 s，TrEnv-X drop+重建为 7.920881 s。第 4 轮前 guest `Cached+Buffers-Shmem` 分别 239.4/45.7/4.0 MiB。raw 缺失，只列历史观察；+82.3% 不能归为纯 page cache 效果，因为 drop=3 同时影响 slab；跨后端还存在内核、VMM、DAX、状态重建和空闲页回收差异。

当前 Exp3 controller 的 TrEnv-X 捕获已改为任务组冻结而不执行 guest 全局清缓存；旧历史 run 是否清缓存必须看旧 raw。仅阅读最新 controller 不能替历史报告补足生效配置。

## 6. 未验证、失败及待确认条件

1. **三任务本机结果受阻/无法判断：** senior 结果目录中没有任何 raw，全部 `latest-run.txt` 目标缺失；本项目 `.artifacts/` 未找到三任务运行目录。历史报告可以指导复现，不能完成本次原始样本验收。
2. **第三项尚未整体通过：** E26/E27 已补两后端同次运行的曲线、同范围物理观测和分析成本，任务结果均正确，3a/3c/3d 已验证通过。3b 仍部分完成：物理观测是变化区间，共享层、元数据、全部任务内容和 provider 匿名内存未完整归属，不能报告成精确全任务总量。两后端 host 值范围不同，不能作公平总内存比较。
3. **平台能力缺口：** 通用 trajectory 导入、AgentENV 在线采集桥接、任意恢复边界、全新机器部署尚缺。多调度有代码和历史记录，缺本机可复算运行。无需以 Kubernetes、Prometheus 或 ARIES 为前置条件。
4. **恢复边界：** 当前保存只验证本机接口和特定状态；没有跨共享服务重启、异机持久化、任意 FD/应用状态测试。TrEnv-X Exp1 文件继承不支持完整进程恢复，Exp2 的原生 VM snapshot 变体需单独判定。
5. **历史失败保留：** `20260928T144933Z-2330037` 首个匿名页未归因，整体 FAIL；后续修复采集方法并有新 PASS，不能改写旧结果。收尾两次集成尝试因共享代码目录补丁冲突、guest 工作目录不存在失败，也不计成功。旧 CPU14 ublk EINVAL 已是迁移历史，不是当前持续阻塞证据。
6. **资源与版本条件：** 服务器有其他 VM，不能保证 CPU 独占；约 51.6 GiB 只够部分阶段。新多实例预算要按实际占用核验。当前工作区未提交实现应先冻结对应快照，防止同伴仅 checkout HEAD 得到不同能力。
7. **统计与验收条件：** PPT 无性能阈值。需要确定希望先验收 guest 曲线还是完整实例/共享服务的 host 范围，以及物理指标先覆盖命名文件子集还是要求整个任务；范围未约定时应同时报告已测部分与缺失，不能自行缩小要求后宣布全部通过。

## 7. 下一步优先级

**第一优先：补齐第三项的3b。** E28已经在两后端通过原VM暂停的稳定集合与恢复后功能验证，两个指定证据缺口和暂停接口不再列为未测，不追加相同长测。下一步先离线核对完整文件目录、物理页到内容/元数据、共享层及provider匿名内存的归属方法，确认覆盖范围后再做一次已知文件校准；无法判定的保留空值。物理观察 PARTIAL 不计作精确全任务总量，不自行规定性能门槛；详细步骤见实验计划。

**第二优先：完善平台使用闭环。** 将 AgentENV 恢复边界参数化，完成组合入口整体运行，形成最小任务/trajectory 输入规范及可复现配置；在线采集作为独立能力接入。部署验证使用独立环境，不重启共享服务或扰动现有 VM。

**第三优先：推进 PPT 实验。** 先 Prettier #6604 的一条冻结路径，再一种多路径方案，获得正确性、内存、物理 cache、时延和清理全部 raw；通过后扩到 Sphinx、Xarray 及其他 rollout。缓存比较固定任务、轨迹、调度、资源、I/O、回收机制和实际冷热状态，保留失败与所有配置。

正式多实例批次前先核验归档留存和峰值空间；遇到不足先提出本项目资源清理清单或新存储方案，不清理他人资源、不丢弃未确认的恢复证据。详细后续计划见[实验计划](../EXPERIMENT_PLAN.md)，其内容仍是计划，不是完成证据。

</details>
