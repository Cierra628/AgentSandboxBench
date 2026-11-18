# Django原路径的三调度对照

## 2026-11-13：Django TVCache单次时间补测通过，首轮为低值异常

[补测报告](../../results/django-tvcache-time-check-20261113T075257Z/OUTCOME.md)：只新增一次TVCache，64实际／196逻辑动作、2保存6恢复、固定补丁与语法、采集和清理均PASS；未运行GRPO／BPO。新调度窗口53.785844秒、动作及下载窗口27.545503秒、工具累计11.856305秒、控制程序整轮60.523603秒，接近原第2、3轮。四次详细起止、逐动作、保存恢复和七实例跨度均已另存。

原背景表的时间列错位：13.685／26.747／27.842是动作窗口，非调度窗口；工具累计为3.731／10.915／12.841秒。四次存档窗口公式及guest运行器一致，起止差和逐动作累计能复核，未发现计时定义变化。首轮为本批低值离群观测，建议时间列标注可比性异常、主性能对照暂不可用，内存仍按原验收保留；未证明计时失效或快慢原因。原第5／10／14／15步搜索命令解释了首轮相对后3次均值工具耗时差的96.14%。

报告同时给含首轮n=4与不含首轮n=3统计，以及原正式三轮的n=3／n=2敏感性表；未替使用者选定口径，未改45份正式总表或任何原始值与历史判定。新一次终点file差0，周期10/606点、1.650165%、最长0.500397017秒、全部恢复，四类峰值未受影响；无stop超时或强杀。收尾可用165.165779 GiB，未额外清理。

下一步先确认采用哪套时间口径；若需解释原因，建议仅对已有四次日志做CPU／读取离线对齐并列明缺失证据，未启动新增业务。以下为历史记录。

## 2026-11-13：五任务正式总表已更新为45份，各调度n=3

[离线汇总](../../results/slice-task-summary-n3-20261113T072051Z/REPORT.md)已纳入Django／C4／Matplotlib／文本编辑／Cython共45份新父组范围数据，全部采用segments_v3。内存和时间总表共15组、每组3份，完整给出均值、中位数、极差及样本标准差；Django三轮逐份标注来源、等权统计，首轮GRPO的ACCEPTED_SEGMENTS_V3与原FAIL并存，旧第三轮采样超时FAIL排除。

重新判定：五任务均值及中位数的PSS/N均为GRPO最高，但C4第3轮、文本编辑第2轮有反例。TVCache调度窗口最长在四任务均值下成立、五任务中位数下成立；Django均值BPO49.472 > TVCache46.860 > GRPO41.968秒，中位数则TVCache51.455 > BPO49.392 > GRPO41.946秒。Django首轮TVCache最短不再代表三次统计，逐轮反例完整保留。

两种current峰值与PSS/N的均值排序仍有4/5任务不一致，仅Cython一致；中位数下采集峰值5/5、调度峰值4/5不一致。覆盖边界逐任务保留PARTIAL／UNKNOWN及变化对象；普通文件严格PARTIAL份数为6／6／8／6／6，块设备含UNKNOWN份数0／4／0／1／0，loop UNKNOWN份数0／1／2／1／0（任务顺序同上）。未发生业务运行、重采集或历史结果改动。

独立复核255个指标统计和任务／逐轮排序通过，5项相关离线测试通过。下一步仅建议先明确内存、延迟预算及起点范围，再设计最小部署容量验证；未启动新增实验。以下为历史记录。

## 2026-11-13：Django第三轮完成，三调度各3份有效数据

[完成报告](../../results/django-slice-v3-third-round-20261113T062231Z/OUTCOME.md)：本批仅新增第三轮TVCache→GRPO→BPO，三次全流程PASS；复用前两轮六份后，各调度n=3。第一轮GRPO仍为ACCEPTED_SEGMENTS_V3，原矩阵FAIL和上批第三轮采样超时FAIL均保留、不重写。

新三份专属slice父组、全部直接子组四项、七root VMM PSS（分母7）、有效权限下原10层10活跃文件mincore和全部计时复核通过。终点file差均0，周期最长分别0.099696983／0.100063728／0.200103901秒，偏离占比0.166113%／0.202429%／0.891266%，均恢复，峰值均未触发替代，segments_v3通过。完整内存和时间表含均值、中位数、极差、样本标准差，旧Docker仅单列对照。原28步、18／26保存点、创建流程和磁盘门槛不改。

采样器新增5秒超时后定向SIGKILL、最多2秒确认消失，回收成功仍FAIL／INCOMPLETE；34项相关离线测试通过。本批没有超时或强杀事件，不将正常结束称作实机强杀验证。用户授权的旧文本编辑64 GiB失败卷已定向删除，清理前后可用102.055050→166.055119 GiB；收尾可用165.794125 GiB。自有VMM、容器、slice、挂载和loop均退出，源服务恢复停止。

下一步仅建议只读更新五任务正式总表，将Django由各n=1更新为各n=3，核对跨任务规律与覆盖边界；不增加业务运行，尚未开始。以下保留此前各阶段历史记录。

## 2026-11-13：采样器超时回收离线通过，Django第三轮因空间不足未启动

[本次报告](../../results/django-stop-kill-offline-20261113T062231Z/REPORT.md)：guest stop原5秒超时后，新增仅针对该段身份的SIGKILL及最多2秒消失确认，事件写入stop-timeout.json及会话记录；成功回收仍FAIL／INCOMPLETE，原错误保留。PID句柄及二次身份检查防止误杀其他进程。34项相关离线测试通过，没有启动真实沙箱验证兜底。

新增显式Django前两轮复用入口，六份有效数据不重跑，第三轮清单固定TVCache→GRPO→BPO。启动检查可用102.572014 GiB，低于恢复组原严格>106 GiB门槛，三次均未启动，没有启动源服务或新建卷、不自行清理。GRPO原48 GiB／>74 GiB、BPO／TVCache原80 GiB／>106 GiB不变；待空间恢复后再按原顺序执行。

历史FAIL不变，Django仍三调度各n=2，未更新五任务正式总表。本次未调整验收标准、创建流程、原28步、18／26保存点、segments_v3及其他等待。下一步仅是解决空间后复查预算，再完成第三轮；任何超时或其他FAIL立即停止保留，不自动重试。

## 2026-11-13：Django新增3份通过，第3轮TVCache采样收尾失败后停止

[本批部分结果](../../results/django-slice-v3-supplement-20261113T050808Z/OUTCOME.md)记录：第2轮BPO→TVCache→GRPO三次全流程PASS，第3轮TVCache在guest采样器monitor-stop等待退出5秒后超时，按约定保留FAIL并停止。第3轮GRPO／BPO未运行，没有自动重试。已有第1轮三份原判定不变，其中GRPO仍为ACCEPTED_SEGMENTS_V3、原矩阵FAIL保留；当前有效6份，三调度各n=2，未达到正式各3次，未更新五任务正式总表。

新增三份父组、全部直接子组四项、七root VMM PSS（分母7）、原10层去重文件P1与全部计时复核通过；终点file差均0，周期最长0.100076922／0.500354790／0.300203987秒、占比0.358423%／2.065404%／0.814664%，均恢复，四类峰值均不受影响，segments_v3全PASS。报告已有各n=2的均值、中位数、极差、样本标准差和历史Docker对照，不把n=2称作三次重复。

失败原始目录`.artifacts/storage-isolated-service/20261113T051515Z-2281603/`，报错见`external-workload/0188-monitor-stop.stderr`：`TimeoutError: owned collector did not exit; retained for diagnosis`。原64实际／196逻辑动作、七份补丁和语法通过，但9段采样会话中1段FAIL、总采集INCOMPLETE，故不纳入统计。离线诊断父组终点差0、最长偏离0.700574156秒、9/772点，闭合通过不能替代缺失的guest收尾收据；未保存超时时进程状态，底层超时原因未确定。

失败80 GiB卷保留，没有额外删除。自有VMM、容器、slice、挂载和loop均已退出，源服务恢复停止；收尾可用27.348904 GiB，不满足BPO／TVCache原严格>106 GiB门槛。下一步仅建议先离线定位采样器退出路径；修补、失败卷清理和剩余三份续跑须再确认，不自动启动。

## 2026-11-13：Django补齐第2／3轮六次已启动

用户要求保留新父组第一轮三份，只补第2轮BPO→TVCache→GRPO、第3轮TVCache→GRPO→BPO。新增显式`--reuse-django-first-round <原摘要> --reuse-v3-summary <既存v3复算>`，只用于完整Django新范围续跑；普通默认三轮九次、其他任务的已有续跑行为不变。第一轮GRPO在新批次标为ACCEPTED_SEGMENTS_V3，原FAIL和旧字段留在原记录；BPO／TVCache保持PASS。没有重跑或修改第一轮文件。

28项相关离线测试通过，实际三份保存数据、元数据、父组路径与v3收据绑定通过，待运行清单恰为六次。GRPO48 GiB卷、严格>74 GiB；BPO／TVCache80 GiB卷、严格>106 GiB，原门槛不变。启动前最新可用107.399612 GiB、MemAvailable486.782112 GiB，各次继续原入口资源检查，不足即停、不自动清理。

原28步、18／26保存点、七实例相同路径、2 vCPU／8 GiB保持。主值为专属slice父组，保存父／直接子组四项、七root VMM PSS（分母7）、有效10层全部活跃文件P1与全部计时。segments_v3不改，普通0.1秒、PSS1秒及终点，默认回收，不含更早卷准备。镜像和任务用本地输入、代理清空，不启动五个外部任务。

预检、最小改动备份及测试：`.artifacts/django-v3-supplement-preflight-20261113T050808Z/`。本批：`.artifacts/django-slice-v3-supplement-20261113T050808Z/`，结果另存同名results目录。此条仅记录启动，不表示验收完成；首次FAIL停止保留、不自动重试，六次完成或失败后汇报。

## 2026-11-11：专属slice父组范围，第一阶段三次验证

用户授权先用现有入口重跑Django三调度各1次，共3次；其他四任务必须等本阶段结果获确认后再启动。新增显式`--rounds 1 --experiment-memory-standard`，普通入口默认仍为原三轮九次，不改systemd／Docker创建流程及原28步、18／26保存点、原检查与磁盘门槛。

父组总量按[范围定义](EXPERIMENT_MEMORY_SCOPE.md)采集，父组与全部直接子组的current／file／anon／kernel另存JSONL，七VMM的PSS、原10镜像层全部活跃物理副本P1、扩展缓存及原计时保持。旧Docker读数及历史72份不修改；无父组不回退，file闭合差额超过固定1MiB或采集缺失时记录异常、停止本批，不自动重试。新报告将同次父子值和历史Docker值分开。

原私有服务TSV和新父组序列均从任务内采集器启动至清理，不含此前卷／输入准备。本次报告不称全准备过程峰值；第一阶段每调度最多n=1，样本标准差为空。新批次`.artifacts/django-slice-stage1-20261111T051900Z/`，配套预检和20项离线测试位于`results/django-slice-stage1-20261111-offline/`。

实际结果见[首份报告](../../results/django-slice-stage1-20261111T051900Z/OUTCOME.md)：GRPO原196动作及任务验收PASS，父组可取，终点file闭合且比Docker多1494 MiB；485周期点有4个file差额超过1MiB，最大621.011719 MiB，因此外层新范围检查FAIL后停止。BPO／TVCache未运行，第二阶段未开始，没有重试。全部原始值与日志保留，当前内核源码和前后点支持统计异步更新解释，未追踪到每次刷新，不调阈值或补值。以下是此前完成的旧范围记录，不代表本次结果。

用户于2026-11-09确认：七个沙箱最终执行Django #16819相同原28步，在原第18／26步后保存并复用前缀。它是受控前缀复用负载，不是原生七分支。GRPO实际196／逻辑196动作；BPO、TVCache实际64／逻辑196动作、各2次保存6次恢复。

三调度各3次，三轮顺序分别为GRPO→BPO→TVCache、BPO→TVCache→GRPO、TVCache→GRPO→BPO。首份正式结果兼作路径验证，不追加短测，不自动重试实际失败。前三任务和旧单guest结果不混入。

当前有效9／9：[完成报告](../../results/django-schedule-matrix-resume-20261109T061236Z/OUTCOME.md)复用[首轮GRPO](../../results/django-schedule-matrix-local-20261109T041653Z/OUTCOME.md)并新增8份，三调度各3次。原动作／退出／固定补丁和语法、身份、采集及清理通过，完整官方套件未运行。扩展普通文件六份恢复组严格PARTIAL，其中第二轮BPO还有1个查询期变化对象；窗口与严格状态分列，未知不记零。首份GRPO与后8份之间宿主重启，起点未统一，不将差异归因于调度。

任务全部完成后，报告生成因复用旧目录曲线的链接报错；只修正跨目录相对路径，7项相关离线测试通过，用保存数据重新生成报告，没有业务重跑。当前无待补项或实验运行，不再追加同类轮次。

此前GRPO在manifest get遗漏接线处失败的[原记录](../../results/django-schedule-matrix-20261109T035126Z/OUTCOME.md)保留；用户批准的[离线修补及清理](../../results/django-local-metadata-fix-20261109/REPORT.md)通过29项相关回归，旧48 GiB失败卷已删除，日志保留。不把该失败计入有效重复。

## 输入与验收

固定配置 `configs/external-django16819-light.json`；本地OCI `.artifacts/django-oci/django16819/`，不联网或使用代理。固定上游版本、原28个脚本与最终补丁的来源见[准备报告](../../results/django-schedule-preparation-20261109/REPORT.md)及同目录plan.json。

原第17步退出1，其余退出0，持续Bash source语义不加pipefail。恢复时核对进度、源码补丁、备份文件及原进程连续性；七实例最终均匹配原最终补丁并通过原py_compile检查。完整Django官方测试未运行，不写成官方全通过或Prettier的0通过／2失败。

## 采集范围不扩成物理总量

- current主指标为私有服务及VMM后代cgroup，另列file／anon／kernel等；不含外层控制和组外输入准备。
- 普通host／guest计数0.1秒；VMM PSS1秒和终点查询，PSS/N仅七VMM比例驻留平均。
- 原10镜像层的全部活跃设备号:inode副本P1；扩展普通文件、块设备cachestat、独立RAM、loop及未链接TMPFS分别报告窗口、严格状态、未知和成本。不按内容相同合并不同物理inode。
- 全窗口与调度窗口current峰值分别报告；调度窗口含创建／保存恢复和原动作下载，不含随后语法检查／查询及清理。
- 工具累计、动作及下载窗口、调度窗口、整轮和保存恢复计时分别保留。默认回收；不加P0冷起点、压力、周期mincore或额外预读。

## 磁盘与执行

每guest 2 vCPU／8 GiB。GRPO48 GiB卷、可用盘严格>74 GiB；BPO／TVCache80 GiB卷、严格>106 GiB。20 GiB保护及6 GiB余量不变，资源不足停止，不自行清理或缩卷。恢复卷预算另含两份8 GiB RAM快照、镜像副本及临时空间，初始10层总长度须≤4 GiB。

```bash
python3 -B scripts/run_django_schedule_matrix.py \
  --inputs .artifacts/django-schedule-preparation-20261109/inputs-ready \
  --upstream .artifacts/workload-and-upper-20261030/upstream \
  --oci-input .artifacts/django-oci/django16819 \
  --output .artifacts/django-schedule-matrix-<新时间> --execute
```

每组新私有服务与cgroup，成功后清理自有资源；报告和曲线放 `results/<批次名>/`。实际失败先保留并报告，不自动修后重跑；检查器问题先用原始数据离线修复。九份有效结果后分别给均值、中位数、范围和样本标准差，不追加轮次。

本次已使用新output及 `--resume-from .artifacts/django-schedule-matrix-local-20261109T041653Z` 完成剩余8项，未传 `--retry-failed`，没有重跑成功组。此前失败历史与旧output保留；九次约定已完成，此示例仅说明入口，不代表继续执行授权。
