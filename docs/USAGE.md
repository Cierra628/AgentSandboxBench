# 本机平台使用说明

**内存采集先选入口：**[简明用法与指标范围](records/HOST_METRICS_GUIDE.md)区分日常host轻量采样和Prettier七角色终点分层查询，列出命令、实际统计组、成本及覆盖限制。

**2026-11-05同步入口：** 同伴拉取代码后的离线检查、已支持任务、资源准备和实跑命令统一见[同伴使用说明](COLLABORATOR_GUIDE.md)。其中明确区分无PSS的轻量路径与开启PSS的三调度路径；本页此前记录按原版本和输入条件理解。

当前普通任务记录工具时延、guest内存和page cache曲线，以AgentENV为后端；TrEnv-X入口保留供后期验证。三个普通单任务配置默认每0.1秒读一次meminfo，不启用CPU/I/O、host物理扫描或VMM采集。指标定义、命令及验证范围见[轻量指标说明](records/LIGHT_TASK_METRICS.md)；当前版本的直接成本、开关影响及冷启动解释统一见[采样开销说明](records/LIGHT_MONITOR_COST_SUMMARY.md)，[原三任务对照](records/LIGHT_MONITOR_CONTROLS.md)按旧版本保留。配置`"monitor_mode": "off"`关闭全部周期和工具边界采样，命令计时及任务检查保留；`"light_monitor": false`表示旧完整采样，不是关闭采样。

此前精细测量仍可使用明确的诊断配置，含义、各版本覆盖、成本及未知量见[阶段验收说明](records/CACHE_MEASUREMENT_VALIDATION.md)。三任务完整单路径、Prettier第24步恢复及真实双分支已有证据；provider缓冲和2页策略已通过私有短恢复及[一次真实双分支](records/PROVIDER_REAL_PAIR.md)。下文`*-cache.json`、`*-shared-*.json`的物理分析仍是旧暂停方案，不能与不暂停方法混用。

如需解释Cached升降，`platform-prettier6604-damon-observe.json`可显式增加默认DAMON状态和计数的只读采集，不改变回收策略，普通轻量模式不增加这些读取。[一轮实际观测](records/PRETTIER_DAMON_OBSERVATION.md)已确认默认回收与四段明显下降在时间上对应；实际内核不提供`skip_anon`，回收累计量按内存而非全部文件缓存报告。

Prettier另有`platform-prettier6604-damon-disabled.json`，在任务准备/采样前关闭该guest的DAMON、等待回收线程退出，采集结束后恢复原启用状态。[一对实际对照](records/PRETTIER_DAMON_PAIR.md)已确认关闭窗口回收增量为0、原结果保持，显著的大幅Cached下降消失。此配置保留0.1秒内存及DAMON计数采集，不改变普通任务默认值，也不禁止其他正常回收。用法仍是统一`check/run/report`入口。

## host总内存与文件缓存轻量采样

只需要guest轻量曲线和host总记账时，普通三任务现有显式`platform-<任务>-host-current.json`配置（#6604、Sphinx7590、Xarray6992）；关闭PSS和最终mincore，旧`*-host-file.json`不变。[选项与检查](records/HOST_CURRENT_TASK_OPTION.md)及[#6604一轮](records/HOST_CURRENT_PRETTIER6604.md)、[Sphinx／Xarray各一轮真实采集](records/HOST_CURRENT_PYTHON_TASKS.md)均已完成，三任务新模式的数值表和曲线见后两份报告。调用为`bash scripts/run-host-file-monitor.sh configs/platform-prettier6604-host-current.json`；8 GiB卷启动前可用磁盘须严格超过34 GiB，源服务须已健康。

同组并发短测使用`bash scripts/run-host-file-monitor.sh configs/platform-prettier6604-host-file.json --current-pair`：先1个再2个guest，各原11步、各观察5秒，current主曲线，无PSS／mincore；不改旧1／2／4／7入口。8 GiB卷同样要求启动前严格超过34 GiB可用。[首轮数据及补测整轮结果](records/HOST_CURRENT_PAIR.md)已有记录；补测的报告、末尾检查和清理全部通过，原失败卷经授权已删除，原记录保留。

同组短测约14.5 GiB准备基线包含临时RAM整卷和已被校验读取的缓存；不是每个guest的独占成本。[基线拆分及部署统计范围](records/HOST_CURRENT_BASELINE_SCOPE.md)说明已确认的7252 MiB RAM容器、其余缓存和未覆盖对象。保留原current与同组增量，不直接扣除全部准备基线后当作真实部署内存。

若要验证去掉RAM整卷的新准备方式，显式在上述`--current-pair`命令后加`--persistent-input`；仅新选项改为组外磁盘恢复、组内新普通文件导入，原入口不变。[离线实现和使用条件](records/HOST_PERSISTENT_INPUT_IMPLEMENTATION.md)已完成，真实新路径尚未运行。准备前要求严格>54 GiB可用磁盘；0.1秒采样不增加查询，只在两个任务窗口结束后新增一次去重输入文件mincore核对及成本记录。新旧current统计范围不同，不直接相减作为物理节省。

10月22日前的部分逐页明细已无损压缩保存，近期轻量数据和图保持原位置；旧精细分析若缺少原`.bin`或大型页所有者表，先按[清理报告](records/STORAGE_CLEANUP_20261029.md)恢复对应单个文件。三任务必需输入备份均保留。

新版完整采样开销的关闭对照使用`configs/platform-prettier6604-host-current-off.json`，仍通过同一`run-host-file-monitor.sh`入口创建统计组，内存／I/O记账、输入准备、原工具计时和全部任务／卷检查保持；关闭guest周期及边界采样、host周期采样与内存基线读取，不生成内存曲线。离线检查及[正反两对116动作实跑](records/HOST_CURRENT_SAMPLING_TWO_PAIRS.md)已完成；整轮差值随顺序反转，工具、回放及创建后窗口分列，保留全部实际值。用法见[关闭入口、计时和未测状态说明](records/HOST_CURRENT_SAMPLING_CONTROL.md)。这不同于旧`--matched-task --host-sampling off`仅关闭host新增采集，不要混算两类对照。

Sphinx另有`configs/platform-sphinx7590-host-current-off.json`，同样只加`monitor_mode: off`，仍使用外层`run-host-file-monitor.sh`。其[首对70动作实跑](records/HOST_CURRENT_SPHINX_SAMPLING_PAIR.md)已完成，原结果、开关、报告和清理通过；本对工具增加64.49%，主要集中在一条环境枚举命令，等待来源仍需诊断，不直接沿用Prettier的耗时结论。

Xarray对应`configs/platform-xarray6992-host-current-off.json`，同一外层入口使用方式不变；[关闭→开启各35步](records/HOST_CURRENT_XARRAY_SAMPLING_PAIR.md)已完成，回放+0.19%、控制程序整轮+0.55%，原945通过／12失败、诊断规则、开关和清理保持。当前三个标准任务均有新版guest＋host current开关结果，仍按各任务报告分别解释。

查看各任务已有host总记账，先看[四项代码任务的统一清单](records/HOST_CURRENT_TASK_INVENTORY.md)：包含current基线／峰值／增量、曲线及实际采集开关。旧#6604缺精确基线，旧三任务带PSS／mincore，新#14400关闭；这些记录按各自配置展示，不合并为同条件开销结论。本机原始数据仍在时，可只读生成数值表：`bash scripts/report-host-current-tasks.sh --output .artifacts/host-current-tasks-new`。

Prettier #14400另有已完成一轮完整验证的显式轻量入口，保留原26步、2 CPU／4 GiB及官方检查，guest／host每0.1秒采样，关闭PSS、mincore和旧重复监控，默认回收保持：[结果、图表及成本](records/HOST_PRETTIER14400_LIGHT.md)。它沿用旧Exp1的v1配置，不使用普通v2任务分派：

```bash
# 源服务须已健康；不自动启动或替换源服务。
export ASB_AGENTENV_SOURCE_SERVICE=asb-source-rebuilt-20261018T160925Z-2726362
bash scripts/run-host-file-monitor.sh \
  configs/platform-prettier14400-host-current.json --exp1-light
```

此模式使用20 GiB卷，启动前磁盘可用须严格超过46 GiB，并依赖本机已验证的只读缓存备份。旧入口不加新选项时行为保持；成功卷清理，失败卷保留并另行申请删除。

新增冻结工具任务可使用`run-host-file-monitor.sh ... --tool-replay configs/host-csv-light-replay.json`，只采guest meminfo和host current，不采PSS或mincore；[CSV实跑与限制](records/HOST_CSV_CURRENT_VALIDATION.md)给出完整命令、曲线、成本和原外层检查失败的离线修复。现有普通任务默认行为不变。

**要查看多沙箱共用文件少占多少host缓存，先看[GRPO／BPO统一报告](records/HOST_COPY_CONTROL_SUMMARY.md)。** 该显式副本模式以真实19文件共用／133独立文件的mincore驻留差为主指标，关闭PSS；host file曲线仅作记账上下文。报告集中列出图、原始结果、阶段、成本和两种调度的实跑命令，不改变下面普通host入口的默认行为。

只从保存JSON生成统一结果表、不启动沙箱或查询缓存：

```bash
bash scripts/report-host-copy-summary.sh --output .artifacts/host-copy-summary-new
```

输出目录必须不存在；生成`SUMMARY.md`和`summary.json`。已有对照各一对，均已完成，日常查看不需要重跑。

显式配置`platform-prettier6604-host-file.json`、`platform-sphinx7590-host-file.json`或`platform-xarray6992-host-file.json`增加host采样，普通guest配置保持不变。使用前先看[当前指标、验收范围和运行选项](records/HOST_MEASUREMENT_ACCEPTANCE.md)。新host报告以`memory.current`为主曲线，表示整组总内存记账；`anon/file/kernel`解释组成，清理前mincore保留指定文件的全局驻留。基线、观测峰值、增量、未覆盖范围和无需驻留清单的离线命令见[current主指标方案](records/HOST_CURRENT_PRIMARY.md)。已有PSS采集选项不变，共享文件模式继续关闭PSS；host file与guest Cached不是同一层，不把这些量相加。

```bash
# 源服务需已确认健康；此入口不负责启动或替换源服务。
export ASB_AGENTENV_SOURCE_SERVICE=asb-source-rebuilt-20261018T160925Z-2726362
bash scripts/run-host-file-monitor.sh configs/platform-prettier6604-host-file.json
```

入口在准备输入前创建统计组，同时启用内存和I/O记账，新服务及新VMM从创建时就在组内。普通完整任务使用8 GiB卷（Prettier 29步，Sphinx／Xarray各35步），启动空间须超过34 GiB，另按入口预留未就绪输入。恢复选项使用24 GiB卷，须超过50 GiB；保留原资源保护和失败卷清理授权规则。

下列选项互斥，已验证范围为Prettier：`--matched-task`为同一快照的单／双沙箱相同后缀，共41实际动作；`--trace-dual-task`为带专用探针的双沙箱31动作，只作来源和覆盖诊断；`--restore-pair`只观测空闲恢复，不执行后缀；`--short-pair`只做两个新guest的标记检查，没有共享快照。已有结果优先离线使用，不逐项重复校准。

实验选项`--independent-matched-task`已完成同一快照、两组独立服务／可写卷的实跑；`--resume-prefix <原源结果目录>`可复用已验证归档，只新增30个后缀动作，不重复原11步。结果、起始缓存差异及记账缺口见[独立卷验证](records/HOST_INDEPENDENT_PAIR_VALIDATION.md)，已有前缀的调用见[继续方式](records/HOST_INDEPENDENT_PAIR.md#授权清理和已验证的继续方式)。不自动删除失败卷或启动源服务；重新执行仍须源服务健康和超过50 GiB可用磁盘。此选项不改变原普通或共享卷入口。独立恢复现于导入后、服务及guest创建前同步自有文件系统，结果写入`checkpoint_import_sync`；本轮准备同步约8.5秒，两组基线差降至3.379 MiB，详见[同步验证](records/HOST_IMPORT_SYNC_VALIDATION.md)。这是准备条件变化，旧未同步结果按原范围保留。

I/O记账设置已经过[小loop对照](records/HOST_LOOP_ACCOUNTING.md)及[真实大卷恢复](records/HOST_FILE_IO_RESTORE.md)验证。[此前共享卷41动作对照](records/HOST_LIVE_FILE_INVENTORY.md)已补齐每组VMM删除前的普通文件清单，并验证目标设备mincore前后的PSS读取。单／双设备驻留422.707／425.227 MiB；普通文件、块设备和原始层按实际身份分别核对，原始层不重复加入。最终190文件仍有0.480469 MiB净不足，保持原检查状态；旧轮次差额不追溯改写。
[历史相同后缀对照](records/HOST_FILE_MATCHED_TASK.md)提供指定设备相对线性参考的减少比例，不是整体host节省。三个普通任务均已完成host入口实跑；[Sphinx／Xarray结果与图表](records/HOST_PYTHON_TASKS.md)包含各自35步及157文件最终清单，未检出净不足但不证明记账完整。使用上面同一命令，将配置换为对应的`*-host-file.json`即可；共享恢复和设备归属检查仍仅覆盖Prettier。新增host采集已完成[一次开启／关闭对照](records/HOST_SAMPLING_CONTROL.md)：工具累计5.645／5.716秒，控制程序46.023／32.397秒，额外查询成本分开报告。只在`--matched-task`后加`--host-sampling off`关闭新增host采样，guest采样和记账条件保留；此时没有host物理量或曲线。普通入口默认开启，三任务guest采样开销仍按原报告理解。

需要更完整的host明细时，在独立卷命令上增加`--host-memory-detail`：目标设备、普通文件、额外映射文件和PSS连续查询，其他大设备随后查询。该选项已完成一次30动作单／双验证；35个补查文件的全局驻留及特殊映射未知项见[结果与调用](records/HOST_MEMORY_COMPACT.md)。不加选项仍用原轻量方案，改进前成果已[备份](records/LIGHT_MEASUREMENT_BACKUP.md)。

[特殊映射离线分类](records/HOST_SPECIAL_MAPPINGS.md)说明Pss_File还可包含映射内核页，不能当作纯page cache或再加kernel；ublkc命令缓冲也可能被PSS及统计组记账漏掉，随后已用下述真实补采单列本轮分配量。默认入口和历史数值保持。

显式明细已补入逐映射信息、必要smaps、可访问已删除后备类型及ublk队列配置，完成[一次真实30后缀动作验证](records/HOST_SPECIAL_MAPPINGS.md#最新实跑回答了什么)：7次查询成功，命令缓冲按源码计算为12／16 KiB，原结果及清理保持。命令不变；仅此选项新增cc和liburing开发依赖，工作流程在启动服务前编译只查GET_DEV_INFO的程序。新增补采状态与原查询状态分列，已有映射PSS不当作整个后备容量。

同一显式选项现已[接入已删除后备缓存查询](records/HOST_DELETED_BACKING_INTEGRATION.md)：每批一个默认4秒超时子进程，经map_files和cachestat查询，结果在`deleted_backing_cache`及报告独立一节列出，不加到file／PSS／kernel。跨进程和超时回收的小对象验证以及[真实原快照后30动作补采](records/HOST_DELETED_BACKING_LIVE.md)均已完成；真实两组缓存4／8 KiB，查询含启动45.080／47.184毫秒。原缺描述符状态与新缓存成功状态分列，旧数据不追补。独立小对象入口为`bash scripts/run-host-deleted-backing.sh --bounded`，无需重复已完成的验证。

无需重新实验即可查看[最新内存分项及耗时](../.artifacts/host-file-independent-pair/20261021T110642Z-31195/MEMORY_DETAIL.md)，[此前汇总](../.artifacts/host-memory-report-20261021/latest/MEMORY_DETAIL.md)按原字段和未测范围保留。独立单／双沙箱流程自动生成`MEMORY_DETAIL.md`及扩展`summary.json`；离线另存使用`bash scripts/report-host-independent-pair.sh WORKFLOW --output NEW_DIRECTORY`，不调用服务或设备，只接受原数据目录外的新目录。新旧结果兼容，缺失字段不补零；完整调用示例见[离线报告说明](records/HOST_SPECIAL_MAPPINGS.md#直接查看或离线生成汇总报告)。

使用已有单／双沙箱结果说明共享效果时，优先引用[同步后离线核算](records/HOST_PHYSICAL_BREAKDOWN.md)：指定设备相对线性参考少驻留394.730 MiB，总记账增量参考少395.398 MiB，后者不写作整体物理节省。公共准备只算一次，普通文件、设备和匿名PSS保留各自时间及范围。

旧普通文件已有缓存的记账归属可用独立研究入口`bash scripts/query-host-existing-cache.sh FILE --group CGROUP --output NEW_DIRECTORY`查询。[一次自有文件及旧归档查询](records/HOST_EXISTING_CACHE_OWNER.md)已验证：当时归档1746.109375 MiB均在实验父组外，带探针查询414.661毫秒、含准备总5.267秒。现已[接入`--host-memory-detail`](records/HOST_EXISTING_CACHE_INTEGRATION.md)：原文件／设备观察之后，以本轮统计组为参考，默认30秒超时查询导入归档，结果在`existing_cache_owner`和报告独立一节列出。默认采样不变，旧记录为NOT_MEASURED／null；不读数据、不回填历史，也不把新增成本计入工具用时。40项离线检查、旧报告兼容和自有进程组超时通过；本轮没有接入后的沙箱实跑。

## 改变文件缓存：起始状态和持续目标

查看缓存变化对任务的影响，使用[缓存控制汇总](records/CACHE_CONTROL_SUMMARY.md)。Prettier已完成起始保留/清理四次和持续64/128 MiB目标对照，推荐同一实际快照、保存前停止后台回收的`--damon-before-checkpoint`选项；它是周期回收目标，不是硬上限。已有结果优先离线查阅，无需重复实跑；24 GiB卷的新执行仍要求启动前超过50 GiB可用磁盘及原资源保护规则。

Sphinx和Xarray已完成[不主动回收基线](records/PYTHON_CACHE_BASELINES.md)及[各两档目标组](records/PYTHON_CACHE_TARGETS.md)。基线配置`platform-sphinx7590-cache-baseline.json`、`platform-xarray6992-cache-baseline.json`通过`guest_cache_baseline: true`显式启用；目标配置使用互斥的正整数`guest_cache_target_mib`。Sphinx已验证65/98 MiB，Xarray为167/251 MiB，均在自有guest关闭DAMON、任务后恢复原状态。

两任务基线合并入口为`bash scripts/run-python-cache-baselines.sh`，四次目标组为`bash scripts/run-python-cache-targets.sh`，单项仍用统一入口和对应`platform-sphinx7590-cache-65.json`等配置。两类入口均使用已有8 GiB单路径预算和指定的reclaim诊断内核，旧配置及Prettier参数保持。已有结果优先离线报告，不重复实跑；这些目标仍是周期回收目标，不是硬上限。

## 不暂停物理缓存采集的统一入口

最新版真实双分支已有[关闭/开启各一次的耗时对照](records/PROVIDER_PAIR_TIMING.md)。独立入口的`--prettier6604-snapshot-cache-sampling off|on`只切换缓存发布、host物理采集及BPF，默认`on`；不关闭常规监控、guest内核成员表或provider观测记录。`off`仅用于真实双分支对照，物理量明确为未请求，不生成零占用曲线。统一任务配置的默认行为不变。

版本2配置可增加`live_cache_kernel`，填入本机已构建的诊断guest ELF路径，即启用新常驻采集器。当前示例使用`.artifacts/agentenv-cache-kernel-20261015-v3/vmlinux.bin`；缺内核或输入时检查会明确失败，不改用旧内核。该统一选项支持三任务完整单路径和Prettier第24步checkpoint/restore，与统一配置的分支及旧暂停测量互斥；其他任务的新恢复采集尚未接入。真实双分支使用独立入口，默认保留[三层版本](records/PRETTIER_REAL_PAIR_CACHE.md)，[最新四来源版本](records/PROVIDER_REAL_PAIR.md)须显式提供provider二进制、GET_DATA和批量参数。原配置行为保持。

```bash
bash scripts/use-platform.sh check configs/platform-sphinx7590-live-cache.json --offline
bash scripts/use-platform.sh run configs/platform-sphinx7590-live-cache.json
bash scripts/use-platform.sh report .artifacts/platform-tasks/20261015T065544Z-1881793 --output .artifacts/<新的统一报告目录>
```

| 新配置 | 运行和报告证据 |
| --- | --- |
| `platform-prettier6604-live-cache.json` | 29步控制器成功数据复用；配置分发通过，统一报告两次11文件一致，未重复新入口实跑 |
| `platform-sphinx7590-live-cache.json` | 统一入口真实35步及22个配对点通过；原诊断策略及24通过/1失败保持，12文件报告复算一致 |
| `platform-xarray6992-live-cache.json` | 统一入口真实35步及65个配对点通过；原SIGPIPE诊断及945通过/12失败保持，12文件报告复算一致 |
| `platform-prettier6604-checkpoint-live-cache.json` | 29步、第24步完整状态恢复及后5步通过；保存前11点、恢复后14点，两份修正报告13文件一致 |

`run`返回独立运行目录，自动在旁边生成`report-<run_id>/`；其中保留原任务、内存/CPU/I/O、时延和清理报告，新增`live-cache/summary.json`、`cache.svg`及`cache.png`。根`summary.json`的`live_cache`提供采样数、两个观测峰值、未确认容量最大值及相对曲线路径。Python任务原来的`page_cache.physical_bytes=NOT_MEASURED`会改为新序列的方法和来源，避免与实际已测数据矛盾。物理页号只留本机raw，未确认容量不是已测host RAM。

同样需要已核验的任务备份、root/BPF/pagemap权限、超过34 GiB磁盘空闲及64 GiB可用RAM；顺序使用8 GiB固定卷，成功后回收本次资源。新guest内核只复制到私有服务，不更改host内核或共享服务。Sphinx的`sphinx-pipeline-v2`和Xarray的SIGPIPE诊断规则明确保留，不能把诊断PASS改写成严格重放PASS。指标含义和最新数字见[实际结果](records/FILE_PHYSICAL_MEMORY.md#2026-10-15三任务完整单路径的不暂停缓存测量完成)。

恢复配置使用24 GiB固定卷，要求超过50 GiB磁盘空闲，不能沿用单路径的34 GiB预算。调用同上，只需将配置换为`platform-prettier6604-checkpoint-live-cache.json`。采集保存前停止、恢复后重新关联host进程和RAM布局，报告分段绘制；正常checkpoint自身的暂停仍存在。

文件恢复后端可能保留尚无VMM驻留页表项的缓存页。现行恢复路径已增加直接后端查询；`platform-prettier6604-checkpoint-layer-cache.json`再显式开启`live_cache_lower: true`，计入可归属的单个原始内存层。各来源按物理页号去重，旧版本及未知量另列。统一配置尚无provider及批量开关，不会自动启用最新增强；完整覆盖与版本见[阶段验收说明](records/CACHE_MEASUREMENT_VALIDATION.md#哪些范围已验证使用哪个版本)。

早期仅VMM恢复数据仍按原范围解释：未映射不代表零host占用，未确认guest容量不能与物理量相加。该次首次自动报告已被离线修正，应使用`.artifacts/cache-restore-20261015/report-a`，详见[早期恢复说明](records/FILE_PHYSICAL_MEMORY.md#2026-10-15第24步恢复后的不暂停采样通过文件后端驻留需单列)。新运行取得的后端数据不用于追溯补算旧运行。

以下独立入口及指定文件校准保留供诊断；正常完整任务优先使用上方统一配置入口，已有成功结果优先离线复算。

指定文件的guest到host关联已有独立入口，使用原guest内核、不暂停。新建一个2 CPU/4096 MiB VM，在1 MiB校准文件上自动采集约11秒；临时锁住已填充文件页，释放前解锁，不代表正常任务开销或整guest覆盖。要求原项目API空闲、固定镜像已在服务中完整缓存、root/BPF权限及超过32 GiB可用空间；只删除本次VM。日常查看结果使用离线报告，不重复真实校准。

```bash
python3 -B scripts/probe_agentenv_live_cache.py --output .artifacts/<新的测量目录>
python3 -B scripts/report_agentenv_live_cache.py .artifacts/agentenv-live-cache-20261015-consistent --output .artifacts/<新的报告目录>
```

报告上图对比同一文件的guest逻辑缓存与host驻留物理量，下图单独显示整guest Cached；两者范围不同。原始PFN留本机，报告输出目录必须是新的项目`.artifacts`子目录。已验证结果和全量采集的下一步见[当前测量记录](records/FILE_PHYSICAL_MEMORY.md#2026-10-15agentenv运行期间的guest缓存与host物理占用关联通过)。

整guest诊断路径使用单独构建的Linux 6.1.175 guest内核，原host内核及共享服务不变。构建需要root、已有编译工具、8 GiB有界tmpfs及4个编译作业，不安装依赖。可复用已有源码资产；以下构建输出和报告输出都必须是新目录：

```bash
python3 -B scripts/build_agentenv_cache_kernel.py --output .artifacts/<新的构建目录> --assets .artifacts/agentenv-cache-kernel-20261015
python3 -B scripts/validate_isolated_agentenv.py --automatic-window --guest-cache-metadata-probe --diagnostic-guest-kernel .artifacts/agentenv-cache-kernel-20261015-v3/vmlinux.bin
python3 -B scripts/report_guest_cache_metadata.py .artifacts/storage-isolated-service/20261015T034459Z-1821447/guest-cache --output .artifacts/<新的报告目录>
```

第一条只构建；第二条示例明确使用已通过的v3内核，若验证新构建需改成其实际`vmlinux.bin`路径。短验证需完整本机原生镜像缓存和超过38 GiB主机可用空间，预分配12 GiB临时卷，顺序读取6组全guest元数据和host映射。它验证普通文件/tmpfs的包含及匿名页排除，不执行完整trajectory。成功后回收本次服务、VM和卷；失败保留现场，完整备份核验后按已授权规则回收源卷。报告展示guest Cached、host确认驻留量，以及未确认guest页容量；第三项不是已测得的host RAM。原始物理页号只留本机，代码仓库只放脱敏结果。已有结果直接离线复算，不重复启动短验证。

完整Prettier #6604冻结任务现可使用常驻采集，不暂停VM；先准备只读RAM镜像缓存，再调用已有隔离控制器，保留原29步及任务检查：

```bash
python3 -B scripts/run_live_prettier_cache.py --output .artifacts/<新的任务目录> --kernel .artifacts/agentenv-cache-kernel-20261015-v3/vmlinux.bin
python3 -B scripts/report_live_task_cache.py .artifacts/storage-isolated-service/20261015T040303Z-1830135/prettier6604 --output .artifacts/<新的报告目录>
```

使用已核验的Prettier备份、已有v3内核及本机服务依赖；需超过34 GiB磁盘空闲和64 GiB可用RAM，任务占用8 GiB固定卷，只读镜像临时展开到有界RAM。采集周期约1秒，独立匿名缓冲区容量16.008 MiB并锁定；这不是任务文件page cache。原始配对数据预算1 GiB，成功后回收本次VM、服务、卷和RAM缓存。结果目录的`report/`含两条缓存曲线、未确认容量、任务检查及阶段时延；原有100 ms轻量内存/CPU/I/O报告另保存在对应storage目录`report/`。

对照同一个诊断内核下关闭新增采集，可在第一条命令加`--cache-sampling off`，仍保留原轻量监控及内核元数据表。该模式只运行任务与常规报告，不生成新的host物理缓存曲线。已完成优化版先关后开各一次的验证：完整重放用时变化+0.96%，只作单对观察，不当作统计稳定值；性能比较须说明顺序和重复次数。当前默认使用`array-accounting-batched-export-v2`采集器，内核仍是`6.1.175-asb-cache-v3`，二者版本不要混淆。host使用已安装的NumPy（现有绘图库依赖），guest不新增依赖。相同的1秒采样周期、匿名缓冲容量和原始格式保持；原逐页计数器仍用于独立复算。最新已完成数据可直接报告，不重跑任务：

```bash
python3 -B scripts/report_live_task_cache.py .artifacts/storage-isolated-service/20261015T050604Z-1849409/prettier6604 --output .artifacts/<新的报告目录>
```

2026-10-09起，用户已持续授权常规实验，不再逐次确认空闲窗口。隔离入口及统一入口可省略`--confirmed-window`和起止时间，默认创建20分钟运行时间范围；这是超时预算，不表示主机独占或其他人不写盘。旧示例的显式时间参数仍兼容；隔离验证脚本如显式设置运行时间而非人工窗口，可加`--automatic-window`。磁盘预算、20 GiB保护量、失败保留及只清理本次资源的规则继续执行。

本入口用于已准备好的实验机，复用固定AgentENV镜像缓存、TrEnv-X模板、CLI和任务测试输入。可选择原服务上的共享盘模式，或容量固定的临时服务模式。当前支持Prettier #14400的26步冻结轨迹；它不是PPT中的Prettier #6604任务。已有冻结轨迹之外，工具接口支持实际调用采集、冻结和重放，在线GLM shell接入也已通过，见下方在线采集；全新机器安装暂缓。

Prettier #6604、Sphinx #7590和Xarray #6992现可通过下面的版本2配置接入同一个`use-platform`入口。默认不带配置仍为Exp1。平台重放结果与原模型任务测试结果分开报告；原模型修复不完整，不修改冻结动作或答案。

## 任务配置和支持范围

版本2配置直接调用已有控制器，使用固定容量临时卷，保留每次独立run_id、生效配置、日志、任务原始结果和清理回执。镜像准备在有界RAM中完成，正式执行仍按现有磁盘预算检查；成功临时卷自动回收，失败卷保留。

| 任务与配置 | 后端及调度 | 恢复边界 | 已有运行证据与本轮入口状态 |
| --- | --- | --- | --- |
| `platform-exp1*.json`：Prettier #14400 | AgentENV、TrEnv-X；单路径 | 隔离模式022；共享模式范围见下文 | 原统一入口已验证。AgentENV为完整VM状态；TrEnv-X仅继承文件状态 |
| `platform-prettier6604.json` | AgentENV；单路径 | 不保存 | 原29步完整路径和持续指标已通过；版本2离线检查通过，同一入口的附带物理测量配置已完整运行 |
| `platform-prettier6604-checkpoint.json` | AgentENV；单路径 | 第24步后，完整VM恢复 | 原恢复与续跑已有成功证据；版本2离线检查通过，未重复真实恢复 |
| `platform-prettier6604-grpo.json` | AgentENV；七个新实例 | 不保存 | 原七实例189动作及持续指标通过；版本2离线检查通过 |
| `platform-prettier6604-bpo.json` | AgentENV；原BPO，七条路径 | 第11、24步后保存，六个恢复分支 | 2026-10-12统一入口84个实际动作、六次状态恢复及七沙箱CPU/I/O/PSS持续曲线通过，见[本轮证据](records/evidence/prettier6604-bpo-runtime-20261012.json) |
| `platform-prettier6604-tvcache.json` | AgentENV；确定顺序前缀树 | 第11、24步后保存，六个恢复分支 | 2026-10-12统一入口17段/84实际动作、六恢复及七沙箱4607持续样本、CPU/I/O/PSS通过，见[本轮证据](records/evidence/prettier6604-tvcache-runtime-20261012.json)；32 GiB卷仍需大于58 GiB主机空闲 |
| `platform-sphinx7590.json` | AgentENV；单路径 | 可另配29，但须使用strict策略及完整评测参考 | 原完整路径、恢复与曲线已有证据；此配置明确采用已批准的管道诊断版本2，不能报告严格重放通过 |
| `platform-xarray6992.json` | AgentENV；单路径 | 可另配22，须提供完整评测参考 | 原完整诊断、恢复与曲线已有证据；明确采用已批准的SIGPIPE诊断规则，严格重放状态保留FAIL |
| `platform-prettier6604-cache.json` | AgentENV；单路径、持续采样和精细分析 | 不保存；第5、24、29步后暂停测量 | 版本2真实29步、三点物理测量、自动报告和离线复算通过，见[证据](records/evidence/platform-task-entry-cache-20261011.json) |
| `platform-prettier6604-shared-1.json`、`platform-prettier6604-shared-2.json`、`platform-prettier6604-shared-7.json` | AgentENV；1/2/7个GRPO新实例 | 不保存；相同前11步后全部暂停测量，再继续原后缀 | 三份离线检查通过；1/2/7实例完整任务、物理测量、持续曲线及清理通过，见[共享缓存记录](records/SHARED_TASK_CACHE.md) |

新增`platform-sphinx7590-cache.json`和`platform-xarray6992-cache.json`均已完成新入口真实35步运行、持续曲线、物理测量、报告和清理。Sphinx在第13/29/35步后测量，Xarray在第13/22/35步后测量；Xarray后两点各有一页未知，保存已确认值及最多4 KiB缺口。两个任务均沿用已批准诊断规则，严格重放状态仍FAIL，模型测试原失败保持。详见[完整任务缓存报告](records/PYTHON_TASK_CACHE.md)。

### Exp3三任务、两后端、三种调度的覆盖

Prettier此前的[轻量GRPO／BPO实跑](records/LIGHT_ROLLOUT_PAIR.md)使用`platform-prettier6604-grpo-light-host.json`和`platform-prettier6604-bpo-light-host.json`显式选择guest 0.1秒meminfo、host 0.1秒计数和1秒全组PSS；这些旧配置仍保持原行为。该次GRPO任务后附加清单不完整与原FAIL保留，任务／曲线已单独交付；BPO实跑和附加清单通过。最新指定文件真实副本对照已完成，使用不同的显式入口、关闭PSS，见[统一结果及用法](records/HOST_COPY_CONTROL_SUMMARY.md)。另两个任务完整七路径仍缺失。

下表只统计完整七路径调度，共18种组合；单路径、Exp1、Exp2和短恢复验证不代替这些组合。学长固定版本的通用controller和三任务冻结输入均在本机，每个任务七条路径、35个逻辑段；这是静态资产，不代表本机18组都运行成功。“未实现”指本平台适配，学长仓库中的实现另列。历史报告和本机原始结果分开使用。

| 任务 | 后端 | GRPO | BPO | TVCache |
| --- | --- | --- | --- | --- |
| Prettier #6604 | AgentENV | 已验证通过：189动作、七实例及新增CPU/I/O/PSS；另有统一入口共享测量变体 | 已验证通过：统一入口84实际动作、六恢复及新增指标 | 已验证通过：统一入口确定树顺序、17段/84实际动作、六恢复及新增指标 |
| Sphinx #7590 | AgentENV | 未实现：本平台七路径适配；学长代码已有 | 未实现：本平台七路径适配；学长代码已有 | 未实现：本平台七路径适配；学长代码已有 |
| Xarray #6992 | AgentENV | 未实现：本平台七路径适配；学长代码已有 | 未实现：本平台七路径适配；学长代码已有 | 未实现：本平台七路径适配；学长代码已有 |
| Prettier #6604 | TrEnv-X | 未实现：本平台Exp3适配；学长代码及本机原模板已有 | 未实现：本平台文件层封存/继承适配；学长代码已有 | 未实现：本平台文件层封存/继承适配；学长代码已有 |
| Sphinx #7590 | TrEnv-X | 未实现：本平台Exp3适配，未找到本机任务模板 | 未实现：本平台Exp3适配，未找到本机任务模板 | 未实现：本平台Exp3适配，未找到本机任务模板 |
| Xarray #6992 | TrEnv-X | 未实现：本平台Exp3适配，未找到本机任务模板 | 未实现：本平台Exp3适配，未找到本机任务模板 | 未实现：本平台Exp3适配，未找到本机任务模板 |

Prettier GRPO原七实例扩展指标见[实际记录](records/evidence/runtime-managed-grpo-20261010.json)，BPO见[本轮证据](records/evidence/prettier6604-bpo-runtime-20261012.json)，TVCache见[本轮证据](records/evidence/prettier6604-tvcache-runtime-20261012.json)及[历史功能](records/evidence/prettier6604-tvcache-20261008.json)。当前按角色修正的inspect段使TVCache执行17段，与学长历史13段区分；三调度的输入与原模型失败保持。Sphinx/Xarray的单路径及恢复已完成，不在本表多路径列中计通过。TrEnv-X Exp2已保存完整VM内存；Exp3的BPO/TVCache须复用学长封存文件层、构建干净模板的流程，仅继承文件状态，不沿用Exp2的进程恢复结论。

Prettier三调度新增指标范围已完成，下一项接入Sphinx/Xarray七路径。TrEnv-X从本机已有Prettier模板的GRPO开始；后续文件层封存和缺失模板另行准备。完整18组覆盖不是本机第一版平台交付的前置条件。首次每配置一次核对正确性与数据完整性，不自动增加统计轮数。

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

## Exp2：四轮保存与恢复

`platform-exp2-preserve.json`和`platform-exp2-drop.json`为AgentENV，`platform-exp2-trenvx.json`为TrEnv-X原生内存snapshot及可写盘模板提升。三组均已实际通过，复用学长Exp2原32个shell动作与20 ms guest runner，2 CPU/4096 MiB；每轮8步，四轮都保存、删除原实例并恢复。`preserve`不主动清缓存，两组`drop`仅在guest保存前执行`sync; echo 3 > /proc/sys/vm/drop_caches`，也会影响可回收slab，宿主机缓存保持。

```bash
bash scripts/use-platform.sh check configs/platform-exp2-preserve.json --offline
bash scripts/use-platform.sh run configs/platform-exp2-preserve.json
bash scripts/use-platform.sh run configs/platform-exp2-drop.json
bash scripts/use-platform.sh run configs/platform-exp2-trenvx.json
```

三条真实命令顺序运行，每次32 GiB固定容量卷，AgentENV要求主机大于58 GiB可用，TrEnv-X沿用大于60 GiB门槛；成功卷自动回收。输入冻结配置为`configs/exp2-prettier6604.json`，不使用Exp3的29步轨迹。TrEnv-X还需本机原`/var/lib/trenvx/templates/prettier-6604-exp2-ch-dax`模板及固定SDK/CH依赖，入口复制并核验文件哈希，不修改原模板；新机要准备这些资产。新增100 ms持续采样和恢复状态探针，原runner不改；其成本包含在本次生命周期中，不能直接与学长旧绝对耗时比较。每轮核对源码、三个`/tmp`案例、步骤进度和进程/匿名内存/打开文件，恢复后只运行下一轮；第四次恢复核对终态。原聚焦Jest与格式化oracle单列，不视作独立任务测试。报告入口沿用`use-platform report <本次运行目录> --output <新目录>`。

三配置各一次、96动作和12次恢复已通过；1375份raw不变，三份17文件报告各两次复算一致，不追加统计轮数。实际状态、曲线及TrEnv-X较高的调用/SDK关闭成本见[Exp2复现记录](records/EXP2_REPRODUCTION.md)。TrEnv-X本实验恢复完整VM状态，Exp3仅文件层继承的语义保持。

TrEnv-X入口现先关闭SDK工具连接、再删除VM，消除已验证的约15秒连接关闭等待；一次额外四轮复验通过，五次删除及关闭合计7.556秒，原动作与恢复语义不变。需要复现关闭顺序短对照时使用`bash scripts/validate-isolated-trenvx.sh --automatic-window --mode exp2 --diagnose-exp2-delete`，它先运行两个短工具实例，再完成同一个四轮控制器；普通统一入口不重复这两个诊断实例。报告使用系统`python3`，SDK专用环境没有绘图依赖；例如`python3 -B scripts/report_exp2.py <raw/replay目录> <新报告目录>`，不修改raw。

TrEnv-X Exp2当前还默认合并首次采样代码上传，恢复后从完整快照继承并核对四份文件哈希；采集器在首个样本前创建目录和阶段标记，停止阶段用Python更新。五个会话的停止、进程回收和数据下载保留；其他任务入口沿用原控制方式。本次统一入口复验通过，监控控制55→26次、73.214→28.355秒，32步、四次恢复和448个100 ms持续样本完整。每次模式、代码继承及实际SHA-256写入raw的`runtime-monitor.json`，不需要新增配置参数。

## 部署检查、资源查询和结果保留

统一入口增加以下操作。查询只读取原生AgentENV资源，模板、snapshot（保存的VM状态）与本地镜像归档分开；输出过滤访问令牌和自定义metadata。每个输出目录须是新的项目`.artifacts`子目录。

```bash
bash scripts/use-platform.sh deployment --output .artifacts/platform-management/deployment-mycheck
bash scripts/use-platform.sh resources --output .artifacts/platform-management/resources-mycheck
bash scripts/use-platform.sh retain .artifacts/platform-tasks/<run_id> --reason '保留此参考运行'
```

`deployment`核对固定源码和子模块版本、版本2配置的输入（现18份）、KVM、ublk控制设备、页大小、host物理页读取权限、cgroup和采集工具；默认读取已准备服务的镜像身份，`--offline`不查询服务。它不安装依赖或重启服务。本机15份历史配置检查已通过，新Exp2三份另通过离线和真实运行；新机器仍需准备`configs/source-revisions.json`固定版本、`runtime/bin/aenv`、依赖、服务及报告列出的本地输入和镜像，凭据在新机自行配置，不复制到Git。配置中的任务路径可改成新准备目录，但输入哈希和原诊断语义保持。

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

### 真实在线Agent采集

```bash
# 只检查输入和模型配置，不发送模型请求或启动沙箱。
bash scripts/use-platform.sh collect-demo --agent-config configs/online-agent-glm.json --check-only

# 模型生成命令、在AgentENV实际执行，冻结后在新沙箱重放并自动报告。
bash scripts/use-platform.sh collect-demo --agent-config configs/online-agent-glm.json
```

在线模式沿用上述两个2 CPU/512 MiB沙箱和8 GiB固定卷，启动要求主机大于34 GiB可用。首个任务为CSV分类求和：模型编写并执行Python脚本，平台独立检查原输入及另一份输入的结果。模型循环位于host，只有run_shell命令通过现有capture接口进入guest；stdout/stderr及非零退出码返回模型，完整原文留存。每调用新建bash，文件持久，shell变量和当前目录不跨调用继承；工具cwd固定在本任务目录。

配置含模型、API地址、凭据环境变量名/私有文件路径和次数/超时上限，不含密钥。默认模型为用户指定GLM，最多12次模型请求、12次工具调用、单次请求90秒、在线循环480秒；模型等待与guest工具用时分开，重放不请求模型。API密钥只在host调用中读取，不传给guest、不存入对话或请求记录。当前用户指定的HTTP接口不加密；HTTPS证书过期，不绕过证书校验。

默认监控覆盖在线等待、工具执行和新实例重放，生成内存、CPU、I/O及host VMM RSS/PSS曲线。此配置不做暂停式物理页分析。轨迹先冻结，再独立检查任务；任务FAIL仍保留真实退出码和输出，并继续验证其重放，不把模型任务失败等同平台失败。复算仍使用`use-platform report <成功运行目录> --output <新目录>`；平台成功要求轨迹重放、任务检查结果保持和清理均通过，模型任务是否PASS另列。

2026-10-12已实际通过：工作流`.artifacts/platform-tasks/20261012T092932Z-1042295`，4次模型请求/3次工具，独立任务检查和新VM严格重放PASS，173个guest样本及默认性能曲线；10文件报告独立复算一致。此前失败和动态目录列表原文保留，新提示明确不使用ls长表；未通过忽略stdout或放宽退出码获得PASS。详见[结果](records/evidence/online-agent-20261012.json)。

### 在线任务配置和冻结轨迹测量

任务配置与模型配置分开。`online-task-csv.json`保留已通过CSV任务；`online-task-prettier6604.json`选择固定研究任务、4 GiB内存、原问题说明、已知镜像初态、仓库状态和独立评测。模型预算位于`online-agent-glm-prettier.json`：20请求/30工具、模型请求90秒、单工具120秒、在线循环480秒。新任务输入只取problem_statement，不向模型发送参考补丁。工具环境和新bash会话语义保持明确。

```bash
# 离线核对配置，不调用模型或启动VM
bash scripts/use-platform.sh collect-demo configs/platform-prettier6604.json \
  --task-config configs/online-task-prettier6604.json \
  --agent-config configs/online-agent-glm-prettier.json --check-only

# 在线采集后自动冻结，再在新VM重放；需要本机已准备输入和私有API凭据
bash scripts/use-platform.sh collect-demo configs/platform-prettier6604.json \
  --task-config configs/online-task-prettier6604.json \
  --agent-config configs/online-agent-glm-prettier.json

# 本次已冻结轨迹的物理分析，不请求模型；12/30仅适用于这条30步轨迹
bash scripts/use-platform.sh collect-demo configs/platform-prettier6604.json \
  --task-config configs/online-task-prettier6604.json \
  --replay-source .artifacts/storage-isolated-service/20261012T104130Z-1075868/prettier6604/captured \
  --physical-after 12 30
```

Prettier在线/冻结分析均使用16 GiB固定卷，启动要求host大于42 GiB可用。`--replay-source`不能同时带`--agent-config`；重放核对相同镜像、资源及初始仓库状态。每步原命令、cwd/env、退出码和输出保留；完成工具前缀可在模型预算耗尽时冻结，结果明确记录模型未完成。仓库HEAD、完整diff、tracked/untracked修改及对应文件哈希在轨迹内，不代表任意进程、所有/tmp文件或持久PTY恢复。

另有`configs/online-task-prettier6604-focused.json`改进调查/修改/测试的提示，并启用每轮剩余次数说明；替换上述`--task-config`即可，其他参数相同。本机11项相关测试通过。10月12日两次真实运行的首请求503记录保留；10月13日恢复后已完成14请求/18工具前缀的采集、完整冻结重放及默认曲线。模型第14次回复被4,096 token上限截断、没有源码修复，独立任务测试FAIL保持。见[本轮结果](records/evidence/online-prettier-focused-20261013.json)和[旧接口失败](records/evidence/online-prettier-focused-api-20261012.json)。

`configs/online-agent-glm-prettier-8k.json`可作为单次输出预算变体替换`--agent-config`：8,192 tokens、单请求135秒，总次数20/30及在线480秒保持。该配置已实际运行一次，17步冻结重放和曲线通过；16号请求在总时间边界超时，源码未修改。它不是成功修复配置，也没有验证成功接收超过4,096 tokens的回复，见[本轮数据](records/evidence/online-prettier-8k-20261013.json)。不建议无新假设反复运行或继续加上限。

用户现指定`deepseek-ai/DeepSeek-V4-Pro`，使用`--agent-config configs/online-agent-deepseek-prettier.json`替换模型配置，其他预算与上述8k组相同。已实际收到20次响应并冻结24工具，新VM完整重放及曲线通过；模型仍未修复，六次提交JSON形式的shell字符串失败，结果原样保留。见[DeepSeek记录](records/evidence/online-prettier-deepseek-20261013.json)。当前`prettier-runtime-v1`另外仅对完整命令`cd /testbed && ls -la`的stdout单列`..`和`.git`两处已核实目录时间，其他内容不放宽；原严格FAIL及旧失败工作流保留。

模型system和工具说明现明确`command`为纯shell字符串，提供正确参数示例，不自动解包模型错误字符串。新一轮收到7响应、执行7工具，第8条API请求精确等待135.102秒后超时；七步冻结重放、曲线及报告复算通过，源码仍未修改。见[新记录](records/evidence/online-prettier-deepseek-format-20261013.json)。已核实目录时间规则还覆盖完整命令`cd /testbed && pwd && ls -la`，pwd内容和其他输出保持检查，原严格FAIL保留。当前不建议为等待接口而反复启动沙箱。

在线请求现逐条保存`online/NNN-timing.json`，使用单调时钟记录完整HTTP响应等待（纳秒）、实际请求超时（秒）及响应/失败状态。`result.json`的`model_requests`包含失败请求，`model_wait_total_ns`为全部请求等待；原`model_calls`及`model_wait_sum_ns`仍只计成功解析的响应。旧结果缺少失败计时时保持原样，不用文件时间估算补写精确值。

仅诊断接口时可使用下面的host命令，不启动沙箱，也不执行返回的工具。输出目录必须不存在；最多两份保存请求，默认额外请求一次短回复，`--skip-short`可只测保存请求。60秒是每次socket超时，外层`timeout`限制整个进程；被外层终止的批次没有完整最终汇总，应查看已保存的逐条记录。本机三请求已验证，见[结果](records/evidence/model-api-wait-20261013.json)，无需日常重复。

```bash
timeout --signal=TERM --kill-after=10s 210s python3 scripts/diagnose_model_api.py \
  --config configs/online-agent-glm-prettier-8k.json \
  --request <已有online目录>/001-request.json \
  --request <已有online目录>/016-request.json \
  --timeout 60 --output <新的本地结果目录>
```

| 在线任务 | 实际证据 | 任务结果与边界 |
| --- | --- | --- |
| CSV分类求和 | 真实4模型请求/3工具，严格新VM重放及两份CSV独立检查通过 | 模型任务通过；没有物理页扫描 |
| Prettier #6604 | 真实20模型请求/30工具前缀，完整冻结重放、默认持续曲线与两个物理点通过 | 模型预算耗尽、未修复；任务测试0通过/2失败保持 |
| Sphinx/Xarray在线任务 | 未接入任务配置 | 已有学长冻结轨迹证据，不能当作在线模型证据 |

Prettier的`prettier-runtime-v1`输出规则仅单列标准Jest/Yarn时间和完整已知DEP0128警告的Node PID，测试名称/数量/结果、命令、退出码和文件保持核对；严格原文差异仍记录为FAIL。精细分析使用现有探针，保留三个12 KiB校准文件页，VM暂停成本单列，默认轻量曲线不断采集但不跨暂停插值。Cached和按host PFN去重的物理文件页分别显示；当前两点不用于宣称缓存控制带来的性能改善。

本次完整重放工作流为`.artifacts/platform-tasks/20261012T105241Z-1086700`，最终复算报告为`.artifacts/platform-tasks/report-online-prettier-recomputed-1`。原自动报告的demo标签/NOT_RUN字段保留，离线复算已改为实际Prettier任务和官方FAIL；原raw没有修改。模型任务失败与重放通过分开显示。新轨迹先根据state-after及实际写操作选点，不能套用本次12/30或学长旧轨迹编号。详见[当前结果](records/evidence/online-prettier-20261012.json)。

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

针对目标首次调用，另有已离线验证的`bash scripts/validate-runtime-command.sh --env-command-timing whole`或`--env-command-timing split`：只保留前11步，随后原样或分项测目标一次，不先跑工具12／13或热缓存重复。[前缀、计时及验证说明](records/SPHINX_ENV_TIMING_OFFLINE.md)及[两guest实跑](records/SPHINX_ENV_TIMING_LIVE.md)已完成：原样1.567秒、拆分1.943秒，没有复现旧慢调用，到两组停止。新模式无内存采样或进程扫描，不改正式35步、默认回收及pip环境。原报告命令识别新结果并生成分项表，不授予完整任务PASS；旧无参数入口保持上述行为。

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
