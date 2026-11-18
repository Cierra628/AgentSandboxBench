# Prettier #6604轻量host采集：一轮完整验证（2026-10-28）

**新公共入口已完成原29步、guest／host采样、自动报告、最终卷检查及清理。** 原动作、关键输出、补丁和独立检查通过；官方仍为0通过／2失败，与冻结参考一致，任务修复结果未改变。PSS和mincore实际未请求，默认回收保持，没有模型请求、快照或额外实验组。

## 查看本轮曲线

- [host运行增量](../../results/host-current-prettier6604-20261028/host-increase.png)：相对创建guest前精确基线，放大创建、任务、检查及清理期间的变化。
- [host全流程与组成](../../results/host-current-prettier6604-20261028/host.png)：包含准备成本，current为主，anon／file／kernel解释组成。
- [工具时延及guest曲线](../../results/host-current-prettier6604-20261028/guest.png)：29步时延、guest非可用内存及Cached。
- [工具用时表](../../results/host-current-prettier6604-20261028/tools.tsv)。同目录有SVG，三张PNG均已实际查看；不需要打开`.artifacts`图片列表。

| 本轮观测 | 数值 |
| --- | ---: |
| 工具累计 | 12.653秒 |
| 冻结回放墙钟 | 14.973秒 |
| 任务控制程序整轮 | 25.915秒 |
| guest采样点／最大间隔 | 213／103.797毫秒 |
| guest非可用内存峰值（MemTotal−MemAvailable） | 615.082 MiB |
| guest Cached峰值（含Shmem） | 255.797 MiB |
| host采样点／最大间隔 | 1119／100.178毫秒 |
| 创建guest前current基线 | 14639.289 MiB |
| 全流程及服务就绪后current观测峰值 | 15564.734 MiB |
| 服务就绪后峰值相对基线的增量 | 925.445 MiB |

这是#6604新模式的一份有效精确基线，早期缺基线的#6604记录保持原样，不追溯补数。当前基线已经包含只读RAM镜像、缓存准备和私有服务；约14.3 GiB不是单个guest RAM。增量仍含后续控制和服务变化，不是任务进程独占内存。

host窗口从输入准备到采样结束，guest窗口从采样就绪覆盖原动作和后续检查。控制程序整轮还包含创建、上传、采样管理、验证、下载和删除guest，不含前置镜像准备或外层最终卷检查。工具累计与控制程序整轮之差不能全部归给采样。增量图尾部约−251.090 MiB在清理阶段，不作为任务结束前端点。

current是实验组及后代的总记账，不保证覆盖组外页和未记账分配；PSS、mincore、file分项不能加到current上，guest Cached与host file也不是同一层。完整定义见[current方案](HOST_CURRENT_PRIMARY.md)。本轮单沙箱不输出共享率或部署密度。

## 直接采集成本

| 项目 | 数值 |
| --- | ---: |
| guest周期采样器CPU | 0.158341秒 |
| guest采样器运行窗口 | 21.232秒 |
| guest采样器最大RSS | 17140 KiB（16.738 MiB） |
| 5次同步采样管理调用累计 | 0.445082秒 |
| host采样线程CPU | 0.460772秒 |
| host计数读取墙钟累计 | 0.361373秒 |

5次管理调用为上传包、等待就绪、阶段标记、结束回收和下载。与任务并行的采集命令等待约21.408秒，不作为新增串行成本。guest CPU只覆盖采样进程自身，不含任务runner的边界date／awk等命令；RSS也不是独占物理内存。CPU、读取用时和控制调用分别报告，不相加成减速率。

本轮没有关闭采样的一组，直接成本不等于完整任务减速；旧PSS开启记录不与本轮混作开关对照。功能、采集及清理已经通过，需要量化新版总减速时另定同任务对照。

## 实际执行条件和检查

```bash
export ASB_AGENTENV_SOURCE_SERVICE=asb-source-rebuilt-20261018T160925Z-2726362
bash scripts/run-host-file-monitor.sh configs/platform-prettier6604-host-current.json
```

固定AgentENV、2 CPU／4096 MiB、8 GiB自有卷、单guest、无checkpoint。源服务按已固定的容器身份启动检查，整轮后恢复停止；没有替换服务或二进制。统计组在准备之前建立，私有服务及新VMM从创建时就在组内；创建前确认无旧Firecracker或预启动池，并保存实际I/O控制器状态和同步内存计数。

沿用已验证备份准备缓存，旧冻结动作和guest采样器不改，没有清空缓存、暂停采集、预读任务文件、强制驻留或改变DAMON。原任务结果、采样回收、自动guest／host报告、实际可写文件清单、只读根限制、固定卷容量和资源边界全部在本轮原生流程通过。mincore为NOT_REQUESTED且无清单文件，PSS为NOT_REQUESTED且无扫描文件。

本轮执行源码与启动前保存的9份候选文件一致；直接复用此前51项离线检查，执行期间没有修改运行代码。原始memory.tsv独立复算的峰值和增量与自动报告一致；没有重跑任务。

## 清理和磁盘情况

用户本次要求清理磁盘后，只删除两处无挂载、无打开文件的可重建Cargo输出：

- `.artifacts/host-full-grpo-build-20261024/target`
- `.artifacts/release-prep-v0.1-20261025/backend-build/target`

共2.514 GiB，目录外的可用二进制、源码、工具链、依赖缓存、日志及实验数据保留；以后构建需要重新编译。此前已获准删除的两份旧8 GiB失败卷另有收据，不重复计入本次2.514 GiB。[清理说明](SPACE_CLEANUP_20261028.md)。

共享盘随后空间恢复，启动前53.292 GiB，满足严格超过34 GiB；本轮观测最低40.776 GiB、结束48.712 GiB。共享盘的其他变化不归因于本次清理或实验。成功卷、guest、私有服务、挂载、loop及准备缓存已回收，没有新失败卷，源服务恢复停止。

## 证据和下一步

- [脱敏汇总](evidence/host-current-prettier6604-20261028.json)。
- 启动、清理及离线核对：`.artifacts/host-current-prettier6604-launch-20261028/`。
- 主工作流程：`.artifacts/platform-tasks/20261028T094233Z-2168794/`。
- 原始任务与最终卷检查：`.artifacts/storage-isolated-service/20261028T094351Z-2169958/`，任务在其`prettier6604/`中。
- 自动报告：`.artifacts/platform-tasks/report-20261028T094233Z-2168794/`。

约定的一轮完成，停止追加。后续Sphinx／Xarray同一轻量选项的各35步[现已完成](HOST_CURRENT_PYTHON_TASKS.md)，三个标准任务的数据齐全。随后已[离线接通同条件关闭两端采样的对照路径](HOST_CURRENT_SAMPLING_CONTROL.md)，随后[关闭→开启首对58动作](HOST_CURRENT_SAMPLING_PAIR.md)已完成，工具及回放耗时、控制程序与冷启动分别记录；重复范围另定。
