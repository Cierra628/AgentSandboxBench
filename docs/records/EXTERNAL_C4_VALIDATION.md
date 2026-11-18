# C4轻量测量：一轮原36步完整验证通过

后续完成了独立的[两对host＋guest采样开关对照](EXTERNAL_C4_SAMPLING_CONTROLS.md)，新四次不混入本页首次成功单轮。

## 固定样本的内容往返、结构与分片约束通过

2026-10-30完成一个新AgentENV guest的C4重分片任务：2 vCPU、8192 MiB RAM、原36步，包含27步Bash和9步write。使用[固定配置](../../configs/external-c4-light.json)，逐步独立Bash、原工作目录、默认管道语义和umask 077保持，没有修改业务脚本。

36步退出码与固定参考一致：第3步退出2，11／12／16／18步退出1，其他为0。原第2步实际输出9898个输入文件。第33步本轮输出：

- 9898个文件封装为55个分片，合计818055644字节。
- `CONSTRAINT VIOLATIONS: NONE (all dirs <=30 entries, all files <=15MB)`。
- 解包恢复9898个文件，内容与结构比较后输出`BYTE-EXACT ROUNDTRIP ON c4_sample`。

第34步再次输出`9898 files, structure + content`，并确认输入仍有9898个文件。原结尾的两份脚本存在性及Python语法检查也通过。验收使用原步骤保存的输出，不在第35步清理后重做一次全量读取；这是固定样本的往返及约束检查，未执行官方隐藏测试。

## 工具约45.75秒，冷创建单独计时

| 用时 | 本轮结果 |
| --- | ---: |
| 36步工具累计 | 45.754192秒 |
| 首步开始到末步结束 | 45.805549秒 |
| 执行整段工具的CLI调用 | 45.858651秒 |
| 冷创建，含首次镜像获取、转换和启动 | 315.144841秒 |
| guest控制程序整轮，含准备、采样控制、检查和清理 | 362.645321秒 |

工具计时包括原脚本执行及少量计时调用开销；跨度另含段间日志与调度，CLI窗口另含命令传递和返回。冷创建和控制程序整轮不是任务执行时间，不能与工具累计重复相加。

## 两端内存及缓存曲线已生成

| 观测量 | 本轮结果／MiB |
| --- | ---: |
| guest `MemTotal - MemAvailable`峰值 | 1264.043 |
| guest `Cached`峰值，含Shmem | 2920.855 |
| host准备基线，尚未下载新镜像 | 397.082 |
| host全流程`memory.current`观测峰值 | 20284.461 |
| 该峰值相对准备基线的增量 | 19887.379 |
| host执行CLI窗口首／末采样 | 6184.715 / 18703.164 |
| host执行CLI窗口峰值 | 20284.461 |
| host全流程file峰值 | 16054.988 |
| host执行CLI窗口file／anon峰值 | 16053.320 / 3774.902 |

**看任务运行过程优先用[host任务阶段放大图](../../results/external-c4-light-20261030/host-task.png)**；它去掉了冷创建的长时间轴，但仍包含准备阶段留在统计组内的缓存。虚线界定host侧CLI开始和返回，不与guest时钟直接相减。

其他结果也在`.artifacts`外，PNG／SVG均保留，三张主要图已实际查看：

- [逐工具时延、guest内存和Cached](../../results/external-c4-light-20261030/guest.png)
- [host全流程及anon／file／kernel分项](../../results/external-c4-light-20261030/host.png)
- [host相对准备基线的变化](../../results/external-c4-light-20261030/host-increase.png)
- [逐工具用时](../../results/external-c4-light-20261030/tools.tsv)及[数值摘要](../../results/external-c4-light-20261030/summary.json)

### 三条曲线分别表示什么

host current为新统计组及其后代的**总内存记账**，覆盖准备、控制程序、私有服务和guest。8 GiB是guest RAM配置，不是整个host统计组的内存上限。因此约19.81 GiB的组峰值不表示8 GiB guest超配，也不表示Django或C4任务进程独占了这些内存。file是其中的文件记账部分；本轮没有逐对象mincore分解，不能把约16 GiB file全部归为C4样本或可共享文件。

guest的`MemTotal - MemAvailable`估计不可轻易回收的内存压力；`MemAvailable`已经考虑可回收文件缓存，因此这个数可以小于Cached，不能将二者简单相加，也不能用前者减Cached当作匿名页。guest Cached与host file属于不同层，不能直接相减计算节省。

本轮保留默认回收行为；曲线下降没有用DAMON或其他单一原因作归因。没有多沙箱或不共享对照，也没有逐页归属证据，本轮交付的是该任务的时延和两端轻量曲线，不计算共享收益或部署密度提升。

## 采样成本和覆盖通过核对

| 项目 | host | guest |
| --- | ---: | ---: |
| 名义间隔 | 0.1秒 | 0.1秒 |
| 有效点 | 3663 | 457 |
| 观测时长 | 366.402秒，首末点之间 | 45.969秒，采样器运行期 |
| 采样器CPU用时 | 1.263821秒，线程CPU | 0.339742秒，进程user+system |
| 约占一个CPU核 | 0.345% | 0.739% |
| 最大相邻采样间隔 | 0.100158秒 | 0.128257秒 |

host读取计数器累计墙钟0.994479秒。guest样本覆盖首工具前至末工具后，采样器退出并回收；host样本覆盖准备、执行及清理。没有PSS、周期mincore、冻结、全根扫描、额外预读或强制驻留。本轮没有关闭采样组，上述CPU比例不等于任务减速率。

## 固定镜像、资源与清理

固定同伴仓库版本仍为`cbe767b2aa23ceb59875b1cb0a7a6141c749312a`，主记录为`reshard-c4-data-20261025T054616Z-971984`。本机在分配卷前已实际取得固定镜像的manifest和config，确认linux/amd64、9层、压缩层合计1432498021字节；随后使用同一固定引用成功冷创建：

`docker.io/alexgshaw/reshard-c4-data@sha256:92d209e704785e17ad6b3f740e813dd72bc7ac33982ce435d197ab9d890eac2a`

启动前磁盘约63.210 GiB，严格>58 GiB，MemAvailable约479.186 GiB，>64 GiB。32 GiB定容卷及自有写边界检查通过；任务、两端采集、报告、离线验收和全部自有清理通过。源服务恢复停止，无本轮活动guest／服务，没有新失败卷；结束约63.204 GiB可用。没有删除其他内容或替换原服务。

本轮复用上一阶段40项有效本地检查，未改运行代码、未追加短测或重复。原始日志不上传，公开摘要只保留数值和必要的验收语句：

- [脱敏结果证据](evidence/external-c4-light-20261030.json)
- 本地驱动、镜像清单、源码快照及离线验收：`.artifacts/external-c4-live-20261030/`
- 工作流程：`.artifacts/host-external-workload/20261030T045232Z-2588870/`
- 原始任务：`.artifacts/storage-isolated-service/20261030T045232Z-2588875/external-workload/`
- 接入方式及复现命令：[离线实现报告](EXTERNAL_C4_IMPLEMENTATION.md)

## 下一步建议

Django和C4两个新任务已各有一轮有效轻量结果，Prettier upper扩展也已完成，不再为这些结果追加重复。下一步先离线核对`build-cython-ext`：固定镜像、83步输入及依赖、编译与两轮测试的验收范围、后台进程和资源预算。它补充编译型负载；完成条件是明确可复用入口和一轮最小实跑范围，不直接安装依赖或启动任务。
