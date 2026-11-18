# Sphinx／Xarray无代理采样开关：方案与离线实现

## 结论：Sphinx和Xarray各两对四次正式对照均完成

2026-10-31先完成源码、实际配置和保存结果核对，随后按用户授权完成四个运行脚本的定向接入，104项相关离线回归通过。2026-11-01分别完成Sphinx和Xarray各自固定关→开、开→关四次正式验证；原任务／政策／评测、真实开关、共同查询及清理均符合要求。外层启动、平台分派、结束后输入核对和报告直接复用，没有新增采样器或报告框架。

Sphinx两对工具增加0.231秒（1.18%）／0.140秒（0.71%），控制程序增加0.784秒（2.56%）／1.714秒（5.67%）；[逐轮成本、用时图与内存曲线](../../results/sphinx-local-no-proxy-sampling-controls-20261031/REPORT.md)已交付。创建0.879～1.083秒、额外评测3.973～4.124秒及组外准备单列，慢值不剔除，不由两对确定稳定总体减速率。四次获准管道退出差异0项，原末步25通过和额外24通过／1失败保持；诊断PASS与历史严格FAIL分别保留。

Sphinx第一轮正式关闭同时验证真实新路径，通过后完成其余三次，没有额外短测。关闭运行内存未测；两次开启共1295个host点、600个guest点，覆盖原五段与额外评测，每轮另保留80条内存／80条vmstat边界读数。四次结束后116文件驻留均8501.008 MiB、容量不足0、逐页归属未查；成功临时资源释放，固定源服务恢复停止，无活动容器或Firecracker。原始记录及执行／离线汇总脚本保留在`.artifacts/sphinx-local-no-proxy-sampling-controls-20261031/`，[脱敏结果](evidence/sphinx-local-no-proxy-sampling-controls-20261031.json)单列本批，不混入旧开启或Prettier数据。

Xarray随后也完成[四次结果及曲线](../../results/xarray-local-no-proxy-sampling-controls-20261101/REPORT.md)：工具变化−1.525秒（−4.44%）／＋0.863秒（＋2.60%），控制程序变化−1.513秒（−1.76%）／＋1.460秒（＋1.74%），方向相反，不作固定加速或减速率。原35步、负向／修复后独立检查、末两次3通过1失败和129通过1失败、额外945通过12失败的完整名单保持。四次均只有trace_index_state/002出现原先允许的0→141差异，命令及955字节标准输出满足原条件；实际退出逐项完全匹配状态FAIL、诊断PASS、历史严格FAIL分别保留，政策未扩大。

Xarray两次开启共2551个host点、1661个guest点覆盖五段及评测，关闭运行内存未测；四次共同116文件驻留均10388.641 MiB、容量不足0、逐页归属仍未查。临时资源释放，源服务恢复停止，无活动容器／Firecracker，收尾约62.630 GiB。[Xarray脱敏结果](evidence/xarray-local-no-proxy-sampling-controls-20261101.json)及`.artifacts/xarray-local-no-proxy-sampling-controls-20261101/`保留原始位置、执行与离线汇总脚本。

先前离线阶段没有启动服务或沙箱。正式两任务共八次沿原恢复／导入和无代理本地路径执行，没有下载或17891回退日志；默认DNS例外及流量未测范围保持。两任务均按四次结束，不追加或替补；下一步仅离线统一三任务各两对的指标、范围和入口，不重跑、重新查询驻留或合算跨任务减速率。

## 使用已有关闭配置，两任务分别统计

Sphinx本批关闭组使用以下入口；以后复跑仍须对应授权、固定源服务就绪及资源预算：

```bash
bash scripts/run-host-file-monitor.sh \
  configs/platform-sphinx7590-host-current-off.json \
  --persistent-input --local-no-proxy
```

开启组只换为`platform-sphinx7590-host-current.json`。Xarray对应使用`platform-xarray6992-host-current-off.json`和不带`-off`的配置，其他选项保持。两任务的原任务、默认回收、无代理、本地缓存检查和结束后116文件查询不变；关闭组运行内存及采样CPU保持未测，不输出假图。

新增12项回归覆盖真实配置与原生解析、原生至共用执行器分派、完整关闭的限制、外层清代理和准备顺序、无host线程／guest采样器、共同查询的阶段／成本／释放顺序、失败输入保留及实际工具报告。原92项相关回归继续通过；原先拒绝Python关闭的断言已改为新正向验证和短检查／冲突开关负向验证，continuous但非light的新无代理用法仍拒绝。

首轮检查的两个错误均在新增测试本身：模拟原生分派缺container变量，以及把含return的检查片段作为模块编译。已补齐模拟上下文并只执行实际运行分支的条件，首轮日志保留，未修改生产逻辑迎合检查。最终104项通过；语法／帮助与文档链接另做离线检查。[实现证据](evidence/python-local-no-proxy-off-implementation-20261031.json)及`.artifacts/python-local-no-proxy-off-implementation-20261031/`保留修改前代码、差异及日志。

## 修补前的阻挡与本次修改

两任务各自的`platform-*-host-current-off.json`与开启配置仅差`monitor_mode: off`；解析后`light_monitor`也变为false。修补前实际外层在Docker、准备和预检之前拒绝，真实持久化命令生成也拒绝；当时只能用已有非持久化关闭命令推导拟接入参数。本次已用真实持久化命令生成和原生解析回归，不再用拟接入参数代表成功。

原生解析结果与真实开启参数只差两项：`light_monitor: true→false`及`python_monitor_mode: continuous→off`。两个任务都满足原有`ordinary_current_only(..., monitor_mode='off')`判定；本次接通以下条件：

| 修改文件 | 修补前阻挡 | 本次修改 |
| --- | --- | --- |
| `scripts/prettier_no_proxy.py` | `require_mode()`只为Prettier允许关闭 | 将关闭放行扩到固定Sphinx／Xarray，继续检查各自原重放政策、持久化、完整单任务和默认回收 |
| `scripts/host_persistent_input.py` | `single_config()`及`native_option()`的关闭分支只接受Prettier | 仅在显式无代理条件下接受两种完整只读Python任务；保留原固定备份、来源、统计组及分析模式限制 |
| `scripts/validate_isolated_agentenv.py` | 无代理检查的关闭部分仍要求Prettier | 允许已识别的Sphinx pipeline-v2／Xarray诊断重放走现有current-off分支；短创建检查仍仅Prettier且不能关闭采样 |
| `scripts/run_xarray6992.py` | 两任务共用执行器的无代理条件要求light | 接受完整关闭重放；保留persistent、各自政策、无checkpoint／缓存控制／评测替换等限制 |

以下实现直接复用，不计划修改：

- `run_host_file_workflow.py`已根据关闭配置设置host-off，拒绝冲突host-on，并把显式无代理条件传给持久化检查；清除六种代理变量及固定源服务要求保持。
- `platform_tasks.py`已能不创建host线程；开启要求真实创建前基线，关闭要求明确off且基线为None。不能为通过检查给关闭组补读一次内存。
- 原生Python分派已传递`--monitor-mode off`及各自原政策；完整任务使用`run_xarray6992.py`，不是短诊断执行器`run_sphinx_command_diagnostic.py`。
- Python执行器在关闭时不构造`RuntimeMonitor`，不启动旧host监控；`without_sampling()`将边界函数置为空操作，移除逐步后台监控，没有guest采样阶段标记。实际采样器上传／启动由runtime控制。
- `report_task_overview.write_tool_report()`与平台关闭报告已保留工具时延、严格／诊断／评测状态，内存标为未测，不输出假图；仍检查周期、边界和host残留。
- 共同结束后输入查询、报告附加收据和成功后输入释放已有任务参数，不需要新增查询或关闭组特例。

旧非持久化关闭和Prettier新关闭保持原行为。未显式使用无代理的新持久化关闭继续拒绝，不为扩展Python放宽旧默认入口。

## persistent-sampling选项保留，实际采样仍完全关闭

Python入口的`--persistent-sampling`／原生`--python-persistent-sampling`还选择现有完整重放路径，并是Sphinx pipeline-v2及Xarray诊断重放的必要条件。关闭组不能删掉这个选项，否则会改变或拒绝原任务路径。

实际控制采样的是`--monitor-mode off`：源码选择runtime=None，转换后的任务脚本没有`/proc/meminfo`、`/proc/vmstat`读取或逐步监控进程。已离线生成两份关闭脚本文本，原工具命令及从start_ns至end_ns的计时片段不变，没有执行脚本。运行中内存、峰值和采样CPU保持未测；不能把保留这个命名选项当作仍在采样。

两份保存的开启结果中，按原五段分别统计：

| 任务 | 原工具次数 | 内存边界行 | vmstat边界行 | 结束后唯一输入文件 |
| --- | ---: | ---: | ---: | ---: |
| Sphinx #7590 | 35 | 80 | 80 | 116 |
| Xarray #6992 | 35 | 80 | 80 | 116 |

原开启runner与当前边界变换一致；相关runtime及重放检查函数与保存版本一致。后续对照覆盖周期采样、这些边界读取及guest采样标记整套操作，不另造“只关0.1秒、留边界读取”的模式。直接采样线程／进程CPU不包含这些Shell边界操作，端到端影响另用开关配对量化。

## 两个任务分别保留原退出政策与评测

| 项目 | Sphinx #7590 | Xarray #6992 |
| --- | --- | --- |
| 原任务 | 35步，七个独立解析／回转／ID用例，原末步25通过 | 35步，负向对照及修复后独立检查；原末两次为3通过／1失败、129通过／1失败 |
| 退出政策 | `sphinx-pipeline-v2`；仅read_repo/001及core_patch/001两个固定管道，在原命令、标准输出／错误、对应补丁满足已有条件时允许141或0；其他步骤原退出不变 | `xarray-approved-sigpipe`；仅trace_index_state/002固定管道，在原命令和955字节原标准输出满足已有条件时允许0或141；其他步骤原退出不变 |
| 额外选定评测 | 25项：24通过、1失败，失败为`tests/test_domain_cpp.py::test_expressions` | 957项：945通过、12失败，失败项按原名单逐一核对 |
| 状态 | 诊断PASS、历史严格FAIL分别保留 | 诊断PASS、历史严格FAIL分别保留；实际退出匹配与获准差异另列 |

现有重放检查函数已接收`sampling=monitoring`，关闭时接受无边界内存数据而保留原命令、顺序、退出、关键输出和补丁检查，不需修改退出政策。继续保持原冻结Shell及base／testbed Python分工，不通过调整测试选择、返回码或命令来满足新关闭模式。

## 共同查询保持同一阶段、同一对象

两组都在隔离运行及自有运行资源清理后、输入删除前，对116个导入普通文件做一次mincore，并读取查询前后组file。文件按设备号和inode去重，保存顺序、身份和查询墙钟／线程CPU，成功后再释放输入。原导入、同步、来源完整性、统计组归属、池关闭及实际写文件边界保持。

关闭的是运行时采样，不是内存记账或所有查询。关闭组的创建前内存计数为None，运行峰值和曲线未测；共同的结束后输入驻留仍是实测，单列成本。mincore只核对导入文件驻留，逐页归属仍未查；不代表运行中的整体物理量或共享收益，不加到current上。

## 离线实现已完成，正式运行仍按原条件验收

只修改上表四个运行脚本，不新增采样器、任务执行器、配置或报告框架。已完成的回归覆盖：

1. 两任务真实配置→平台／原生参数→共用执行器分派；开关只改变采样字段，保留persistent-sampling和各自原政策。
2. 新关闭不创建host线程、guest采样器、边界读取和采样标记；运行基线None、报告未测且无假图，共同116文件查询和成本／清理顺序保持。
3. 缺本地缓存或代理证明、错误开关／基线、查询及清理失败保留输入；未知任务、错误政策、短检查、恢复和精细分析组合仍拒绝。
4. Prettier新开关、两任务原开启和旧默认入口继续通过；已完成真实结果不重跑作回归。

回归只使用小文件和模拟检查；随后Sphinx／Xarray分别执行下述独立四次，各自首轮正式关闭验证真实新路径，没有因另一任务通过而省略。

## 已完成的正式范围：每任务各四次，共280步

每个任务固定关→开、开→关，四次新4 GiB／2 vCPU guest，共140个原动作；两任务合计八次／280步，但分别安排、验收和统计。每个任务第一轮正式关闭同时验证该任务新路径，通过后完成另外三次，不额外增加仅创建验证，不拿旧单次开启插入新配对。

每次新统计组、新116文件输入、新私有服务及8 GiB运行卷；默认回收，不暂停、不额外预读或强制驻留，不使用17891或补下载。分别记录工具累计、冻结重放、控制程序整轮、创建、额外评测、组内流程、组外准备、导入及最终查询。开启另列采样点数、覆盖、间隔、直接CPU及同步管理调用；关闭运行内存未测。

| 任务 | 临时来源 | 输入预算 | 准备前磁盘严格大于 | 运行卷分配前磁盘严格大于 |
| --- | ---: | ---: | ---: | ---: |
| Sphinx | 20 GiB | 10 GiB | 56 GiB | 34 GiB |
| Xarray | 24 GiB | 12 GiB | 62 GiB | 34 GiB |

MemAvailable仍须大于64 GiB，来源释放后才分配运行卷；原20 GiB动态停止线、余量、单写入锁、超时和失败现场保留不变。当前离线空间只作记录，未来实跑前重查，不提前清理或降低门槛。

完成条件为各自原功能／政策／评测、真实开关、共同查询及清理均有证据，逐对`开启−关闭`秒及百分比可复算。保留慢创建和慢评测，不剔除或替补，不为更低百分比追加轮数。任务／平台失败停止该批并保留首个现场；检查器问题先用原日志离线处理，不重跑成功任务。

- [离线核对证据](evidence/python-local-no-proxy-off-assessment-20261031.json)
- 核对脚本、生成的两份关闭runner文本及日志：`.artifacts/python-local-no-proxy-off-assessment-20261031/`
- 原开启结果：[Sphinx](../../results/sphinx-local-no-proxy-20261031/REPORT.md) · [Xarray](../../results/xarray-local-no-proxy-20261031/REPORT.md)
- 已完成的共同路径参考：[Prettier两对四次](../../results/prettier-local-no-proxy-sampling-controls-20261031/REPORT.md)
