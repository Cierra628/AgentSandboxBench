# 用current观察沙箱在host上的总内存记账

需要判断一批沙箱运行时在host上占多少内存，先看`memory.current`曲线；需要解释其中共享文件缓存的变化，再看`file`和指定文件的`mincore`驻留。2026-10-28已将这个顺序接入通用host报告，没有增加运行时采样操作。

## 主指标和统计范围

`memory.current`是实验cgroup（Linux资源统计组）及其后代当前的内存记账量，单位为字节。它包含组内匿名内存、文件缓存和已记账的内核内存，比只看file更适合作为部署密度研究的基础。

统计组在输入准备之前建立，覆盖**输入准备、控制程序、私有服务及全部新建沙箱**。新服务和VMM从创建起就在组内，关闭预启动池，保留内存及I/O记账；不把已有沙箱移动进来。主图因此表示整批运行所需的组内记账，不是guest任务进程独占量。沙箱数增加时，这些公共成本只计一份。

当前范围仍有两个缺口：已记在组外的共享页，以及未纳入内存组记账的分配。例如旧归档曾在独立查询时确认1746.109375 MiB记在交互会话组；ublk命令缓冲也有未请求内核记账的源码证据。这些历史结果不能作为新任务的固定补数。`current`不能保证完整物理覆盖，也不是整个沙箱物理占用的严格下界，因为统计组还包含控制与准备程序。

组成指标继续同次读取：

- `anon`：匿名映射，通常包括冷启动VMM的guest RAM。即使不能跨guest共享，也必须计入部署密度所需总内存。
- `file`：文件数据缓存，包含tmpfs和共享内存。它不是guest内部`Cached`在host上的对应页集合。
- `kernel`：已记账的内核内存。已有缺失字段保持未知。
- `shmem`：已包含在file内，不再相加。

这些分项已经计入current，不能再加到current上。计数是顺序读取，统计也可能延迟；`current−anon−file−kernel`不是未记账内存的测量值。`mincore`与PSS也不作为加项。当前采样没有读取swap，hugetlb大页覆盖未核实；如后续研究实际可部署数量，再在该实验中记录交换、内存压力和OOM（内存不足终止），并同时检查任务时延及成功情况。

定义依据为实际内核源码中的`Documentation/admin-guide/cgroup-v2.rst`：1300～1305行的current、1542～1556行的组成、1961～1975行的记账归属；本机路径`/home/yxli/linux-6.19.9-ioctl/`。[Linux官方说明](https://docs.kernel.org/admin-guide/cgroup-v2.html)给出同一接口定义。

## 同时报告绝对占用和相对基线的变化

| 报告项 | 计算与用途 |
| --- | --- |
| 全流程绝对曲线和观测峰值 | 从准备到采样结束的current；用于了解整批运行成本，峰值受采样间隔限制 |
| 创建guest前基线C0 | 复用服务就绪、尚无guest时保存的同步计数；包含已准备输入和私有服务 |
| 服务就绪后的观测峰值Cmax | 只在创建前标记之后的周期点中取最高值，保留同一采样行的各分项和时间 |
| 峰值增量 | Cmax−C0；包含随后控制程序、服务和全部沙箱的变化，不叫“任务进程内存” |
| 最后读数及其增量 | 明确是采样最后一点；可能已删除guest，不能代替任务结束前端点 |
| 时间加权平均值 | 前一点持续至后一点，按真实间隔加权，只计算首点至末点；是含实际缺口的近似平均 |
| 成本和时间缺口 | 保留原读取总用时、采样线程CPU时间、采样点数和最大间隔；不把统计间隔当成查询成本 |

旧记录没有同步基线时，基线和增量输出`null/NOT_MEASURED`。相邻点仅保留原时间，不插值伪造C0；负增量照实保留。同步计数中的各文件也不是一个原子快照。

输出图分为两种：`overview.png/svg`先展示全流程current，再展示anon/file/kernel；若原来采了PSS，才增加映射内存面板。`after-service-ready.png/svg`单独放大创建前标记之后的current增量，避免几GiB准备成本淹没运行变化；没有基线就展示绝对量并注明缺失。窗口仍含后续控制和清理，不改称纯工具执行窗口。

## 不增加采样负担，复用现有入口

运行时仍每0.1秒读已有`memory.current`和`memory.stat`，默认回收保持开启。新方案没有增加PSS、逐页归属探针、内容读取或常驻查询。已有普通host入口是否采PSS保持原行为；显式共享文件模式继续关闭PSS。`--current-only`是**离线报告选项**，不会替用户关闭运行时PSS或驻留查询。

现有`run-host-file-monitor.sh`和`*-host-file.json`继续可用。通用自动报告改以current为主，原`file_peak_bytes`、`current_peak_bytes`、文件清单、PSS选项和报告路径保留，新增`current`统计对象和`REPORT.md`。平台上层摘要同步提供current峰值、创建前基线和峰值增量。显式副本实验仍按原指定文件驻留计算共享收益，不被改成current收益。

只需总记账图时，已有采样可以不依赖文件驻留清单离线生成：

```bash
python3 -B scripts/report_host_file.py \
  .artifacts/<采样所在目录>/host-file \
  --current-only \
  --creation .artifacts/storage-isolated-service/<对应运行>/result.json \
  --output .artifacts/<新的报告目录>
```

必须使用同一轮创建记录和采样；报告检查统计组一致、无旧VMM和预启动池、采样覆盖创建前标记。已有数据若带PSS，报告只读取保存结果，不重新查询进程。该模式把mincore标为未测，不输出零驻留，也不能交给旧文件节省外推入口。原`--residency`报告保持文件清单完整性检查，原双摘要CLI仍只外推file峰值，不因主图改为current而改变含义。旧图和旧报告不覆盖。

## 离线复算结果

本次只读取保存的计数与记录，未启动服务、沙箱或任务，也未访问任务文件内容。28项相关离线测试通过，旧Sphinx报告的峰值、基线、清单等15项字段复核保持；原PSS开启与关闭数据、新CLI及文件外推拒绝条件均已检查。测试中两处旧控制器模拟缺少后来新增的全局变量，已补齐夹具默认值；运行控制器未修改，首次失败日志保留。

| 保存数据 | 点数 | 创建前current / MiB | 全流程观测峰值 / MiB | 服务就绪后峰值增量 / MiB |
| --- | ---: | ---: | ---: | ---: |
| 既有Sphinx单任务 | 1271 | 17559.047 | 18139.586 | 580.539 |
| 既有Prettier七沙箱GRPO，PSS关闭 | 1355 | 25322.113 | 29778.055 | 4455.941 |

这些是不同任务和准备条件的示例，不作彼此的收益对照。Sphinx采样线程CPU共1.316秒，计数读取墙钟累计0.393秒；GRPO分别0.455／0.370秒，均来自旧采集记录，不是这次修改新测得的开销。Sphinx的线程CPU还包含原PSS扫描，不把它解释成纯current读取成本。任务减速仍引用[采样开销报告](LIGHT_MONITOR_COST_SUMMARY.md)，本次不推导新的减速率。

- [GRPO总记账曲线](../../results/host-memory-current-20261028/grpo-no-pss/overview.png) · [创建前基线之后的放大图](../../results/host-memory-current-20261028/grpo-no-pss/after-service-ready.png)
- [Sphinx总记账曲线](../../results/host-memory-current-20261028/sphinx/overview.png) · [创建前基线之后的放大图](../../results/host-memory-current-20261028/sphinx/after-service-ready.png)
- 原始数据绑定、复算脚本和检查日志：`.artifacts/host-current-primary-20261028/`；脱敏摘录：[本次证据](evidence/host-current-primary-20261028.json)。

## 后续CSV轻量适配已有实跑数据

[CSV验证](HOST_CSV_CURRENT_VALIDATION.md)已取得3次严格任务重放、17个guest点和969个host点，无PSS／mincore。新入口的上传路径及检查器已修补；原外层FAIL和未执行的尾部检查保留，成功任务没有重跑。该短任务的固定采样控制成本大于工具累计，不用它代替大型代码任务的开销或密度结论。

## 后续Prettier #14400完整验证已通过

[新增代码任务结果](HOST_PRETTIER14400_LIGHT.md)完成原26步、三种独立输入和官方4条断言，97个guest点／1008个host点，PSS及mincore关闭。创建前current14632.219 MiB，峰值15445.199 MiB，峰值增量812.980 MiB；自动报告、实际最终卷检查及清理全部执行通过。图已保存到`results/host-current-prettier14400-20261028/`。这是一轮有效新采集，不与旧准备状态或采集版本混作性能对照。

## 已有四项代码任务已统一整理

[四任务清单](HOST_CURRENT_TASK_INVENTORY.md)把#6604、Sphinx、Xarray和#14400的实际基线、峰值、增量及开关放在同一表中，七沙箱GRPO另列参考。旧#6604没有精确基线，增量保持null；其他三项分别580.539／1352.152／812.980 MiB。旧三任务带PSS／mincore，新#14400关闭；曲线可以复用，性能不混作新版同条件对照。可直接打开的表、图和离线bash入口均见清单。

## 后续任务如何保持可比较

推广新任务时固定相同的host／guest采样频率、CPU与guest RAM、后端和默认回收，记录工具时延、guest内存／Cached、host current及组成，重复运行并报告各轮及均值和范围。host组必须在准备前建立；保留准备成本和创建前基线，不把不同准备状态的数据混作任务差异。

增加并发时优先在同一组、同服务内观察N=1、2、4、7的相同任务阶段；准备完成并同步自有文件系统后才取共同基线。保留current绝对量、基线增量和相邻并发级别的增量，不用N乘整组单沙箱总量当作“无共享”实测，也不从指定文件节省比例直接推算多部署多少沙箱。真实密度还取决于峰值、回收、任务时延和失败情况。

本阶段完成条件是：总指标、基线、组成及成本定义正确，报告支持已有PSS开启／关闭数据且旧字段和旧文件比较含义保留。后续已完成#14400一轮完整验证，见上节。四任务离线清单现已完成。普通三任务的[显式无PSS／mincore host选项](HOST_CURRENT_TASK_OPTION.md)现已完成离线接入，保留原入口和检查；#6604公共路径一轮[已完成](HOST_CURRENT_PRETTIER6604.md)，精确基线14639.289 MiB、峰值增量925.445 MiB；Sphinx／Xarray随后也各[完成一轮](HOST_CURRENT_PYTHON_TASKS.md)，增量580.605／1273.297 MiB。三个标准任务的真实采集范围现已齐全；随后[#6604两对](HOST_CURRENT_SAMPLING_TWO_PAIRS.md)、[Sphinx一对](HOST_CURRENT_SPHINX_SAMPLING_PAIR.md)和[Xarray一对](HOST_CURRENT_XARRAY_SAMPLING_PAIR.md)也均完成当前采样开关验证，耗时影响按各自窗口及实际波动分开报告。
