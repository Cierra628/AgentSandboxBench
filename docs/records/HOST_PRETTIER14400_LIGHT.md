# Prettier #14400轻量测量：一轮完整验证（2026-10-28）

**原26步、独立功能检查和官方4条断言通过，guest／host的0.1秒采样、自动报告、最终卷检查及清理全部完成。** 本轮使用host总记账`memory.current`作为主指标，关闭PSS和mincore，保留AgentENV默认回收。没有新增模型请求或快照操作。

## 先看运行增量，再看全流程

- [host运行增量图](../../results/host-current-prettier14400-20261028/host-increase.png)：相对创建guest前基线，放大随后创建、任务、检查和清理的变化。
- [host全流程及组成图](../../results/host-current-prettier14400-20261028/host.png)：包含缓存准备；第二幅展示anon／file／kernel，不与current相加。
- [工具时延及guest曲线](../../results/host-current-prettier14400-20261028/guest.png)：26次工具调用、guest非可用内存及Cached；后两幅只有一个guest。
- [26步用时表](../../results/host-current-prettier14400-20261028/tools.tsv)。以上均已复制到`.artifacts`外，PNG实际查看通过；同目录保留SVG。

| 本轮观测 | 结果 |
| --- | ---: |
| 工具累计 | 2.210秒 |
| Exp1控制程序整轮 | 14.800秒 |
| guest采样点／最大间隔 | 97／100.743毫秒 |
| guest非可用内存峰值（MemTotal−MemAvailable） | 521.883 MiB |
| guest Cached峰值（含Shmem） | 231.402 MiB |
| host采样点／最大间隔 | 1008／100.137毫秒 |
| 创建guest前current基线 | 14632.219 MiB |
| 全流程及服务就绪后current观测峰值 | 15445.199 MiB |
| 服务就绪后峰值相对基线的增量 | 812.980 MiB |
| 最后采样相对基线的增量 | −268.312 MiB |

工具累计只加26个原动作的单调时钟用时。控制程序整轮从Exp1控制程序启动至写入结果，包含服务身份和输入核对、冷启动、上传、采样管理、动作、下载、独立／官方检查和删除guest；不包含此前的缓存准备、私有服务启动及外层后续卷检查／制图。两者之差不能解释为采样开销。

guest曲线从工具通道就绪后的采样启动覆盖到独立／官方检查完成，不是只有26步命令。host覆盖更长的准备、服务、沙箱及清理窗口，基线已包含准备输入和私有服务。最后一点在清理阶段，负增量照实保留，不表示guest有负内存。

`current`是整组及后代的内存记账，不是纯沙箱进程或完整物理占用：组外共享页、未记账内核分配不包含，swap未采、hugetlb覆盖未核实。`file`不是guest Cached对应的物理页；本轮没有mincore驻留或多沙箱对照，不推导共享节省或部署密度。完整定义见[current方案](HOST_CURRENT_PRIMARY.md)。

## 采集成本直接记录，不替代开关对照

| 直接成本 | 本轮结果 |
| --- | ---: |
| guest采样器CPU时间 | 0.143197秒 |
| guest采样器运行窗口 | 9.575秒 |
| guest采样器最大RSS | 17136 KiB（16.734 MiB） |
| 5次同步采样控制调用累计 | 0.553809秒 |
| host采样线程CPU时间 | 0.360278秒 |
| host计数读取墙钟累计 | 0.285616秒 |
| host采样首点至末点 | 100.723秒 |

5次控制调用为上传采样包、等待就绪、阶段标记、结束回收及下载采样结果。不把与任务并行的`monitor-collect`约9.815秒等待算成额外串行成本。上述直接成本已经包含在各自运行窗口内，不能叠加成任务减速。host线程CPU约占其观测窗口的0.358%单核；guest采样器RSS包含共享映射，不是独占物理内存。

本轮没有关闭采样的一组，因此不输出新减速百分比；采样器回执也没有独立测量动作计时包装程序的成本。本轮证明完整代码任务可以使用新入口取得有效数据，不以一次运行确定稳定峰值或平均性能。

## 新入口保留原任务，关闭重复监控

```bash
# 源服务须已健康；入口不会启动或替换源服务。
export ASB_AGENTENV_SOURCE_SERVICE=asb-source-rebuilt-20261018T160925Z-2726362
bash scripts/run-host-file-monitor.sh \
  configs/platform-prettier14400-host-current.json --exp1-light
```

配置沿用旧Exp1的v1输入规范，固定AgentENV、2 CPU／4 GiB、完整26步、无checkpoint、0.1秒。须使用上述显式入口，不交给普通v2任务表。

- 外层先建统计组并启用内存／I/O记账，再准备缓存、创建私有服务和VMM；PSS及mincore关闭。
- 26个原动作内容与顺序不改，guest上传后再次核对冻结清单。008／016退出127、其余24步为0；原patch、024关键输出、SVG／嵌套SVG／HTML三种独立输入及格式化幂等性全部保持，官方4通过／0失败。
- guest只运行一个周期meminfo采样进程；原runner设置`GUEST_MEMORY_SAMPLING=0`、`DROP_GUEST_CACHES=0`，不启动旧host监控，不关闭默认DAMON。
- 一个常驻Python包装程序使用`time.monotonic_ns()`计时原bash动作，保留原timeout、cwd、stdout/stderr和退出码，不逐步启动Python读时钟。
- 停止并回收采样器后删除guest。自动guest报告、最终卷边界与实际可写文件清单检查均在本轮原生流程执行通过，随后生成host报告。不是检查器修复后补记整轮通过。

旧入口不带新选项仍保留原runner及采样行为。59项相关离线检查通过；本轮实际执行源码与保存的候选快照一致，执行期间未修改运行代码。

## 缓存来源和准备范围

当前重建源服务只保留依赖，没有image-cache目录。已有Prettier #6604备份的完整镜像清单同时包含#14400固定digest配置，因此复用`prepare_prettier6604_cache.py`准备只读RAM缓存，不下载新镜像。

备份来源仍标为#6604，新入口额外检查#14400固定digest配置。commits只读借用，写入进入本轮自有卷。缓存准备在统计组建立之后，纳入全流程current和创建前基线；约14.3 GiB基线不能解释为单个guest RAM。固定镜像、初始HEAD、原动作及官方检查来源见[输入核对](HOST_PRETTIER14400_INPUTS.md)。

## 首次失败、获准清理与重试

首次目录`.artifacts/host-exp1-light/20261028T060500Z-2048846/`在创建guest之前因服务标签检查错误失败，任务动作0次，准备阶段852个host点不作任务数据。实际Exp1约定是`agentenv.experiment=initial-smoke`，现已恢复，并在新模式核对本轮`agentsandboxbench.run_id`。原FAIL和源码保留。

用户授权后，重新确认无挂载、loop、使用者及关联容器，只删除`.artifacts/storage-isolated-service/20261028T060617Z-2049509/volume.ext4`这一份20 GiB失败卷，保留日志及旧CSV两份失败卷。凭据在`.artifacts/host-exp1-light-retry-20261028/deletion-receipt.json`。

重试启动前64.903 GiB可用，满足20 GiB卷启动前严格超过46 GiB的门槛；分配后观测最低44.901 GiB，结束64.900 GiB。重试成功卷已按既有生命周期删除，自有guest、私有服务、挂载、loop和准备缓存均回收，源服务恢复停止。本次没有暂存、提交或推送。

## 证据与本阶段结束条件

- [脱敏结果](evidence/host-prettier14400-light-20261028.json)。
- 实际执行及离线核对：`.artifacts/host-exp1-light-retry-20261028/`，`launch-state.json`和`analysis.log`均通过。
- 外层结果：`.artifacts/host-exp1-light/20261028T063439Z-2055110/`。
- 隔离结果与最终卷检查：`.artifacts/storage-isolated-service/20261028T063556Z-2055755/`。
- 原始任务：上述隔离目录下`exp1/raw/20261028T063603Z-2056467/`，对应检查位于`exp1/audit/`。

约定的一轮已经完成，停止追加实跑。后续[历史数据清单](HOST_CURRENT_TASK_INVENTORY.md)和[三个标准任务的新模式采集](HOST_CURRENT_PYTHON_TASKS.md)均已完成；旧PSS开启数据不与新关闭数据混算性能。下一步建议先离线接通同条件的采样关闭路径，再决定一对新模式开关对照，不先增加重复轮数。
