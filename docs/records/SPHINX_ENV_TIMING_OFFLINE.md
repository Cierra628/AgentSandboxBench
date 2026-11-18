# Sphinx环境枚举命令：首次执行的分项计时（2026-10-28）

**已离线接通最短诊断入口，35项检查和四种命令行输入检查通过。** 只准备了代码、验证和下一次范围，没有启动真实服务、沙箱或任务；正式35步与现有轻量采样配置不变。[上一对实验](HOST_CURRENT_SPHINX_SAMPLING_PAIR.md)中1.585→7.178秒的慢调用仍按原数值保留。

随后已完成[两个新guest的原样／拆分首次诊断](SPHINX_ENV_TIMING_LIVE.md)，两组及清理通过，未复现约7秒慢调用；本页保留原离线实现说明。

## 旧证据可以复用，不重做十次热缓存重复

[既有诊断](SANDBOX_MONITORING.md#单条命令短诊断pip版本检查有联网等待正式轨迹保持原样)已经验证过pip版本检查的联网等待：同一VM内默认两次约1.991／1.778秒，关闭版本检查约0.609／0.602秒，输出和退出码相同。但那是25毫秒进程扫描下的热缓存诊断，没复现当前7.178秒调用，不能用它解释本次全部增量。

原入口先执行13步，再做6次目标命令重复及4次版本检查对照；目标第一次出现的时刻已经过去。本次保留旧入口，新增只测首次目标调用的选项，不重复上述十次热缓存控制，也不关闭pip版本检查。

## 前11步保留，第12步就是待测命令

| 顺序 | 新诊断中的处理 |
| --- | --- |
| `read_repo/001–006`，共6步 | 原样执行，复用既有输入及输出检查；首条0／141仍按原管道规则判断 |
| `trace_cpp_parser/001–005`，共5步 | 原样执行；检查原退出码、输出和patch，不启用find失败例外 |
| `trace_cpp_parser/006`，全局第12步 | 首次执行目标命令：原样或分项计时，仅一次 |
| 全局第13步及其后 | 不执行，也不做完整任务评测 |

这是保持原顺序、不提前运行目标的最短连续前缀，不表示11步都是运行conda／pip的功能依赖。前缀没有修复源码的命令，但文件读取、Python探查和find会改变缓存及启动状态，所以不随意省略。第13步使用testbed解释器做后续解析探查，与首次目标调用无关，已排除。

原动作清单完整保留；只在新诊断的任务脚本中加入停止位置，前11步命令不改。脚本停止在目标之前，检查器拒绝第6、7条trace步骤已执行或目标输出提前出现的结果。

## 两种模式只用于定位工具内部等待

| 选项 | 目标执行方式 | shell次数 |
| --- | --- | ---: |
| `whole` | 原复合命令文本原样运行一次 | 1 |
| `split` | 同一顺序执行ls、conda、pip＋grep，合并stdout／stderr，最终退出码取最后一项 | 3 |

分项模式在同一工作目录、继承环境下运行；这些子命令不修改父shell状态，原命令的cd效果由工作目录保留。额外两次shell启动是诊断成本，计入各项时间，不从数据中扣掉，也不将两台VM的差值当作计时器成本。两种模式都必须与固定参考核对总输出和原预期退出1。

计时采用单调时钟`time.monotonic_ns`，记录每项墙钟时间、等待完成的子进程CPU及缺页计数、诊断器CPU；原样与分项都有同样的父进程记录。总计满足`elapsed_ns = parts_elapsed_sum_ns + between_parts_ns`，合并输出在计时后进行。目标共用90秒预算，超时终止并回收本次子进程组，不继续下一项；TIMEOUT不会算作捕获成功。

新诊断关闭周期和边界内存采样，不运行25毫秒进程树扫描、PSS或mincore，不改变回收或pip环境。它先回答哪个子命令慢，不是新一轮0.1秒轻量方案开销对照；正式采样开关、35步命令及报告保持原行为。新结果的完整重放和官方评测均为`NOT_RUN`。

## 调用入口和检查

只离线核对输入，不启动沙箱：

```bash
python3 -B scripts/run_sphinx_command_diagnostic.py \
  --inputs .artifacts/sphinx-backbone/prepare-20261009T041720Z \
  --reference .artifacts/storage-isolated-service/20261009T055637Z-3955483/python-backbone \
  --env-command-timing split --check-only
```

以后实跑使用既有8 GiB隔离入口。源服务须按原规则确认健康，固定源身份不变；此脚本不启动或替换源服务：

```bash
export ASB_AGENTENV_SOURCE_SERVICE=asb-source-rebuilt-20261018T160925Z-2726362
bash scripts/validate-runtime-command.sh --env-command-timing whole
bash scripts/validate-runtime-command.sh --env-command-timing split
```

每条命令各建立新guest和卷。仍需启动及分配前可用磁盘严格超过34 GiB、准备时64 GiB RAM余量，通过最终卷及可写文件检查和自有资源清理；失败卷保留，定向删除需另获授权。原离线阶段未执行这两条命令，随后授权实跑的结果见本页顶部链接。

结果在本轮隔离结果的`python-backbone/asb-command/target/`：每项各有stdout／stderr，`result.json`记录总时间和分项。原报告命令识别新模式，导出`summary.json`和`timing.tsv`，保留诊断及`NOT_RUN`状态，不生成内存曲线：

```bash
python3 -B scripts/report_sphinx_command_diagnostic.py \
  <隔离结果/python-backbone> <新的.artifacts报告目录>
```

旧无参数诊断、`--find-diagnostic`、旧报告及正式35步入口保持原条件。新选项不与find诊断或内存采样混用，这个限制只作用于新模式。

## 本次离线证据

35项检查含新增9项：真实清单与停止位置、只执行前5条trace、本地原样／分项输出及非零退出、总计时、超时回收、错误数据拒绝、native参数传递、成功／失败的模拟guest清理、旧入口仍执行原热缓存重复及新报告状态。其余26项复用既有Sphinx、命令捕获及诊断报告检查；测试仅运行临时数据、玩具命令或模拟服务，没有执行真实conda／pip任务。

原样、分项、旧无参数及find四种实际`--check-only`均为`VALIDATED`，bash语法通过。[脱敏验证记录](evidence/sphinx-env-timing-offline-20261028.json)；修改前源码、测试日志、前缀计划和通过验证的代码在`.artifacts/sphinx-env-timing-offline-20261028/`。

## 原两组计划与后续结果

建议先`whole`、再`split`，各一个新guest，每组11条原前缀命令和一次目标调用；拆分组目标由三个子命令组成，不称为完整原12步重放。首组直接验证新前缀与清理，第二组验证首次分项计时，不另加短组、热缓存重复或pip开关控制。

完成条件：前缀、原输出及退出码、源码与原负例检查保持；时钟、分项总计、CPU／缺页计数、继承的pip环境和额外shell次数有记录；原报告、最终卷检查及清理实际通过，固定源服务恢复原停止状态。保留全部结果，即使没有再现7秒慢调用也到这两组结束，不修改原实验或自动扩到完整Sphinx／Xarray。

上述两组随后均已完成，未追加重复；下一步范围见[实际结果](SPHINX_ENV_TIMING_LIVE.md)。
