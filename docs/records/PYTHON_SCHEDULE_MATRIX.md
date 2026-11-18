# Sphinx／Xarray三调度重复：完成范围与复现入口

## 当前结果：18／18完成，本阶段收尾

[最终汇总](../../results/python-schedule-matrix-final-20261108T160912Z/OUTCOME.md)及[全部逐次值、统计和曲线](../../results/python-schedule-matrix-final-20261108T160912Z/REPORT.md)已交付。Sphinx／Xarray分别完成GRPO、BPO、TVCache各3次，原七角色任务、身份、主指标及清理通过，官方指定测试结果逐角色一致；历史失败尝试未计入重复。当前没有待补槽位或运行中的实验。

2026-11-09离线修订：原全采集窗口current峰值保留，另从`schedule_start_monotonic_ns`至`original_paths_end_monotonic_ns`（含两端）计算调度窗口峰值，包含创建／准备、原动作、保存恢复及下载，不含后续独立最终测试。Xarray九次原全窗口峰值均与独立pytest调用重合，逐次时刻与对齐证据见最终目录`report-revision.json`。普通文件窗口合计、有效对象数及原严格状态已在主表展示，合计包含原镜像P1，不与P1相加，不代表全部host page cache或同一时刻物理并集。最后批次只新增Xarray，Sphinx九份全部复用；原各组条件不变。6项报告回归及18份原字段／原六项统计保留检查通过，没有启动沙箱或重新采样。

原镜像P1：GRPO每组13个活跃物理文件，恢复调度每组26个；upper和新增快照层由扩展查询覆盖。18组普通文件查询窗口均无未知来源；12组原严格普通文件状态PARTIAL保留。另有Xarray首轮BPO一个RAM严格来源未知、首轮TVCache一个loop严格值未知；独立固定RAM／loop窗口分别有效，不补旧严格值，不形成跨层物理总和。

两任务每组2 vCPU、4 GiB／guest，host普通计数0.1秒、PSS1秒，默认回收，无额外缓存控制。所有成功卷及临时服务／挂载／loop清理，RAM输入释放；源服务恢复停止，其他工作区保持。本阶段不追加验证，另五项仍先定分支。

2026-11-08，用户已选择：先补Sphinx／Xarray的原七分支，Django、C4、Cython、Matplotlib和文本编辑另定分支，不用七份相同路径替代原生分支。准备始于2026-11-08，2026-11-09已完成两任务三调度各3次。当前结果见下节，前期阶段记录保留作来源。

同日后续：用户授权清理两份32 GiB失败卷并开始实验。新入口和采集已接入，但两次启动均在创建任务前被磁盘门槛拦截，实际0次；[清理、启动与空间变化报告](../../results/python-schedule-start-20261108/REPORT.md)保留完整证据。以下结构与18次顺序不变，运行接入状态按下文更新。

随后空间恢复，第三批取得Sphinx第一轮GRPO完整记录；BPO保存恢复边界通过，但新发现的`early_2_inspect/004`管道141／0差异未在原诊断例外内，已停止并[离线定位](../../results/python-schedule-bpo-failure-20261108/REPORT.md)。用户已确认精确双输出规则及旧失败卷清理，后续重试104动作通过，但在终点文件选择失败；[恢复副本漏采修正](../../results/python-restored-lower-fix-20261108/REPORT.md)已完成离线验证。上述两个BPO失败尝试均不算完整重复。随后用户确认清理，取得首轮BPO与TVCache完整结果；当时为3／18有效。两恢复组均真实查询26个原镜像层物理文件，七VMM PSS、分层及最终任务检查通过。第二轮BPO另在trace_cpp_parser/005预期0／实际1处停止，剩余15槽位未完成；[实跑与定位](../../results/python-schedule-matrix-copies-20261108T141958Z/OUTCOME.md)保留新原始路径及待确认条件。

## 阶段记录：Sphinx9次与Xarray首轮GRPO完成

用户授权清理Xarray旧40 GiB失败卷后，批次`.artifacts/python-schedule-matrix-xarray-20261108T153217Z`取得Xarray首轮完整GRPO，七角色230动作、补丁保真比较、七分支最终检查、身份／PSS／物理缓存分层和清理通过。当时10／18有效；Sphinx9份复用，Xarray剩余8槽位。物理指标、完整计时及曲线见[本轮结果](../../results/python-schedule-matrix-xarray-20261108T153217Z/OUTCOME.md)。

BPO在首次保存前遇到新的trace_index_state/001预期0／实际1，原规则拒绝，未进入恢复或终点采集。原输出、清单及空补丁与成功GRPO七角色一致，被抑制错误原因未知。当时需先确认仅此Xarray动作的精确0／1诊断和64 GiB失败卷定向清理；随后已获确认并完成剩余运行，原FAIL不改。

## 前一阶段：Sphinx9次完成，Xarray待补9次

用户确认精确find诊断例外并清理原失败卷后，新增6次Sphinx全部完成，与首轮3次组成各调度3轮。报告含四类主要指标、current、完整计时及查询成本的均值／中位数／范围／样本标准差和逐次曲线，见[本轮汇总](../../results/python-schedule-followup-20261108T151805Z/OUTCOME.md)。所有组的原镜像活跃物理键集合与P1一致：GRPO13对象，恢复组26对象；同内容不同inode不合并。当时有效9／18，不包含原失败组。

Xarray首轮完成230动作与终点采集，但最终检查器.strip()截掉补丁尾部上下文空格，误报三个early分支；原七份输出与归档逐字相同。已只对补丁输出启用保真传递，原读取默认及内容检查条件保持，17项相关回归通过。三个early最终测试未执行，仍保留整组FAIL，不以离线比较替代缺失测试。

当时从`.artifacts/python-schedule-matrix-find-20261108T150156Z`继续；后续Xarray自身完整运行已验证恢复副本和各分支结果，不用Sphinx数据代替。

## 两个任务各9次，先Sphinx后Xarray

每个任务使用同一固定镜像、原七角色和三种调度，分别重复3次。顺序沿用Prettier已使用的三轮轮换：

1. GRPO → BPO → TVCache。
2. BPO → TVCache → GRPO。
3. TVCache → GRPO → BPO。

合计18次新运行。旧单guest重复只作为适配依据，不算本轮GRPO；Prettier结果独立保留，不混算。每条新路径的第一份正式结果兼作必要运行验证，不另加短测或替补轮次。任务失败、平台失败和检查器失败分别记录；出现实际失败先保留现场并定位，不自动重跑。

## 原轨迹已经提供分支，不需要新调用模型生成

来源分别为`IncrementalDAX_moti/motivation/experiments/exp3-RL-fork/workloads/`下的`sphinx-7590-rl-fork`和`xarray-6992-rl-fork`。已读取其`rollouts.json`、`agent-sampling.json`及各段`actions.tsv`，核对角色、前缀、动作数量和主干原始预期退出码；没有重新计算文件摘要或读取业务数据集。

| 原轨迹结构 | Sphinx #7590 | Xarray #6992 |
| --- | ---: | ---: |
| backbone完整路径 | 35动作 | 35动作 |
| early_1／2／3各自完整路径 | 各25动作 | 各25动作 |
| late_1／2／3各自完整路径 | 各40动作 | 各40动作 |
| GRPO实际／逻辑动作 | 230／230 | 230／230 |
| BPO实际／逻辑动作 | 104／230 | 104／230 |
| TVCache实际／逻辑动作 | 104／230 | 104／230 |
| BPO／TVCache保存／恢复 | 2／6 | 2／6 |
| 初始lower对象参考数 | 13 | 13 |

第一次分叉在主干第2段结束，即第13动作后；第二次在第4段结束，即第29动作后。早分支各自补12动作、晚分支各自补11动作：`35 + 3×12 + 3×11 = 104`。Xarray旧单路径第22动作的保存恢复验证不是本轮第二分叉点，不能直接替代。

GRPO七根分别完整执行；BPO执行主干并保存两个前缀，再恢复六个后缀并发执行；TVCache按原确定性前缀树逐子树推进。TVCache沿用本项目已有实现含义，不新增动态容量或淘汰策略。

## 运行入口和采集已接入，两任务各9次完成

新增显式`run_python_schedule.py`和原隔离生命周期参数`--python-schedule`，不改变普通入口。两任务正常路径、保存恢复和完整采集均已实跑，原入口与新入口的区分如下：

| 原入口限制 | 新入口实现与验证条件 |
| --- | --- |
| 普通平台配置把非single调度限制为Prettier | 保留此限制，通过新增显式Python入口接入，不修改普通配置默认 |
| 旧`run_xarray6992.py`只执行backbone | 新入口复用既有控制器，接入七角色、2次保存／6次恢复、逐实例采样及清理；动作顺序与原分支退出码按角色检查 |
| 原终点固定19文件 | 新入口登记原始13层身份，各实例按有序digest／大小确认这些层，再按设备号:inode查询所有活跃物理副本；同内容不同inode不合并，单用户父文件纳入。报告分列13逻辑层和实际物理文件数；旧Prettier默认19个检查保持 |
| 旧诊断只读取主干目录 | 新验证器按实际角色读取，继续调用原精确命令／输出例外；没有取消旧单路径限制 |
| 后备设备、恢复RAM与loop必须分开确认 | 18次均有按真实对象枚举的查询记录；独立RAM及loop窗口与原严格状态分列，缺项保留，不硬套Prettier的物理量 |
| 原结果检查只在主干单路径实跑过 | 新入口主干继续比较固定补丁及原官方数量；其他六分支执行原命令、保存完整补丁并分别做独立测试，不套用主干数量。其他分支没有独立完整补丁参考，明确标记，不声称已与固定补丁逐字一致 |

离线计划器的`runtime_ready=false`仍是原静态字段，不参与实际运行验收。两任务各三调度3次的实际任务、指标和清理状态见最终结果；不要用计划文件的静态标记覆盖已取得的运行证据。补丁输出保真修复及两任务恢复路径均已实跑。

## 每次采集的范围统一

与Prettier新版三调度一致，host主计数选**新私有服务cgroup**，包括服务及全部VMM后代；组外输入准备与外层控制单列，不能和旧单guest外层实验组的绝对current混算。

- 普通host／guest计数0.1秒；PSS周期1秒，终点核对七VMM总量及PSS/N。
- current、file、anon、kernel曲线；shmem、file_dirty、file_writeback按实际保存频率标明。无周期字段不伪装成已有曲线。
- 终点一次原镜像13层的所有活跃物理文件P1及扩展普通文件mincore；按设备号:inode去重，不按digest合并副本，记录实际文件数。恢复新增快照层仍在扩展查询，覆盖活跃upper、env/run/tmp和FD/maps来源，已查询对象复用同次结果。
- 终点块设备cachestat、恢复RAM固定设备查询、TMPFS和准确loop；各自范围、状态和成本分别报告，不跨层相加。未发现或未采集不填零。
- 主报告使用[host物理指标方案](HOST_PHYSICAL_METRICS.md)，guest Cached等另图；原严格PARTIAL、窗口读数及实际未知分别保留。
- 保存逐动作和逐guest工具跨度、工具累计、调度窗口、动作及下载窗口、整轮以及每次保存／恢复耗时；逻辑与实际动作分列。
- 保存身份链、逐项结果检查、采样墙钟／CPU、有效点数／实际间隔、磁盘最低余量及清理。新结果使用新目录，不覆盖旧记录。

默认回收保持，不加压力、不新增P0冷起点、不清缓存、不暂停、不预读、不强制驻留，不开周期mincore或广泛扫描，不新增代理授权。

## 资源准备与运行条件

最初准备时可用盘约58.58 GiB、宿主可用内存约474 GiB、swap为0。用户随后授权删除两份失败卷，共释放64 GiB；可用空间曾回到102.155 GiB，随后因共享盘持续写入再次不足。另一工作区的`asb-five-recovery-source-20261106`保持运行。

新模式实际卷容量为GRPO 40 GiB、Sphinx恢复组56 GiB、Xarray恢复组64 GiB；分别要求可用盘严格超过66／82／90 GiB。GRPO没有快照，按实际用途不预留保存恢复空间；保护线没有降低。复制后还按七guest写入、两份RAM／镜像后备及临时空间核对卷内预算，原值保存在外层结果中。Sphinx输入已在本次准备中成功恢复到临时RAM，资源不足后已释放，没有留下新实验卷。

同任务三调度使用相同镜像和guest资源：原配置2 vCPU、4 GiB／guest。七guest配置容量不是实际host占用。逐组建新私有服务和cgroup，先建组再创建guest，不复用池中旧VMM；保留当前其他工作区资源。

## 元数据与复现入口：当前矩阵已完成

运行以下命令只读取元数据并保存计划，没有执行选项：

```bash
python3 -B scripts/prepare_python_schedule_matrix.py \
  --output ".artifacts/python-schedule-preparation-$(date -u +%Y%m%dT%H%M%SZ)"
```

本轮`.artifacts/python-schedule-preparation-20261108/plan.json`保存18个NOT_RUN条目、角色路径、分叉点、原始预期退出码和采集组合；`resources.json`保存资源时点，`tests.log`记录3项纯离线回归通过。测试使用临时元数据，不触碰服务或设备。

只有明确需要另跑一套相同矩阵时，再用下面的显式入口；它会新增18次，不是查看结果命令。输出目录必须是新的，资源不足或首次实际失败即停止，不自动重试：

```bash
python3 -B scripts/run_python_schedule_matrix.py \
  --output ".artifacts/python-schedule-matrix-$(date -u +%Y%m%dT%H%M%SZ)" \
  --sphinx-branch-policy early2-dual-output-v1 \
  --sphinx-find-exit-diagnostic --xarray-find-exit-diagnostic \
  --execute
```

上面的命令不会自动采用旧失败结果，也不会改变普通入口默认。当前18份已完成，无需部分继续。以后真实失败且已停止的批次，可用`--resume-from`复用成功槽位；重试该批失败组还须得到对应确认并显式传`--retry-failed`，不把这个参数当自动重试许可。已完成PASS批次不能作为失败批次继续。

明确诊断规则仅覆盖已确认的单条命令：Sphinx的两条原管道、early_2_inspect/004条件输出及trace_cpp_parser/005环境查询，Xarray原trace_index_state/002 SIGPIPE和新增001环境查询。实际退出与严格FAIL保留，最终任务验收不变；原命令、输出和补丁有其他变化时仍拒绝。
