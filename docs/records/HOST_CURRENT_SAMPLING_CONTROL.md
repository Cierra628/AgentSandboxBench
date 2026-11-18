# 轻量host模式的采样开关对照入口（2026-10-28）

**已离线接通关闭guest与host采样的同条件入口，84项检查及三任务配置核对通过。** 本页记录最初离线实现；随后已完成[正反两对完整实跑](HOST_CURRENT_SAMPLING_TWO_PAIRS.md)，取得116动作及分窗口耗时，原任务、开关、报告与清理通过。已有三个标准任务的开启结果保持，见[三任务汇总](HOST_CURRENT_PYTHON_TASKS.md)。

Sphinx的显式关闭配置`platform-sphinx7590-host-current-off.json`也已完成[一对70动作实跑](HOST_CURRENT_SPHINX_SAMPLING_PAIR.md)，原任务、既有诊断规则、开关、报告和清理通过；本对时延增加需按报告单列。Xarray的`platform-xarray6992-host-current-off.json`随后也已完成[一对70动作](HOST_CURRENT_XARRAY_SAMPLING_PAIR.md)，回放+0.19%、控制程序整轮+0.55%，任务／诊断、开关、报告与清理通过；三任务当前对照数据齐全。

## 只切换采样，不移除统计组

新增`configs/platform-prettier6604-host-current-off.json`，相对开启配置仅增加`"monitor_mode": "off"`。不增加另一套开关。普通三任务使用相同方式；Sphinx和Xarray后来均已加入正式关闭配置，并完成本页顶部链接的一对实跑。最初离线核对与后续结果分别保留。

| 项目 | 开启组 | 关闭组 |
| --- | --- | --- |
| 输入准备前建立新统计组、内存／I/O记账 | 保留 | 保留 |
| 固定输入、私有服务、新guest及原任务检查 | 保留 | 保留 |
| 工具命令计时、控制程序整轮计时 | 保留 | 保留 |
| guest周期meminfo及工具边界内存／vmstat采样 | 原行为 | 关闭 |
| host周期current／anon／file／kernel及创建前内存基线 | 保留 | 关闭 |
| PSS、最终mincore | 未请求 | 未请求 |
| 磁盘保护、最终卷与可写文件检查、自有资源清理 | 保留 | 保留 |

两组各用一个从准备开始建立的新统计组，范围和设置相同，不复用已经有进程和记账的旧组。关闭组仍核对服务归属、无预启动VMM、实际I/O控制器和池设置；`host_file_monitor: true`此时保留这些条件，不代表启动采样线程。磁盘保护的空间查询两组均保留，不属于被关闭的内存采样。

默认回收、同步策略、2 CPU／4096 MiB、任务顺序和官方结果判断不变；没有暂停沙箱、任务预读、强制驻留或回收干预。旧`*-host-file.json`、普通开启配置及CSV／Exp1专用入口不变，旧matched／scaling的host单独开关也保留。

## 关闭结果保留计时，不生成内存曲线

外层入口从配置推导`ASB_HOST_FILE_SAMPLING=off`，仍创建带`MemoryAccounting=yes`和`IOAccounting=yes`的统计组。若显式要求host开启，或guest关闭与host环境状态矛盾，入口拒绝执行。

guest复用既有关闭实现，不创建周期采样器，并将任务脚本的内存／vmstat采样函数变为空操作；命令计时保持。结果仍保留各步退出码和时延、原任务状态、控制程序用时与清理信息。边界采样表保留表头、数据行为空。

host不构造采样器，也不读创建前内存计数。创建记录保存实际`host_sampling: off`及`before_guest_counters: null`。周期目录和最终驻留清单都不生成。

外层自动报告输出真实`tools.tsv`，guest内存为`NOT_MEASURED`，host采样为`NOT_REQUESTED`，基线、峰值和增量均为`null`。它检查关闭状态和真实缺失数据；发现周期清单、边界数据行、host采样目录、驻留清单或非空基线就报错。原官方失败、严格重放失败和诊断状态照实保留，不补零、不画假曲线。

## 使用方式

仅检查本机输入，不启动服务：

```bash
bash scripts/use-platform.sh check \
  configs/platform-prettier6604-host-current-off.json --offline
```

以后实跑关闭组使用同一个外层入口；源服务须先按原规则确认健康，此命令不启动或替换源服务：

```bash
export ASB_AGENTENV_SOURCE_SERVICE=asb-source-rebuilt-20261018T160925Z-2726362
bash scripts/run-host-file-monitor.sh \
  configs/platform-prettier6604-host-current-off.json
```

开启组改回`configs/platform-prettier6604-host-current.json`。不要直接用普通`use-platform.sh run`替代外层入口；两组都必须先建立统计组。每组8 GiB卷，启动前和实际分配前仍须通过严格超过34 GiB可用磁盘等原保护，失败卷定向删除须另有授权。

## 验证证据与成本记录

84项检查覆盖新配置与原命令差异、外层与隔离执行入口的条件、关闭时无host采样器和基线读取、报告拒绝残余采样、成功／失败后的缓存回收，以及旧平台、host计数／报告、CSV、Exp1、轻量guest、matched和scaling路径。测试使用本地临时数据、临时采样进程或模拟服务；临时文件mincore回归不查询实验卷或任务文件。

三任务的关闭配置另经实际bash离线入口核对，均为`VALIDATED`。开启组的原成本字段继续保存；关闭组不生成采样CPU／内存峰值，不用零代替。两组的工具累计和控制程序整轮从原始任务结果读取，后续分别比较；前置镜像准备和最终卷检查用时另列，不混入任务控制程序整轮。

- [脱敏验证记录](evidence/host-current-sampling-control-offline-20261028.json)。
- 本机日志、修改前后源码和临时配置：`.artifacts/host-current-off-offline-20261028/`。

## 原首对计划与已完成结果

建议按关闭→开启各跑一次原29步，共两个新guest、58动作。两组保持同版本、同源服务身份、同输入及准备条件、默认回收；先验证关闭组没有采样、开启组有有效guest／host数据，原官方0通过／2失败与关键结果、自动报告、最终卷检查及清理均应保持。

分别报告工具累计、冻结回放、任务控制程序整轮和外层准备等阶段的直接差值，开启组另列采样CPU与管理成本。到一对停止，仅作为本对观察，不与旧版本结果混算或自动增加重复轮数。上述首对随后经用户授权完成，实际结果与下一步范围见本页顶部链接。
