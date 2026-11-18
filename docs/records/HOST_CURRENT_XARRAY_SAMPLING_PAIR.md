# Xarray：guest＋host current轻量采样开关对照

## 完成：一对原35步，当前三个标准任务均有开关对照

2026-10-28完成关闭→开启各一次Xarray #6992，共两个新guest／70动作。任务顺序、原诊断规则、最终补丁、官方945通过／12失败、真实采样开关、报告、最终卷检查及清理均通过。严格重放仍为FAIL，诊断PASS，原官方任务状态FAIL分别保留，不把预期失败改为通过。

本对原动作回放+0.19%、任务控制程序整轮+0.55%；整轮减去创建调用后+1.91%。这是本对的有符号差值，包含任务及平台波动，不是重复估计的稳定净开销。[脱敏数据与分阶段核对](evidence/host-current-xarray-sampling-pair-20261028.json)保留完整数值。

## 耗时：不要混合不同窗口

单位：秒；变化为开启相对关闭。

| 窗口 | 关闭 | 开启 | 开启−关闭 | 变化 |
| --- | ---: | ---: | ---: | ---: |
| 工具命令累计 | 34.331500 | 33.996784 | -0.334715 | -0.97% |
| 原35步回放窗口 | 36.663341 | 36.732607 | +0.069265 | +0.19% |
| 任务控制程序整轮 | 68.721351 | 69.100127 | +0.378777 | +0.55% |
| 创建guest调用 | 3.515278 | 2.650751 | -0.864527 | -24.59% |
| 整轮减去创建调用 | 65.206072 | 66.449376 | +1.243304 | +1.91% |
| 外层入口：准备至报告及清理 | 181.461267 | 184.010721 | +2.549453 | +1.40% |
| 只读缓存准备计时 | 98.740832 | 99.287226 | +0.546394 | +0.55% |
| 额外运行时报告命令 | 0.000000 | 0.678867 | +0.678867 | — |

工具累计来自35条`steps.tsv`的命令时间；回放另含执行通道和紧邻检查；控制程序还含创建、准备、原评测、采样管理及清理。外层入口包含镜像缓存准备及报告等，不能与控制程序窗口相加。缓存准备的计时不含此前归档验证，不是外层准备的全部。

同步调用按保存的单调时间核对为不重叠，累计加本地剩余时间精确回到原整轮。创建调用少0.865秒；原评测窗口多1.121秒、同步采样管理多0.730秒，最终任务检查少0.672秒，其他差异一并保留。[分阶段表](../../results/host-current-xarray-pair-20261028/stage-breakdown.tsv)中的执行调用含工具本身，不能再与工具累计相加。[35条工具对照](../../results/host-current-xarray-pair-20261028/tool-comparison.tsv)保留实际退出码，不删除较慢命令。

## 采样成本与内存曲线

| 开启组指标 | 数值 |
| --- | ---: |
| guest／host采样点 | 656／1814 |
| guest周期采样最大间隔 | 0.140144秒 |
| guest采样器CPU | 0.390975秒 |
| guest采样器最大RSS | 17696 KiB |
| 5次同步采样管理调用累计 | 0.730075秒 |
| host采样线程CPU／计数读取累计 | 0.581366／0.459381秒 |
| guest逻辑内存观测峰值：MemTotal−MemAvailable | 772.906 MiB |
| guest Cached观测峰值：含Shmem | 314.031 MiB |
| host current创建前基线 | 21405.023 MiB |
| host current全流程观测峰值 | 22674.730 MiB |
| 创建前基线之后的host current峰值增量 | 1269.707 MiB |

CPU、并行读取和同步管理调用分别记录，不相加成墙钟减速。guest采样器CPU不覆盖边界date／awk等命令；最大RSS不是独占物理量。关闭组真实没有guest周期／边界数据和host周期／基线数据，内存及采样器成本保持未测／null；无调用的管理累计为0，不把它当测得的采样CPU。PSS与mincore均未请求。

current覆盖新统计组及其后代，包括输入准备、控制程序、私有服务和guest。约20.90 GiB创建前基线包含只读输入准备，不是一个guest占用；1269.707 MiB增量仍含创建后控制、评测和服务活动，不是guest RAM独占量。它不覆盖组外记账和未记账分配；本对不计算共享收益或部署密度。guest Cached与host file是不同层，不相减求物理节省。

图已查看，均在`.artifacts`外：

- [host current创建后增量](../../results/host-current-xarray-pair-20261028/host-increase.png)：优先看这张；含控制及清理，末尾低于基线不表示负占用。
- [host全流程及anon／file／kernel组成](../../results/host-current-xarray-pair-20261028/host.png)：前段大幅增长主要是输入准备；分项用于解释current，不再相加到current上。
- [工具时延、guest逻辑内存及Cached](../../results/host-current-xarray-pair-20261028/guest.png)：guest曲线还覆盖原评测阶段，不是纯35步工具窗口。两端横轴起点不同，不能直接把同一横坐标当成同一时刻。

同目录保留SVG、两组工具原表与计时表。采样间隔0.1秒，默认回收保持；没有暂停、任务文件预读、强制驻留或回收干预。

## 原退出差异与失败记录

`trace_index_state/002`关闭返回0、开启返回141；两组均按原`xarray-approved-sigpipe`规则核对固定命令和955字节完整stdout，其他34条退出一致，最终patch和官方结果一致。实际退出码及严格FAIL原样保存，没有放宽规则或重跑任务。

此前[首次关闭组创建失败](HOST_CURRENT_XARRAY_SAMPLING_ATTEMPT.md)发生在远端manifest请求，600秒超时、0任务动作，开启未启动。用户明确批准后，只删除该次8 GiB失败卷，保留所有失败记录。恢复前用同一固定源服务内的原regctl短超时请求manifest，2.918秒成功并与保存清单逐字节相同；该检查在两个任务流程外，不计入任务或采样成本。没有更换离线传输、修改源服务或重转换镜像，旧失败不混入本对性能比较。

## 运行条件与证据位置

新增关闭配置仅在对应开启配置上加`monitor_mode: off`。复用原离线配置及源码核对，没有影响行为的新实现，不重复完整离线测试。每组准备前新建统计组，内存／I/O记账保持，不使用池内旧guest；同一固定源身份、镜像digest、冻结输入、2 CPU／4096 MiB及8 GiB卷。

```bash
export ASB_AGENTENV_SOURCE_SERVICE=asb-source-rebuilt-20261018T160925Z-2726362
bash scripts/run-host-file-monitor.sh configs/platform-xarray6992-host-current-off.json
bash scripts/run-host-file-monitor.sh configs/platform-xarray6992-host-current.json
```

这两条任务入口要求按已有流程启动并检查固定源服务；本次完整驱动还负责源服务启停、资源检查和失败后停止，位于`.artifacts/host-current-xarray-resume-20261028/run.sh`。

- 关闭工作流：`.artifacts/platform-tasks/20261028T153440Z-2289211`；任务：`.artifacts/storage-isolated-service/20261028T153621Z-2290175`。
- 开启工作流：`.artifacts/platform-tasks/20261028T153741Z-2292076`；任务：`.artifacts/storage-isolated-service/20261028T153923Z-2292974`。
- 原始计时、源码副本、分析及清理核对：`.artifacts/host-current-xarray-resume-20261028/`。
- 完整自动报告：分别为`.artifacts/platform-tasks/report-20261028T153440Z-2289211`和`.artifacts/platform-tasks/report-20261028T153741Z-2292076`。

启动前分别38.633／38.628 GiB可用；实际分配前按原预算检查，严格超过34 GiB。分配卷后最低30.631／30.626 GiB不与启动前门槛混淆。两份成功卷、挂载、loop、私有服务及统计组均已回收，源服务恢复原停止状态，无新失败卷；结束约38.620 GiB可用。任务和原检查、自动报告及自有清理实际通过后结束一对，没有追加组数。

## 下一步建议

当前Prettier两对、Sphinx一对及本次Xarray一对均已完成，新版guest＋host current采样的三任务功能与开关数据齐全。下一段建议只离线汇总三任务的同口径内存增量、直接采样成本和耗时差异，判断还有哪个具体问题值得补测；不自动增加重复或并发规模。完成条件是各窗口、基线与不同任务的结论边界一致，给出需要新增实跑与否的明确建议。
