# 沙箱内存采集：选入口、看指标、确认范围

日常观察沙箱在host上的内存变化，优先使用**guest轻量采样＋host总记账**；需要解释哪些文件缓存驻留时，再显式开启终点分层查询。两种路径都保留默认回收，不暂停、不预读、不清缓存、不强制驻留。

2026-11-08起，host内存主结果按[物理指标方案](HOST_PHYSICAL_METRICS.md)解释：current看整组物理内存记账，file看组内文件缓存，PSS/N看VMM的比例驻留量，mincore／cachestat看指定对象的缓存页。它们都有实际host内存依据；范围不完整不等于逻辑容量。guest Cached等继续采集并单独展示，不进入host物理节省计算。此次只调整方案说明和离线核对，不改采样器、默认值或历史结果。

截至2026-11-07，三种调度均已有显式分层查询的真实验证；[最新结果与分层成本](../../results/schedule-layer-completion-20261107/REPORT.md)列出本轮GRPO／TVCache及复用的BPO。普通入口默认保持，日常轻量路径与完整监控分开使用。

## 新版三调度重复结果

[Prettier七沙箱三调度各三次](../../results/prettier-schedule-layer-repeats-20261107/REPORT.md)已完成，含服务组current、PSS/N、扩展普通文件mincore、块设备cachestat及任务／工具计时的均值、中位数和波动范围；[总览图](../../results/prettier-schedule-layer-repeats-20261107/comparison.png)及逐次曲线单独保存，不与旧九次或单次方法验证混算。

主要计时／PSS／组内计数均3次有效；TVCache块设备完整量和GRPO准确loop驻留各缺1次，表和图注明2／3，不补零。各层范围仍按本文定义，较低的VMM PSS/N不等于较低的服务组current。 [两项缺失的离线原因与改进建议](../../results/schedule-layer-gaps-offline-20261107/REPORT.md)已说明：RAM没有原始缓存计数；loop仅因mtime变化被旧严格条件拒绝，原始窗口读数单列，不加入旧统计。

后续另获授权时，可用下面的专用命令新建完整9次批次；不加`--execute`只保存计划，不创建沙箱。该命令复用已验证单次入口，不修改普通默认：

```bash
python3 -B scripts/run_schedule_layer_repeats.py \
  --output ".artifacts/schedule-layer-repeats-$(date -u +%Y%m%dT%H%M%SZ)" \
  --execute
```

每次准备前严格超过58 GiB可用盘、64 GiB可用内存，运行中保持原20 GiB磁盘保护。失败保留原始结果并停止，不自动重跑、清理旧卷或使用代理。

## loop查询窗口字段：已完成一次真实正常路径验证

[实现与检查](../../results/schedule-loop-window-offline-20261107/REPORT.md)为显式`--cache-layers`组合增加`outer_loop.query_window`。它允许文件mtime变化，但仍检查准确后备的身份、大小、loop配置和挂载归属；原严格`status/resident_bytes`及分层PARTIAL不变。

前后元数据在`outer_loop.residency.metadata_before/metadata_after/metadata_changes`，来自原两次fstat，不重复查询。报告将窗口驻留与严格驻留分列，未知仍空、实测0仍0；两列是同一次mincore，不相加。旧9次没有该字段，不按新规则追补。

[单次七沙箱GRPO结果](../../results/schedule-loop-window-live-20261107/REPORT.md)已验证新字段真实产出：完整16 GiB范围内，两套值均320.695312 MiB，前后身份／大小／mtime不变。本次只验证正常路径，不为触发mtime变化加轮次。loop整段墙钟／调用线程CPU0.100062／0.094017秒，全部终点0.544865／0.403909秒；新增字段的额外CPU未分离，没有开关减速率。

## RAM设备独立查询：已完成一次真实正常路径验证

[离线拆分说明](../../results/schedule-ram-split-offline-20261107/REPORT.md)将固定RAM设备缓存索引量与原VMM映射／来源关系分开。现有显式四类cachestat命令再加`--split-ram-cache`才启用，默认不开；其他入口和方法不自动变动。

每个RAM设备额外计划一次cachestat，用预选的组内open FD前后核对身份、进程／cgroup与容量，不在失败后换来源或重试。新`objects[].fixed_ram_device.query`及`block_devices.fixed_ram_totals`不填原严格值；`source_relationships`单列OBSERVED／CHANGED／UNKNOWN。无RAM不是缓存0，而是NOT_APPLICABLE／null。

[单次TVCache真实结果](../../results/schedule-ram-split-live-20261107/REPORT.md)：两个设备独立查询2／2有效、合计987.191406 MiB，原来源均OBSERVED，没有触发映射变化；额外两次核心查询墙钟3.901毫秒，含准备4.586毫秒，完整终点0.710876秒。这些是单次直接查询窗口成本，没有整轮开关减速率。

新值只是组内仍持有设备的全局缓存观察，不证明每个guest还映射原地址、guest缓存物理归属或实际页记账组。两套顺序窗口和其他缓存层不相加，旧9次缺项不追补。

## 按问题选择，不把所有查询都开着

| 要回答的问题 | 已有入口 | 实际采集 |
| --- | --- | --- |
| 单任务工具多久、guest内存如何变化 | 普通 `platform-<任务>.json` 轻量配置 | 工具计时、guest meminfo，不自动增加host采集 |
| 一批运行在host上记了多少内存 | `run-host-file-monitor.sh`＋`*-host-current.json` | guest轻量曲线、host current及组成；不采PSS、不查终点mincore |
| Prettier七角色各VMM占多少、可枚举缓存有哪些 | `run_schedule_smoke.py` 显式分层选项 | 完整监控、周期和终点PSS、普通文件mincore、块设备cachestat、TMPFS和loop；不是前一行的轻量配置 |

第二行已验证Prettier #6604、Sphinx #7590、Xarray #6992完整单路径；不直接用于保存恢复或三调度。第三行限Prettier七角色：GRPO为189实际／189逻辑动作，BPO和TVCache为84实际／189逻辑动作。显式cachestat组合已验证GRPO的8个、BPO／TVCache各10个登记设备；GRPO没有恢复RAM后备，不强求该对象存在。

### 日常host总记账：不增加PSS和驻留查询

先只读检查本机已有输入：

```bash
bash scripts/use-platform.sh check \
  configs/platform-prettier6604-host-current.json --offline
```

需要实跑且源服务已经健康时，使用下面的已有入口；此命令**会创建沙箱**，不负责启动或替换源服务：

```bash
export ASB_AGENTENV_SOURCE_SERVICE=asb-source-rebuilt-20261018T160925Z-2726362
bash scripts/run-host-file-monitor.sh \
  configs/platform-prettier6604-host-current.json
```

对应8 GiB卷启动前须严格超过34 GiB可用盘，仍执行入口原资源检查和失败卷保留规则。Sphinx／Xarray替换为各自的`*-host-current.json`；配置条件见[轻量host选项](HOST_CURRENT_TASK_OPTION.md)。

### 终点缓存核查：普通文件mincore，块设备cachestat

以下为后续获准实跑时的完整BPO命令，不是离线检查。输出目录及其末级目录名都必须是新的，驱动会据末级名称建立RAM输入目录：

```bash
run_name="bpo-cachestat-$(date -u +%Y%m%dT%H%M%SZ)-$$"
python3 -B scripts/run_schedule_smoke.py \
  --output ".artifacts/schedule-cache-check/$run_name" \
  --schedule bpo --expanded-files --cache-layers \
  --block-cache-method cachestat --unused-network-pools
```

驱动要求本机固定源容器初始停止、身份及restart=no符合原条件；它会临时启动源容器，创建新私有服务并在结束时收尾。复用已验证本地输入、清空代理，不下载镜像。准备前可用盘须严格超过58 GiB、可用RAM超过64 GiB，BPO使用32 GiB卷，运行中保留20 GiB磁盘保护。

`--cache-layers`必须同时有`--expanded-files`；显式块设备方法依赖前者。不传方法仍用mincore；不传`--schedule`会按原行为各跑三种调度一次。不要误用这两个默认行为增加运行数量。完整参数与身份检查见[分层查询说明](SCHEDULE_MINCORE_COVERAGE.md)。

## 指标名称固定，统计范围随入口核对

| 报告中的量 | 来源、单位和意义 | 不能替代的量 |
| --- | --- | --- |
| 组内存记账量 `memory.current` | cgroup（Linux资源统计组）及其后代的当前物理内存分配记账，原始字节，图表MiB；不是虚拟配置容量，用于观察整组成本 | 不是逐物理页去重后的全host精确并集，也不一定只含任务 |
| 文件缓存记账量 `memory.stat:file` | 记在该组的实际文件缓存页容量，含tmpfs／shmem；不是文件长度 | 不是guest Cached，也不是这些文件在全host的全部驻留 |
| `anon`／`kernel`／`shmem` | 同组组成计数；shmem已包含在file中 | 不用current减file冒充anon，不再把shmem加到file |
| guest `Cached` | guest的`/proc/meminfo`逻辑文件缓存计数，含与Shmem重叠的部分 | 不等于host file；数据也可能在VMM的匿名RAM中 |
| 普通文件mincore驻留量 `resident_bytes` | 对身份确认的文件用`mmap(PROT_NONE)+mincore`查询；按设备号:inode去重 | 不保证无路径旧缓存或其他缓存层都已覆盖 |
| 块设备cachestat缓存索引量 `cache_index_bytes` | 完整设备范围内实际缓存条目对应的`nr_cache×页大小`，不是设备容量；按活跃rdev去重，保留并核查全部别名 | 包括尚未uptodate（内容尚未准备好）的页，不改名为mincore驻留量；nr_evicted不算当前占用 |
| VMM PSS与PSS/N | `/proc/PID/smaps_rollup`，内核按映射次数分摊共享页；终点七VMM顺序读取之和及其除7 | 不含全部服务／块设备提供进程，不是整组物理占用或各实例峰值之和 |

普通文件与块设备的计数代表目标对象的全局缓存观测，不受本次cgroup记账范围限制。current、file、PSS、mincore、cachestat以及不同缓存层之间均不相加成物理总量。

必须随数据保存实际cgroup路径并区分两种范围：

- **常规host-current入口：**统计组在输入准备前建立，包含准备、控制程序、私有服务及全部新guest。基线、全流程峰值和执行阶段峰值分开看，不能把准备缓存当每guest独占成本。
- **上述三调度入口：**过程`host/memory-samples.tsv`及终点`schedule-smoke-metrics.json`里的计数取私有服务cgroup，覆盖服务及其VMM等后代，不包含组外外层控制程序和RAM输入准备。不能和前一种范围的current绝对值直接比较。

新建服务时就在目标组内；不将池中旧VMM移组后当作新分配。共享页可能仍记在其他组，另有部分内核分配未记账；current不能自动覆盖它们。mincore小计大于file只能提示范围／时差问题，实际组外容量需要页记账归属证据，不能拿净差加回current。

## 频率和时间口径不能混用

普通guest／host计数目标周期0.1秒；实际查询和调度会延长间隔，峰值只表示观测最大值。三调度周期VMM PSS为1秒，终点七VMM PSS另查一次；mincore和分层查询仅终点一次，没有周期驻留扫描。

轻量guest内存曲线用`MemTotal−MemAvailable`，Cached单列；旧完整监控的“非空闲内存”等曲线按各自报告定义，不改标成同一指标。三调度过程host表包含current／anon／file／kernel，终点counters另有shmem、file_dirty、file_writeback；不要把终点字段当作已有同频曲线。

- **工具累计：**各个实际工具动作耗时之和，并发部分重叠，不是墙钟时间；不把BPO／TVCache的84实际动作折算成189实际步。
- **动作及下载窗口：**第一段执行开始到最后一段返回，包含相关控制与下载，截止在终点查询前。
- **控制程序整轮：**原任务控制程序的创建、准备、保存恢复、执行、检查和清理；不含更外层服务分卷与RAM输入准备。
- **查询成本：**单列墙钟和调用线程CPU；不是相对关闭采样的任务减速率，也不能代表完整工作进程CPU。

## BPO成本示例，其他调度见最新结果

以下全部来自[2026-11-07 BPO报告](../../results/schedule-block-cachestat-20261107/REPORT.md)的保存数据，不是本次新增采集：

| 查询范围 | 墙钟秒 | 调用线程CPU秒 |
| --- | ---: | ---: |
| 七VMM终点PSS | 0.133362 | 0.115772 |
| 原19文件P1 | 0.065777 | 0.065727 |
| 普通文件扩展（含清单／去重） | 0.101172 | 0.097987 |
| 块设备cachestat核心／含前后身份检查 | 0.003343／0.030948 | 0.003343／0.030130 |
| 块设备整层（再含登记） | 0.094861 | 0.060112 |
| 块设备、TMPFS与准确loop三层共计 | 0.296216 | 0.204544 |
| 全部终点查询 | 0.771335 | 0.609310 |

这些行有包含关系，不逐行相加；全部终点还包含绑定和清单核对等步骤。TMPFS工作进程查询函数另有7.351毫秒CPU，未包含其解释器启动、序列化和退出CPU，完整工作进程CPU仍未采到。单次成本不代表三种调度或其他任务的稳定性能；采样开关影响继续按[各任务开销报告](LIGHT_MONITOR_COST_SUMMARY.md)引用。

## 从结果文件检查覆盖，不只看PASS

三调度原始数据以本次输出`result.json → runs[].raw`定位；主要字段位于该目录的`schedule-smoke-metrics.json`：

1. `p1`是旧19文件结果，不是全部文件。`expanded_files`覆盖活跃lower／upper、私有服务env/run/tmp及进程FD/maps；普通文件去重后原19结果复用，不重复计入。
2. `expanded_files.stable_regular_resident_bytes`是原严格小计。`query_window_regular`允许登记后变化但各自查询稳定的文件进入新小计；原PARTIAL及变化记录保持，不说成同一时刻快照。
3. `cache_layers.block_devices.method_totals.cachestat`看`status`、`observed_objects`和`complete_bytes`；逐对象`queries.cachestat`有范围、原始计数及前／核心／后检查成本。本次10／10、1112.601562 MiB只表示登记设备完整。
4. 未请求块设备mincore为`NOT_REQUESTED/null`，不是0也不是采集失败。兼容报告中旧“ublk块设备mincore”行仍可能出现；本模式有效值以独立cachestat方法表为准，不把未请求字段当缺失容量。
5. TMPFS整后备和准确loop各自看状态、身份及读数；它们不混进普通磁盘文件小计。任何实际失败保留UNKNOWN／null，完整量为null；不从另一别名或方法补值。

本次普通磁盘文件255个各自查询窗口均有效，但8个upper登记后变化，原普通范围仍PARTIAL。已释放、已删除且无持有者的对象、无路径旧缓存及窗口外变化仍可能漏掉；guest Cached的实际host物理归属和跨层物理并集也未由这些接口证明。任务／官方检查是否保持与缓存覆盖状态分别报告。

三调度分层方法可用性已经验证。无路径旧缓存等覆盖边界保持，不为此追加相近验证；后续正式性能或密度比较需另定范围。

## 旧三调度表保留，最新分层结果另列（2026-11-07）

[统一指标表及原始来源](../../results/schedule-metrics-inventory-20261107/REPORT.md)将同批三调度的服务组current、终点PSS/N、原19文件及扩展普通文件驻留、任务窗口和实际工具累计分列；后续单次BPO的分层查询另表。旧三组未采集的块设备cachestat保留null，不用后续BPO补值；旧普通文件PARTIAL、变化读数及未解析标记均保留。

旧表由纯离线入口`report_schedule_inventory.py`生成，5项回归通过，数值和未采集标记保持。之后GRPO／TVCache各一次补测已完成，见[最新分层报告](../../results/schedule-layer-completion-20261107/REPORT.md)：三调度各文件查询窗口和设备层有效，TVCache的变化upper及原PARTIAL单列；两次新增与一次复用不作为正式重复。单次逐对象报告用`report_schedule_cache_layers.py`离线另存，该报告器现支持GRPO／BPO／TVCache，9项回归通过；不触碰真实对象或改变运行时采集。
