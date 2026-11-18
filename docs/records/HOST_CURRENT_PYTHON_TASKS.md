# Sphinx与Xarray的轻量host测量（2026-10-28）

**两个任务各完成一轮原35步，共70个任务动作；新轻量模式的采样、自动报告、精确基线、最终卷检查及清理全部通过。** 加上此前#6604，三个标准任务均已有关闭PSS／mincore的真实完整采集。原诊断规则和任务失败保留：Sphinx官方24通过／1失败，Xarray为945／12，二者严格重放状态仍为FAIL、原诊断状态为PASS。

## 先看各任务的运行增量

| 任务 | host运行增量 | host全流程及组成 | 工具及guest曲线 |
| --- | --- | --- | --- |
| Sphinx #7590 | [增量图](../../results/host-current-python-20261028/sphinx7590/host-increase.png) | [总记账图](../../results/host-current-python-20261028/sphinx7590/host.png) | [三指标图](../../results/host-current-python-20261028/sphinx7590/guest.png) |
| Xarray #6992 | [增量图](../../results/host-current-python-20261028/xarray6992/host-increase.png) | [总记账图](../../results/host-current-python-20261028/xarray6992/host.png) | [三指标图](../../results/host-current-python-20261028/xarray6992/guest.png) |

六张PNG均已实际查看，同目录保留SVG和各自35步用时表，全部位于`.artifacts`之外。每项只有一个guest，不把不同任务的曲线叠成一组共享收益对照。

| 指标 | Sphinx | Xarray |
| --- | ---: | ---: |
| 工具累计 | 10.840秒 | 35.319秒 |
| 冻结回放墙钟 | 14.436秒 | 38.505秒 |
| 任务控制程序整轮 | 28.519秒 | 71.456秒 |
| guest采样点 | 224 | 666 |
| guest非可用内存峰值（MemTotal−MemAvailable） | 291.691 MiB | 735.477 MiB |
| guest Cached峰值（含Shmem） | 115.082 MiB | 297.758 MiB |
| host采样点 | 1214 | 1839 |
| host创建前current基线 | 17579.406 MiB | 21885.977 MiB |
| 全流程及服务就绪后current观测峰值 | 18160.012 MiB | 23159.273 MiB |
| 服务就绪后峰值相对基线的增量 | 580.605 MiB | 1273.297 MiB |
| guest最大采样间隔 | 103.044毫秒 | 123.815毫秒 |
| host最大采样间隔 | 100.180毫秒 | 100.121毫秒 |

current是整个实验组及后代的总记账，基线含输入准备和私有服务，增量还包含后续控制、服务、检查及清理的变化。图尾清理后的负增量不是任务结束前端点。current不保证覆盖组外页和未记账分配；guest Cached与host file不同层，不能相减求共享量，也不把file、PSS或mincore加到current上。完整定义见[current方案](HOST_CURRENT_PRIMARY.md)。

工具累计只加原工具命令；回放和控制程序还含执行通道、下载、验证、采样管理和清理。guest曲线覆盖就绪后的准备、原任务及最终评测，不仅是命令窗口；host还覆盖前置镜像准备。因此表中的差值不作为采样减速或纯任务进程内存。

## 直接采集成本

| 项目 | Sphinx | Xarray |
| --- | ---: | ---: |
| guest采样器CPU | 0.230549秒 | 0.373986秒 |
| guest采样器最大RSS | 17668 KiB | 17688 KiB |
| 同步采样管理调用 | 5次／0.584690秒 | 5次／0.728927秒 |
| host采样线程CPU | 0.445346秒 | 0.624057秒 |
| host计数读取墙钟累计 | 0.353406秒 | 0.496302秒 |

PSS和mincore均为NOT_REQUESTED，无扫描或最终清单文件。与任务并行的采集命令等待不算新增串行成本；CPU、读取用时和管理调用分别记录，不叠加成总减速。guest CPU只覆盖采样进程本身，不包含runner边界date／awk等命令，RSS也不是独占物理内存。

两项各运行一次，没有关闭采样的对照，不能用本表或旧PSS开启结果计算新版减速率。默认回收保持，未采DAMON事件计数，不将本轮Cached每段下降直接归因于DAMON。

## 三个标准任务的新模式结果齐全

| 新模式单任务 | guest点数 | host点数 | 创建前current／MiB | current峰值／MiB | 峰值增量／MiB |
| --- | ---: | ---: | ---: | ---: | ---: |
| Prettier #6604 | 213 | 1119 | 14639.289 | 15564.734 | 925.445 |
| Sphinx #7590 | 224 | 1214 | 17579.406 | 18160.012 | 580.605 |
| Xarray #6992 | 666 | 1839 | 21885.977 | 23159.273 | 1273.297 |

[三任务数值表](../../results/host-current-python-20261028/standard-tasks.tsv)绑定各自原始结果；[#6604报告](HOST_CURRENT_PRETTIER6604.md)保持。三项合计99动作、1103个guest点、4172个host点，都是0.1秒guest轻量计数及host current、无PSS／mincore、2 CPU／4096 MiB和默认回收。输入镜像和准备容量不同，不能从任务间差异推导优化收益或部署密度。早期#6604缺基线、旧Python任务带PSS的数据按原轮次保留，不追溯改写。

## 执行与资源情况

```bash
export ASB_AGENTENV_SOURCE_SERVICE=asb-source-rebuilt-20261018T160925Z-2726362
bash scripts/run-host-file-monitor.sh configs/platform-sphinx7590-host-current.json
bash scripts/run-host-file-monitor.sh configs/platform-xarray6992-host-current.json
```

实际顺序执行，每组一个新guest、8 GiB自有卷、独立统计组。统计组在输入准备前建立，源服务身份固定，私有服务从创建起就在组内；无旧Firecracker或预启动池，并保存实际I/O控制器及同步创建前计数。guest及host周期仍为0.1秒，未修改任务、采样器、默认回收或同步策略，没有暂停、任务文件预读、强制驻留或模型请求。

原命令、预期退出、关键输出、patch、独立检查及原官方失败集合保持；自动guest／host报告、实际可写文件、只读根、固定卷容量及清理在两轮原生流程执行通过。每轮9份执行源码与启动前候选一致，原始计数独立复算的current峰值及增量与自动报告相同。直接复用51项有效离线检查，没有修改运行代码或重跑成功任务。

首次Sphinx准备后因磁盘不足被拦下，0个guest、0个任务动作，原FAIL和841个准备点另存于[准备失败记录](HOST_CURRENT_PYTHON_ATTEMPT.md)，不混入成功任务曲线或耗时。共享盘恢复后另用新目录继续，未删除任何备份候选。

| 成功运行磁盘观测 | Sphinx | Xarray |
| --- | ---: | ---: |
| 启动前可用 | 39.995 GiB | 39.894 GiB |
| 本轮观测最低可用 | 30.363 GiB | 31.255 GiB |
| 清理后可用 | 39.894 GiB | 39.250 GiB |

启动和实际分配前仍要求严格超过34 GiB，最低值是分配8 GiB卷之后的观测，不与启动门槛混淆。成功卷、guest、私有服务、挂载、loop和准备缓存均清理，源服务每轮恢复停止，无新失败卷。共享盘其他变化不归因于任务或清理。

## 证据与下一步

- [本次脱敏结果](evidence/host-current-python-20261028.json)。
- 成功驱动、验收及分析：`.artifacts/host-current-python-retry-20261028/`。
- Sphinx原始结果：`.artifacts/storage-isolated-service/20261028T103715Z-2210161/`；工作流程`.artifacts/platform-tasks/20261028T103552Z-2208296/`。
- Xarray原始结果：`.artifacts/storage-isolated-service/20261028T103939Z-2214400/`；工作流程`.artifacts/platform-tasks/20261028T103757Z-2212463/`。
- 两份自动报告分别在`.artifacts/platform-tasks/report-20261028T103552Z-2208296/`和`report-20261028T103757Z-2212463/`。

约定两轮已完成，停止追加。随后已[离线接通新模式的关闭采样对照路径](HOST_CURRENT_SAMPLING_CONTROL.md)，保留相同统计组范围、记账、输入准备、工具计时、任务及卷检查与清理，84项检查通过。随后[#6604关闭→开启首对58动作](HOST_CURRENT_SAMPLING_PAIR.md)已完成，原任务及清理通过；本对耗时按工具、回放、控制程序与冷启动分别记录，不套用为Python任务开销。
