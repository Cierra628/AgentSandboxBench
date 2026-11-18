# Sphinx：轻量host方案的一对采样开关实验（2026-10-28）

**关闭→开启各完成原35步，共两个新guest、70动作。** 任务与平台原生流程、采样开关、自动报告、最终卷检查和清理通过；原官方24通过／1失败、严格重放FAIL与诊断PASS均保持。当前入口及指标可以使用，本对开启组存在明显工具时延增加，需要单独解释，不能仅凭整轮更快判断开销低。

## 工具明显变慢，整轮却被冷启动差异掩盖

| 时间窗口 | 关闭采样 | 开启采样 | 开启−关闭 | 变化 |
| --- | ---: | ---: | ---: | ---: |
| 工具累计 | 9.683秒 | 15.928秒 | +6.245秒 | +64.49% |
| 原动作回放墙钟 | 12.027秒 | 19.235秒 | +7.208秒 | +59.93% |
| 任务控制程序整轮 | 32.094秒 | 29.632秒 | -2.461秒 | -7.67% |
| 其中：冷启动调用 | 12.746秒 | 2.606秒 | -10.141秒 | -79.56% |
| 控制程序减去冷启动调用 | 19.347秒 | 27.027秒 | +7.679秒 | +39.69% |
| 外层入口总时长 | 124.036秒 | 130.243秒 | +6.208秒 | +5.00% |
| 缓存解压及准备内层计时 | 79.483秒 | 86.648秒 | +7.165秒 | +9.01% |

百分比为`(开启／关闭−1)×100%`。关闭组冷启动多10.141秒，掩盖了开启组任务阶段的增加。控制程序减去冷启动调用后多7.679秒，但其中仍有工具、执行通道、检查和采样管理，不直接当作采样净开销。全部较慢值保留，不与Prettier或旧guest-only数据混成统一减速率。

工具累计只加原命令；回放含执行通道及结果下载；控制程序整轮还含创建、准备、检查和删除guest。外层另含输入准备、卷／私有服务、报告及清理，不含源服务启停。缓存准备内层计时不含此前归档校验，不能直接从外层相减称为任务用时。完整数值：[时间表](../../results/host-current-sphinx-pair-20261028/timing.tsv)。

## 约90%的工具增量集中在一条环境枚举命令

工具图第12条，即`trace_cpp_parser/006`，关闭／开启分别为1.585／7.178秒，增加5.593秒，占工具累计增量的89.56%。它执行的是：

```bash
cd /testbed && ls /opt/miniconda3/envs 2>/dev/null; /opt/miniconda3/bin/conda env list 2>/dev/null; pip list 2>/dev/null | grep -i -E "docutils|sphinx|pytest"
```

两组该条命令、stdout、stderr相同，原预期退出1也保持。此前另一份[相同轻量开启配置的Sphinx记录](HOST_CURRENT_PYTHON_TASKS.md)中，同一命令为1.660秒；这个历史单次开启样本单列，不计为本对关闭组或新增重复。

当前保存的是复合命令总时间，不能区分`ls`、`conda env list`和`pip list`各用了多久。已保存的命令边界计数还显示：

| 同一工具窗口的guest全局计数 | 本次开启 | 此前开启记录 |
| --- | ---: | ---: |
| Cached：命令前→命令后 | 83.477→20.145 MiB | 77.496→108.859 MiB |
| pgmajfault增量：重大缺页计数 | 291 | 119 |

pgmajfault记录取页需要I/O的重大缺页，不等于host物理盘读取次数。两次的缓存状态和缺页活动不同；这些是guest全局计数，不是该进程的独占归属。本轮没采DAMON事件或子命令等待时间，现有数据不能区分采样引起的时序变化、正常回收与其他等待的贡献，不把全部差值归给采样CPU或DAMON。

同步调用已按保存时间核对为互不重叠：工具执行通道累计增加7.186秒、采样管理增加0.548秒，冷启动减少10.141秒；其余阶段及本地剩余时间与控制程序总差精确相合。这里的执行通道时间包含工具本身，不能再与工具累计相加。参见[分阶段表](../../results/host-current-sphinx-pair-20261028/stage-breakdown.tsv)及[35条工具对照表](../../results/host-current-sphinx-pair-20261028/tool-comparison.tsv)。

## 采样及内存结果

| 开启组指标 | 数值 |
| --- | ---: |
| guest／host采样点 | 265／1279 |
| guest采样器CPU | 0.236259秒 |
| guest采样器最大RSS | 17648 KiB |
| 5次同步采样管理调用 | 0.548475秒 |
| host采样线程CPU／计数读取累计 | 0.426866／0.339832秒 |
| host current创建前基线 | 17577.516 MiB |
| host current观测峰值／相对基线增量 | 18166.375／588.859 MiB |

CPU、管理调用和并行读取分别记录，不相加成总减速；guest CPU不含脚本边界date／awk等操作，RSS不是独占物理内存。关闭组内存和采样成本保持未测／null，边界表只有表头，不补零或生成内存曲线。PSS与mincore均未请求。

current是整组及其后代的总记账，包含输入准备、控制程序、私有服务和guest；基线已有准备输入，不是单个guest占用。它不保证覆盖组外页和未记账分配。guest Cached与host file不同层，不相减求共享量；本对不计算共享收益或部署密度。

## 退出码差异按原诊断规则保留

`read_repo/001`在关闭组返回0、开启组返回141；其余34条退出码一致，最终patch及官方结果一致。既有`sphinx-pipeline-v2`只对两条指定管道允许0／141，并核对冻结命令、stdout、stderr和前缀patch；本次差异仅在第一条。两组严格重放状态仍为FAIL，没有授予严格PASS。

附加离线分析最初误套了Prettier的“所有退出码逐项相等”检查，原错误日志保留在本机。随后直接复用`sphinx7590_inputs.check_pipeline_exit`核对已批准位置及全部必要内容后通过，原规则和实际退出码都未修改，没有重跑任务。

## 用法与证据

新增`configs/platform-sphinx7590-host-current-off.json`，仅在对应开启配置上增加`monitor_mode: off`，实际bash离线输入核对通过。源服务须按原规则健康，两组均使用同一外层入口：

```bash
export ASB_AGENTENV_SOURCE_SERVICE=asb-source-rebuilt-20261018T160925Z-2726362
bash scripts/run-host-file-monitor.sh configs/platform-sphinx7590-host-current-off.json
bash scripts/run-host-file-monitor.sh configs/platform-sphinx7590-host-current.json
```

- [host运行增量](../../results/host-current-sphinx-pair-20261028/host-increase.png)、[host全流程与组成](../../results/host-current-sphinx-pair-20261028/host.png)、[工具／guest曲线](../../results/host-current-sphinx-pair-20261028/guest.png)。三张PNG均已查看，位于`.artifacts`外，同目录保留SVG及工具用时表。
- [本次脱敏数据与离线分解](evidence/host-current-sphinx-sampling-pair-20261028.json)。
- 启动、检查、原错误日志及分析：`.artifacts/host-current-sphinx-pair-20261028/`。
- 关闭组：`.artifacts/platform-tasks/20261028T132510Z-2250666`；任务与卷检查：`.artifacts/storage-isolated-service/20261028T132632Z-2251439`。
- 开启组：`.artifacts/platform-tasks/20261028T132714Z-2252999`；任务与卷检查：`.artifacts/storage-isolated-service/20261028T132843Z-2253499`。自动报告位于各工作流程的同级`report-<运行名>`目录，完整路径保存在脱敏数据中。

同一固定源服务身份、输入和诊断策略，2 CPU／4096 MiB、每组8 GiB卷；统计组在准备前建立，内存／I/O记账和新服务归属检查保留，默认回收及同步策略不变。没有暂停、任务预读、强制驻留、回收干预或模型请求。启动前分别38.679／38.675 GiB可用，分配卷后最低30.677／30.672 GiB，原严格超过34 GiB的启动及分配前门槛保持。

两个成功卷、挂载、loop、私有服务及统计组已回收，源服务恢复原停止状态，无Firecracker或新失败卷；结束约38.669 GiB可用。本次约定一对到此结束，没有扩到Xarray或追加重复。

## 后续：首次分项诊断已离线接通，实跑另定

[首次环境枚举诊断入口](SPHINX_ENV_TIMING_OFFLINE.md)已完成最短11步前缀和原样／拆分计时、输出／退出及清理验证，共35项本地检查通过；旧诊断的pip联网证据另列，不替代本对或修改较慢样本。

下一步建议两个新guest，原样／拆分各一次首次目标，关闭诊断内存采样，保留默认回收与pip环境；输出、计时及原状态和清理通过后停止。[这两组随后已完成](SPHINX_ENV_TIMING_LIVE.md)，原样1.567秒、拆分1.943秒，未复现约7秒调用；本对数据及性能边界保持。
