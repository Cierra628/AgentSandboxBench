# 每个任务只记录工具时延、内存和page cache（2026-10-19）

2026-11-07的[当前入口与指标简明说明](HOST_METRICS_GUIDE.md)集中说明guest-only、host-current和七角色分层模式的区别。host轻量入口覆盖整个实验组，三调度入口取私有服务组；两者current绝对值不直接比较。普通文件mincore与块设备cachestat分名报告，下面历史记录按原配置保留。

当前目标按用户要求收敛为三项，不再要求逐页确认host物理归属。普通单路径任务默认使用轻量模式；此前精细物理测量、缓存控制和恢复实验的代码与结果保留，继续使用各自明确配置。

2026-11-06新增：[8种任务新版采集与计时汇总](../../results/new-sampling-summary-20261106/REPORT.md)列出普通计数0.1秒、显式VMM PSS1秒及终点PSS／初始下层P1的实际结果，分开任务、采集管理、原检查与删除。新版外部单路径是轻量组合，Prettier七角色仍是完整监控，两者不能混算采样开销。下面guest-only及各日期说明仍按原运行范围保留。

2026-11-07新增：[三调度mincore扩展](SCHEDULE_MINCORE_COVERAGE.md)为Prettier完整监控显式补查全部活跃lower／upper、私有服务保留文件、打开文件和文件映射，原19文件P1保持。[三组真实结果](../../results/schedule-mincore-coverage-20261107/REPORT.md)将稳定小计、变化文件、未知／排除项和查询成本分列；不自动扩展其他任务或把它当全部host缓存，普通轻量入口默认不变。

当前采样开销统一见[工具、启动、直接控制与整轮的说明](LIGHT_MONITOR_COST_SUMMARY.md)：集中列出新版单沙箱／七沙箱三对、host查询及启动诊断的成本和适用范围。下方各日期记录按原版本保留。

## 当前新版采集：普通计数0.1秒，周期PSS默认1秒、可显式选2秒

guest逻辑曲线、host实验组记账、VMM进程PSS和指定文件驻留是不同范围的量。PSS是将进程共享页按共享比例分摊后的驻留内存，不代表整组占用；下层文件驻留也不是guest逻辑缓存的完整host对应量，各量不相加。

| 采集项 | 默认或次数 | 2秒选项的影响 |
| --- | --- | --- |
| guest meminfo、host组current／file等计数、已启用的VMM普通计数 | 0.1秒 | 保持 |
| 已启用的周期VMM PSS | 默认1秒 | 显式传`--vmm-pss-interval-seconds 2`才变为2秒 |
| 每个guest完成点的VMM PSS、初始下层文件mincore P1及查询成本 | 各一次 | 保持独立查询，不受周期参数控制 |

2秒参数只接入外部任务的新采集组合。C4、Django、Cython还需`--endpoint-metrics`，Matplotlib和文本编辑原本启用该组合；Sphinx／Xarray的Python入口、Prettier及旧采集入口不能直接套用。**实际2秒性能证据目前只来自C4双沙箱**，不作为其他任务的开销保证。完整用法及收据位置见[PSS间隔说明](VMM_PSS_INTERVAL.md)。普通guest-only配置不因本节新增host或PSS采集。

[C4双沙箱1秒／2秒实际对照](../../results/c4-pss-interval-pair-20261106/REPORT.md)已完成4guest、144原动作。2秒配置正确生效，全部周期采集CPU少30.29%；工具累计少2.91%、任务窗口少4.71%，但整轮因删除请求等待增加20.83%。这是顺序各一次的实际变化，不是稳定加速率；成本与时间范围见[开销统一说明](LIGHT_MONITOR_COST_SUMMARY.md)。

两种周期都可能漏掉短峰值。2秒保留过程曲线并降低本轮成本；需要较密的PSS过程点时保留1秒。完成点的独立查询仍提供该时刻的值，不替代过程峰值。不能拿两次不同运行的峰值差计算降频误差；guest Cached、host file及其波动也不能直接归因于PSS间隔。旧报告按当时采集配置引用，不改标为2秒结果。

## 2026-10-25：七沙箱收尾与host查询优化已实跑

[多沙箱成本优化](MULTI_HOST_OVERHEAD.md)只改变轻量模式最终收尾和mincore结果统计：一组七沙箱完整GRPO189动作、2166点通过，最终收尾0.514秒，原两次19文件查询合计0.144秒。采样频率、默认回收、原调度和查询含义保持。七个采样器合计1.544486 CPU秒；本轮没有关闭测量组，不能据此给出整体并发减速率。下方普通单沙箱开关结果仍按原范围使用。

## 2026-10-25：轻量启停调用已合并，三对实跑完成

[耗时分解与优化](LIGHT_MONITOR_OVERHEAD.md)已完成：Prettier旧三对的直接采样控制为0.575～0.795秒，整轮几秒差值主要在采样启动前的冷启动。新版一次上传两个文件，由采集命令初始化目录和阶段，一次收尾命令完成末尾标记、停止和回收确认；阻塞调用10→5。0.1秒、采样字段、首样本就绪和进程回收保持，旧完整模式及已保存结果保留。离线阶段26项相关检查通过；下文旧开关对照数字仍按原版本引用。

随后[Prettier三对开关实跑](LIGHT_PRETTIER_OPTIMIZED_CONTROLS.md)完成六次174动作、601点，原结果和清理通过。直接控制中位数0.422秒，旧三次0.724秒；本轮工具耗时配对变化中位数+2.05%（−5.64%～+4.46%），整轮+5.87%（−10.39%～+7.30%）。采样器约占一个核心的0.67%～0.86%，峰值RSS16.26～16.75 MiB。当前保留该方案作为普通单沙箱默认，不追加相近重复；host和多沙箱成本仍按各自范围报告。

## 新增：Prettier完整GRPO／BPO的当前轻量实跑

2026-10-22按新显式配置完成GRPO、BPO各一次，guest三项保持本页定义，另启用host文件缓存记账和全组PSS；[完整结果、曲线和附加检查状态](LIGHT_ROLLOUT_PAIR.md)单列。旧多路径配置仍默认完整采样。下文“不启动host采集器”描述原guest-only模式，不包含这两个显式host配置；Sphinx／Xarray当前轻量完整多路径尚未采集。

## 三项指标怎么读

| 指标 | 来源和含义 |
| --- | --- |
| 每次工具调用耗时 | 复用guest执行器的`steps.tsv`中`duration_ns`，保留每次退出码；统计命令执行经过的时间，不包括模型推理、host API及下载结果的时间 |
| 内存变化 | guest的`MemTotal−MemAvailable`，表示内核估算的已用内存，单位换算为MiB |
| page cache变化 | guest的`Cached`原值，包含与`Shmem`重叠的共享内存缓存；不再加一遍`Shmem` |

内存和page cache都来自guest，不是host物理占用，不能相加作为总内存。`MemAvailable`包含预计可回收的缓存，因此内存曲线也不是`MemTotal−MemFree`的旧“非空闲内存”曲线。已有报告的原口径不改写。

周期采集只读一次`/proc/meminfo`，当前间隔0.1秒，启动和停止时也取样。仍使用原执行器边界记录和任务结果检查。曲线覆盖采样就绪至任务检查结束、删除沙箱之前，包含期间等待；峰值是采样最高值，采样空缺不连接。很短的工具调用只保证有时延记录，不保证期间存在周期内存点。

2026-10-27的[GRPO逐路径核对](GRPO_PATH_COST_BREAKDOWN.md)明确了边界记录的成本：当前开启组每七guest除周期采样外，仍有448次边界meminfo和448次边界vmstat读取。此前“周期采集只读meminfo”指后台Python进程，不包括这些执行脚本操作；采样器CPU收据也只覆盖后台进程自身。[可选跳过边界vmstat](LIGHT_BOUNDARY_VMSTAT_IMPLEMENTATION.md)已离线实现、默认关闭，[候选单对真实结果及报告恢复](LIGHT_BOUNDARY_VMSTAT_PAIR.md)已取得1976周期点与完整原任务记录，创建后窗口+8.16%；随后[两组都采样的正反顺序两对](LIGHT_BOUNDARY_VMSTAT_TWO_PAIRS.md)累计7425点，动作及下载窗口分别−13.54%／−10.27%、创建后窗口−10.48%／−4.43%；原整轮包含尚未解释的创建差异，分开报告。旧数据和本页三项指标不变。

当前内存曲线横轴使用不随系统校时跳变的单调计时，原系统时间戳仍保留在数据表中。工具时延也沿用原执行器的单调计时，不因系统时间调整而增加或减少。

轻量模式不读取CPU、块设备I/O或host页映射，不启动服务cgroup及VMM内存采集器，不要求定制guest内核，不新增暂停、预读、锁页或缓存回收。一个guest采集进程复用现有启动、停止和回收流程；上传两个现有Python文件，不增加guest依赖。

这里的“不新增缓存回收”指不启动本项目额外的缓存控制器；AgentENV默认DAMON后台回收仍按原启动配置开启。不同运行的Cached可能因此呈现不同的升降过程。已离线确认Prettier第一轮0.1秒数据抽成每秒一个点仍有大幅下降，而第二轮0.1秒数据近似上升，见[原始读数、配置与对比图](LIGHT_MONITOR_CONTROLS.md)。

随后[一轮只读DAMON补充观测](PRETTIER_DAMON_OBSERVATION.md)取得219组内存/回收读数，实际状态始终开启，累计成功回收247.559 MiB，四段明显Cached下降与回收计数增长对应。该次使用`platform-prettier6604-damon-observe.json`显式启用附加采集，不改变普通轻量模式或回收策略，也不混入18次性能对照。实际内核不提供`skip_anon`，不能仅据启动字符串断言只回收文件缓存。

新增[Prettier一对默认开启/任务期间关闭对照](PRETTIER_DAMON_PAIR.md)已验证关闭方案可用。`platform-prettier6604-damon-disabled.json`显式设置`damon_observe: true`、`damon_disable: true`，在任务准备与采样之前停止该guest的DAMON，采集结束后恢复原启用状态。关闭窗口回收增量为0，最大相邻Cached下降0.074 MiB，对照默认组为75.695 MiB。两个专用配置均保留0.1秒内存和回收计数读取，普通三任务默认条件不变；关闭DAMON不禁止其他正常回收或文件变化。

2026-10-19后续按用户要求将默认间隔改为0.1秒，并新增关闭全部采样的对照入口。下方已完成的112点仍是此前1秒版本，不改写为0.1秒结果；0.1秒版本已完成三任务各3对开关对照，共18次、594个原动作和3214个周期点；工具耗时和控制程序成本分开报告，详见[0.1秒采样对照](LIGHT_MONITOR_CONTROLS.md)。

## AgentENV公开资料提供了什么依据

[AgentENV官方文章](https://kvcache.ai/blog/agentenv-open-sourced/)介绍了host缓存共享、guest缓存回收以及节点总体资源使用，没有公布每个guest的page cache测量步骤，不能据此认定它采用了逐页标记或物理归属统计。

本机固定源码`AgentENV`的版本为`2f48c9c78ec58e4d73c49aa07cb81d984a4a4184`。`src/observability/host.rs`的`read_memory_bytes`直接读取`MemTotal`和`MemAvailable`；有适用的cgroup内存限制时，节点指标会转用相应限制与用量。[公开代码](https://github.com/kvcache-ai/AgentENV/blob/main/src/observability/host.rs)可查阅相应实现。

本次参考的是“直接读系统计数器”的实现方式，将采样放在guest内部，另记录`Cached`。这两条guest曲线是本项目选定的指标，不是官方文章已公布的测量方法。

## 使用三个普通任务配置

原入口不变；普通单路径、无保存恢复、无物理分析的任务默认启用`light_monitor`。例如：

```bash
# 只检查输入和执行参数，不启动沙箱。
bash scripts/use-platform.sh check configs/platform-sphinx7590.json --offline

# 后续获得实跑授权时执行；会创建私有实验资源。
bash scripts/use-platform.sh run configs/platform-sphinx7590.json

# 对上一步返回的运行目录生成新报告，输出目录必须不存在。
bash scripts/use-platform.sh report .artifacts/platform-tasks/<运行目录> \
  --output .artifacts/sphinx-light-report
```

另外两个配置为`configs/platform-prettier6604.json`、`configs/platform-xarray6992.json`。要显式保留旧100毫秒CPU/I/O及VMM采样，可在普通任务配置中设置`"light_monitor": false`。`*-live-cache.json`、缓存控制、物理分析、分支和保存恢复配置仍沿用原方案，不自动改成轻量模式。

主要输出为`overview.svg`/`overview.png`三幅图、`tools.tsv`逐次时延、`memory.tsv`内存与缓存读数，以及`summary.json`摘要。报告通过只表示指标文件读取一致；任务原失败、诊断模式和非零退出码另行保留，不改写为任务成功。

## 已有日志可以直接生成简明报告

无需重新采样、扫描物理页或启动沙箱：

```bash
python3 -B scripts/report_task_overview.py \
  .artifacts/storage-isolated-service/<已有运行目录>/python-backbone \
  .artifacts/task-overview-new
```

Prettier的原始数据子目录改为`prettier6604`。输入必须包含现有任务的`result.json`、`segments.tsv`、逐段`steps.tsv`及guest采样日志。

## 三任务各一轮轻量实验已完成

本次按授权只新增Prettier完整29步和Xarray完整35步，两次均一次完成；此前Sphinx的35步直接复用，没有增加任务或对照轮数。三个任务都使用2 CPU/4096 MiB、8 GiB私有卷及1秒guest内存采样，只报告约定的三项指标。

| 任务 | 工具调用次数 | 工具耗时合计/秒 | guest已用内存采样峰值/MiB | Cached采样峰值/MiB | 内存样本数 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Sphinx（复用） | 35 | 10.320156 | 308.562500 | 119.675781 | 22 |
| Prettier | 29 | 11.749002 | 561.691406 | 250.546875 | 21 |
| Xarray | 35 | 35.766090 | 730.605469 | 313.941406 | 69 |

三任务合计99次原工具调用、112个周期内存样本。每个任务只运行一次，表格用于说明本轮各任务的实际读数，不是性能排名或采样开销对照。内存曲线包含监控就绪后、删除guest前的准备和选定测试；三任务观测时长分别约20.25、19.92、67.83秒，峰值不是只在工具执行区间内取值。

原动作、退出码、关键输出、补丁和独立结果检查通过。Prettier原选定测试仍0通过/2失败；Xarray仍945通过/12失败/0跳过，原SIGPIPE诊断策略和严格重放FAIL保持。Sphinx原24通过/1失败不变。实验流程和采集通过，不改写原任务失败。

直接查看当前结果图，文件已确认`yyxie`可读：

- Sphinx：[PNG](evidence/sphinx-light-20261019.png)、[SVG](evidence/sphinx-light-20261019.svg)。
- Prettier：[PNG](evidence/prettier-light-20261019.png)、[SVG](evidence/prettier-light-20261019.svg)。
- Xarray：[PNG](evidence/xarray-light-20261019.png)、[SVG](evidence/xarray-light-20261019.svg)。

### 修正Prettier系统时间跳变造成的绘图空白

Prettier相邻两个点的系统时间相差81.193862秒，而单调计时只相差1.000752秒。全部21点的系统时间跨度为100.117661秒，实际单调计时跨度为19.924557秒，原图因此误显约80.19秒空白；采样没有在这段时间中断。

本次只修改轻量报告的时间横轴，改用各采集会话内的单调计时；多会话以host记录的会话开始时间安排位置，不声称消除了启动准备的对齐误差。真实的单调计时缺口仍断线，不用插值补点。默认旧报告仍按原系统时间处理，不改变其历史口径。原始时间戳、工具时延、内存及缓存读数均未改动，任务未重跑。

已补80秒系统校时的最小回归检查，3项轻量检查和13项原运行监控检查通过。三份当前报告离线重建后逐行核对，显示时长与原单调计时一致；Prettier、Xarray最大采样间隔分别为1.009518秒和1.002877秒，已查看实际图。工具执行器本来就使用单调计时，原时延无需修正。

### 当前报告和资源状态

批次目录为`.artifacts/light-two-tasks-20261019T075750Z-3091760/`，`runs.json`记录三任务原始数据、初始报告与当前重建报告的对应关系。当前报告位于该目录的`reports/sphinx7590/`、`reports/prettier6604/`、`reports/xarray6992/`，含图、`tools.tsv`、`memory.tsv`和摘要。此前报告保留；当前图表以这里的单调计时版本为准。

Prettier实际运行在`.artifacts/platform-tasks/20261019T075811Z-3092779/`，原始任务数据位于`.artifacts/storage-isolated-service/20261019T075930Z-3093519/prettier6604/`。Xarray实际运行在`.artifacts/platform-tasks/20261019T080004Z-3095009/`，原始数据位于`.artifacts/storage-isolated-service/20261019T080145Z-3095961/python-backbone/`。批次`verification.json`、执行/重建/核对脚本和测试日志保留完整来源及核对结果。

两轮都未开启CPU/I/O、VMM或host物理页采集，没有新增暂停、预读、锁页或主动缓存回收。两个guest采集进程均已退出并回收，任务沙箱、私有卷、loop和RAM准备环境已清理，保留源服务已重新停止。两次运行磁盘最低可用分别96.923和96.877 GiB。

本阶段三任务覆盖已齐，下一步建议先检查三任务图表；当前无需追加实验。若要研究采样开销或改变采样频率，再单独确定最小对照范围。

## 首次Sphinx轻量实跑已完成

用户授权的一轮Sphinx #7590已通过统一入口完成35个原动作，只有一次实际任务执行，没有增加对照。使用普通`platform-sphinx7590.json`、2 CPU/4096 MiB及8 GiB私有卷，`light_monitor=true`；没有定制内核、物理页扫描或主动缓存控制。

| 指标 | 本轮结果 |
| --- | ---: |
| 工具调用次数 | 35 |
| 工具执行耗时合计 | 10.320155679秒 |
| guest周期内存样本 | 22 |
| guest已用内存采样峰值（MemTotal−MemAvailable） | 308.5625 MiB |
| guest Cached采样范围 | 32.19140625–119.67578125 MiB |
| 最大记录间隔 | 1.001099344秒 |

曲线覆盖约20.25秒的采样窗口，包含准备、任务和选定测试期间的活动，不把内存峰值限定为工具执行瞬间。采集程序自身CPU用时0.07076秒；这不是相对关闭采样的任务减速率，本轮不增加开关采样对照。

原动作、关键输出、最终补丁与源码及完整选定测试结果检查通过。原结果仍24通过、1失败、0跳过，失败为`tests/test_domain_cpp.py::test_expressions`；保留原管道诊断版本2，诊断PASS、严格重放FAIL，不把采集成功写成修复成功。

采集回执确认1秒模式，22行内存读数与报告一致，采集进程已退出并回收。没有CPU/I/O、VMM或服务cgroup采样文件，没有物理缓存扫描目录。任务沙箱、临时卷、loop和RAM准备环境已回收；保留的源服务在本轮前启动，结束后重新停止。运行期间磁盘最低可用98.905 GiB。

证据位置：

- 运行：`.artifacts/platform-tasks/20261019T073048Z-3070899/`。
- 原始任务与采集：`.artifacts/storage-isolated-service/20261019T073213Z-3071814/python-backbone/`。
- 三项报告：`.artifacts/platform-tasks/report-20261019T073048Z-3070899/`，其中`overview.svg`/`overview.png`已实际查看，`tools.tsv`和`memory.tsv`分别保留逐次时延及曲线数据。
- VSCode可直接查看的结果图：[PNG](evidence/sphinx-light-20261019.png)、[SVG](evidence/sphinx-light-20261019.svg)。它们是上述原图的副本，`yyxie`可读；原始实验目录仍保留root权限，不开放日志或运行数据。
- 启停记录和离线核对：`.artifacts/sphinx-light-20261019T073047Z-3070385/verification.json`；同目录保留执行、核对脚本和日志。

首次准备检查把正常的HTTP 204健康响应误判为未就绪，发生在任务启动前；修正临时脚本接受200/204后才执行本轮，初次记录保留在`.artifacts/sphinx-light-20261019T072919Z-3068644/`。离线清理检查又曾错误要求删除空挂载目录，已按卷移除、目录为空且未挂载及无loop的实际状态修正，没有重跑任务或删除保留目录。

该阶段只执行了Sphinx；随后用户授权补充的Prettier、Xarray结果见上节。

## 此前：本地实现与离线验证

- 轻量采样、三项报告和统一配置入口已实现。新增3项本地检查通过，覆盖1秒采样、无CPU/I/O/host采集、停止后进程回收、原失败和非零退出码保留、计时总和核对及曲线空缺；相关54项原检查通过。
- 复用Prettier 29步、Sphinx 35步、Xarray 35步日志，生成三份报告，共99次工具调用、1118个原有内存样本。报告在`.artifacts/light-task-metrics-20261019/<任务名>/`，来源绑定在`existing-reports.json`。这些是旧采集条件下的报告示例，不是新轻量模式的性能实验，也不用于三任务横向排名。
- 证据、测试日志和复现脚本在`.artifacts/light-task-metrics-20261019/`。已查看实际生成的三任务图片，标题、单位、工具数量、内存定义及缓存读数一致。
- 该实现阶段没有启动沙箱或服务，证据为本地采样检查、离线参数检查和旧日志报告；随后一轮真实Sphinx结果见上节。相对关闭采样的实跑开销未量化。

该阶段提出的一轮Sphinx验证现已按用户授权完成，未增加性能对照轮数。
