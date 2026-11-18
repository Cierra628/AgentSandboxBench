# 当前七沙箱GRPO跳过边界vmstat：离线核对（2026-11-01）

## 结论：复用现有变换，配套接通入口和检查器

**后续已完成[离线接入与回归](GRPO_LOCAL_BOUNDARY_VMSTAT_IMPLEMENTATION.md)。** 下文保留接入前的限制和20项核对记录；当时NOT_SUPPORTED的状态已更新为参数及报告接通、真实候选待验证。

**不必改采样器，也不必改并发调度。** 现有`boundary_runner(..., skip_vmstat=True)`只把`sample_vmstat`函数体改为空操作，保留函数调用位置、表头、边界meminfo、阶段文件写入和原命令计时。它可以作为当前普通输入／无代理GRPO的优化候选。

核对时完整入口仍不支持这个组合。配置、原生参数、任务执行器以及两个结果检查器都有限制；四次对照程序也只认识“整套采样关／开”。只放开配置，会在后面的参数检查或报告阶段失败，必须一起处理。

本段只核对源码、参数函数和保存结果，生成静态预览；没有修改运行脚本、现用配置或采样设置，没有启动服务／沙箱、联网、驻留查询或清理。[20项核对及实际拒绝位置](evidence/grpo-local-vmstat-offline-20261101.json)已保存。候选在该次核对时的状态为`NOT_SUPPORTED`；接入后的状态见上方新报告，性能收益仍未测。

## 只减少当前主曲线不用的边界读取

读取冻结包的原执行脚本，用现有函数生成“保留／跳过”两份文本。默认变换与刚完成两次开启组实际执行的脚本完全一致；候选与默认文本的唯一差异是vmstat函数体。两份预览均通过bash语法检查，没有执行其中的任务。

| 项目 | 保留边界vmstat | 候选：跳过边界vmstat |
| --- | --- | --- |
| 七guest、189动作、逐guest准备／并发调度 | 原样 | 原样 |
| host current、guest周期meminfo | 0.1秒 | 0.1秒 |
| 边界meminfo | 448条 | 448条 |
| 边界vmstat | 448条 | 仅表头、0条数据，标为未采集 |
| late_1的两种边界记录 | 各76条 | meminfo76条，vmstat未采集 |
| 原命令、退出码、计时片段和阶段写入 | 原样 | 文本相同 |
| 默认回收、PSS、105输入查询及资源保护 | 现有方式 | 现有方式 |

行数来自[当前四次保存数据](../../results/prettier-grpo-local-no-proxy-breakdown-20261101/REPORT.md)，不是重新采集。按现有函数，每次vmstat读取还调用一次date和一次awk，因此候选每轮预期少448次读取、896次外部命令调用；这是操作次数，不是已经测得的秒数。0条vmstat记录不代表页故障或I/O为零，继续使用现有`NOT_MEASURED`元数据。

## 最小配套范围：八个现有脚本和一份候选配置

| 位置 | 需要处理的实际限制 |
| --- | --- |
| [platform_tasks.py](../../scripts/platform_tasks.py)的`configuration` | 跳过选项当前要求`grpo_create_before_setup`；增加当前显式本地GRPO的例外，仍要求采样开启、轻量模式及原任务范围 |
| [host_persistent_input.py](../../scripts/host_persistent_input.py)的`local_grpo_config` | 当前明确排除跳过选项；仅对开启采样的新候选放行，关闭采样＋跳过仍拒绝 |
| [validate_isolated_agentenv.py](../../scripts/validate_isolated_agentenv.py)的`ordinary_current_only` | 当前也排除跳过选项；保留普通输入、current、无代理及无其他诊断条件 |
| [run_prettier6604_grpo.py](../../scripts/run_prettier6604_grpo.py)的`skip_boundary_vmstat_options` | 目前必须先通过旧创建顺序检查；增加当前本地逐guest准备分支，不改`do_grpo`、`RuntimeMonitor`或原189动作 |
| [report_prettier6604_grpo.py](../../scripts/report_prettier6604_grpo.py)的`audit` | 当前候选必须是旧创建顺序；新分支依据保存的布局、选项、七guest无代理证明及轻量状态校验，不依赖离线报告进程的环境变量 |
| [run_grpo_light_pair.py](../../scripts/run_grpo_light_pair.py)的`inspect_run` | 共享的检查函数也只接受旧创建顺序的候选；保留原任务审核及`NOT_MEASURED`检查，加入受限新分支，不能绕过这个函数或抹掉候选标记 |
| [run_grpo_local_no_proxy_controls.py](../../scripts/run_grpo_local_no_proxy_controls.py) | 当前开启组强制两种边界均448条，固定关→开／开→关；增加显式“保留／跳过边界vmstat”对照，默认仍是原整套开关，两组`mode`都保持on |
| [report_grpo_local_no_proxy_controls.py](../../scripts/report_grpo_local_no_proxy_controls.py) | 原比较与文字使用“关闭采样／开启采样”；新对照要明确写“保留／跳过边界vmstat”，分别展示真实行数和成本，不重定义旧off／on |

候选配置从`platform-prettier6604-grpo-host-current.json`派生，只增加`light_skip_boundary_vmstat: true`，不增加先全部创建开关。当前静态预览在`.artifacts/grpo-local-vmstat-offline-20261101/check-fixed/candidate-config-preview.json`；它是接入前的静态预览，原拒绝结果保留；现用候选配置及支持范围见接入报告。

已经可直接复用的部分：平台参数构造已有该选项，原生预检查和实际任务两处转交都已存在；`verify_path`已有“meminfo数量保持、vmstat表头完整且没有数据”的校验；轻量报告已传递候选标记及未采集说明。外层host工作流和无代理检查通过上述配置函数识别范围，不需另造采样器、参数链或通用调度框架。

内层已有无代理模式及完整七guest约束可用于识别候选，外层仍须核对普通输入／current组合。资源创建前的参数检查不能要求已有活guest；完成任务后的报告则使用保存的证明。原旧创建顺序、整套关闭、单任务及未带选项的入口保持原合同。

## 离线实现后的验证条件

1. **范围与参数：**新配置到真实原生解析只多一个选项，保持逐guest准备；普通输入／无代理／current检查前后一致。关闭采样、BPO／恢复、前缀、物理扫描、回收干预及其他任务不能混入。旧候选及旧默认入口保留原行为。
2. **执行文本与小文件用例：**候选只替换vmstat函数体，命令、计时和阶段写入相同。复用原两动作模拟用例，确认原退出0／1及内存记录保持、候选不执行vmstat读取；不执行真实任务文件。
3. **结果与报告：**请求选项、实际脚本、任务结果、有效配置和报告一致；每段边界内存数量正确，总计448条，候选vmstat只留原表头。候选出现数据、内存缺行或标记不一致必须拒绝；原退出／关键输出／补丁／官方失败规则不放宽。
4. **开销量化：**两组都保留0.1秒host／guest采样及管理操作，比较工具累计、并发动作及下载、检查、控制程序、采样CPU和管理调用，输入查询／准备仍单列。不把旧关闭组或历史候选直接并入新配对。

已有逐路径分析器按on强制两种边界行数相等，不能直接接收本候选。首次收益量化可以复用不依赖vmstat的控制程序阶段函数和原计时，旧分析器继续拒绝；若以后需要候选的逐路径分解，再显式扩展其校验，不把这项变成首次实跑的前置工作。

## 正式验证范围：两组都采样，只比较这一项

实施并通过离线检查后，建议固定两对四次：**保留→跳过、跳过→保留**。每次新统计组／输入／私有服务、七个4 GiB／2 vCPU guest、原189动作，共28guest、756动作。首个正式候选组同时验证新路径，不增加仅创建或额外短测。

保留默认回收、原逐guest准备／并发顺序、任务及每实例官方原0通过2失败、同步基线、一次105输入查询、无PSS和原资源保护。准备前可用磁盘严格>54 GiB、MemAvailable>64 GiB，来源释放后分配16 GiB运行卷前仍严格>42 GiB；这些条件留到实跑前重查，本段不为离线核对申请清理。

功能正确、真实采样与跳过状态、查询和清理通过且逐对差值可复算，即完成这两对；不预设必须有正收益、不为更好百分比加轮。任务或平台失败保留首个现场并停止，检查器失败先用原始结果修复，不重跑成功任务。

## 证据与复算

接入前20项核对包含13个当时的拒绝位置或消费限制；参数／配置函数实际调用，报告范围断言则从实际源码中单独执行，未运行完整任务审核，也没有把旧数据改写成候选。脚本内禁止外部进程调用，实际调用数0；另仅对两份静态预览执行bash语法检查。

初次核对脚本误把原生自身的选项检查也算作参数转交，已改为按源码语法树定位`check_args`及`task_args`两处转交；原失败日志保留，没有为此启动或重跑任务。

```bash
bash scripts/assess-grpo-local-vmstat.sh \
  --output .artifacts/grpo-local-vmstat-assessment-new
```

当前核对脚本已随接入更新，复跑检查现在的支持状态，不再复现接入前的13处拒绝。输出目录必须不存在。[脱敏核对](evidence/grpo-local-vmstat-offline-20261101.json)包含每项结果；源码预览及日志在`.artifacts/grpo-local-vmstat-offline-20261101/`。

**本段离线核对完成。下一步建议只做上述受限接入和必要离线回归，通过后再安排两对实跑。**
