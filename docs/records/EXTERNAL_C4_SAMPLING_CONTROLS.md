# C4两对采样开关对照完成：工具变化+9.30%与−2.70%

后续[Django独立两对](EXTERNAL_DJANGO_SAMPLING_CONTROLS.md)已完成，复用驱动并保留本页C4默认、旧结果及数值，不合并为通用减速率。下文描述本次C4批次。

## 四次原任务均通过，保留顺序和全部结果

2026-10-30按[已确认计划](EXTERNAL_C4_SAMPLING_PLAN.md)完成关闭→开启、开启→关闭两对四次。每次一个新2 vCPU／8192 MiB guest、新统计组及私有服务，串行使用32 GiB卷，原36步共144步，没有追加短测、重跑或额外轮次。

两种模式只切换host与guest的内存采样：开启为两端0.1秒；关闭不创建host线程、不上传或运行guest采样器，也不生成内存曲线。逐步计时、原业务输入／runner、输出下载、cgroup记账、磁盘保护及任务验收在两组都保留。

所有轮次的原退出序列、9898文件样本、33／34步内容往返／结构与分片约束、原语法检查、有效时间戳及资源清理均通过。预期非零保持：第3步=2，11／12／16／18步=1，其余0。没有执行官方隐藏测试。

固定同伴仓库提交为`cbe767b2aa23ceb59875b1cb0a7a6141c749312a`，沿用[`external-c4-light.json`](../../configs/external-c4-light.json)和固定镜像。每轮下载的36份脚本、9份payload及业务runner与原输入逐字一致；没有提前读取样本、清缓存、冻结、PSS、mincore或回收设置更改。

## 工具累计均值：关闭42.92秒，开启44.15秒

**先看[用时对照图](../../results/external-c4-sampling-controls-20261030/timings.png)**。下面均为“关闭／开启”的数值，行名保留实际先后顺序。变化率为`(开启／关闭−1)×100%`，负值保留。

| 配对与执行顺序 | 工具累计／秒 | 整段CLI／秒 | 冷创建／秒 | 控制程序整轮／秒 |
| --- | ---: | ---: | ---: | ---: |
| 第一对：关→开 | 39.882／43.591（+9.30%） | 39.972／43.699（+9.32%） | 158.864／163.952（+3.20%） | 200.102／209.458（+4.68%） |
| 第二对：开→关 | 45.959／44.717（−2.70%） | 46.085／44.826（−2.73%） | 125.120／128.933（+3.05%） | 172.511／175.404（+1.68%） |

开启组工具均值44.154035秒，关闭42.920646秒。两项配对变化的中位数为+3.2984%，范围−2.7020%～+9.2988%；这个中位数与“两个均值的比值”不是同一个统计量。

**本次没有得到方向一致的工具减速。** 关闭组本身从39.88变为45.96秒，相差6.08秒；两对的开启减关闭分别+3.71秒、−1.24秒。运行波动会明显影响这个最小对照，不能把配对差全部认作采样自身成本，也不把+3.30%作为固定减速率。按照计划，两对完成即停止，不为缩小范围继续加轮。

工具累计只加guest原命令用时；CLI另含命令传递与返回。冷创建含首次镜像获取、转换和guest启动，不代表host缓存已清空。控制程序整轮另含创建、准备、验收、采样管理／下载及删除。报告绘图在运行清理之后，不加入这项整轮时间；后台采集与业务重叠的时间也不额外加到整轮上。

### 逐步骤差异说明波动发生在哪里

保存的36步计时可直接分解总差，不需要再运行任务：

- 第一对工具多3.709秒，第26步多3.076秒、第27步多1.099秒，其他步骤有增有减。第26步是解包、原内容完整性和目录结构比较；第27步还包含再次解包／压缩等检查。
- 第二对工具少1.242秒，但第33步完整往返反而多1.441秒；第26步少1.245秒，27／32／25步也更快。

差异集中在原文件处理和校验步骤，但这些墙钟记录没有进一步分离CPU、调度和I/O等待，不指定某个内核机制为原因。完整逐步差和每轮原计时在[逐步骤数据](../../results/external-c4-sampling-controls-20261030/step-changes.json)及[数值摘要](../../results/external-c4-sampling-controls-20261030/summary.json)。

## 直接采样成本：CPU较低，阻塞管理调用约半秒

以下只列两次开启；关闭的内存和采样器CPU为未测量，不填零。

| 项目 | 第一对开启 | 第二对开启 |
| --- | ---: | ---: |
| host／guest采样点 | 2137 / 438 | 1790 / 447 |
| host线程CPU／秒 | 0.757830 | 0.520699 |
| guest采样器CPU／秒 | 0.358430 | 0.358151 |
| host约占单核 | 0.355% | 0.291% |
| guest约占单核 | 0.816% | 0.796% |
| host计数读取累计墙钟／秒 | 0.595720 | 0.413002 |
| 四个阻塞采样管理调用累计／秒 | 0.574925 | 0.470645 |
| 异步采集请求的存活墙钟／秒 | 44.170794 | 45.204193 |
| host最大采样间隔／秒 | 0.100132 | 0.100107 |
| guest最大采样间隔／秒 | 0.122004 | 0.131947 |

host CPU覆盖准备、任务和清理阶段，guest CPU覆盖其采样器运行期，窗口不同。这是相对单核可用时间的CPU占比，不是任务减速率。计数读取包含在线程工作内，不重复加到线程CPU上。

四个阻塞调用为采样代码上传、就绪、停止并回收、采样数据下载；已包含在控制程序整轮中。异步采集请求与业务重叠约44～45秒，不代表额外阻塞了同样长时间，也不是采样器CPU成本。

## 两次开启的内存曲线仍可使用

| 开启组 | host整组current峰值／MiB | guest内存压力峰值／MiB | guest Cached峰值／MiB |
| --- | ---: | ---: | ---: |
| 第一对 | 19028.715 | 1162.867 | 3110.805 |
| 第二对 | 18869.289 | 1346.891 | 2498.918 |

- 第一对：[host任务阶段](../../results/external-c4-sampling-controls-20261030/pair-1-on-host-task.png)、[工具及guest曲线](../../results/external-c4-sampling-controls-20261030/pair-1-on-guest.png)、[host全流程](../../results/external-c4-sampling-controls-20261030/pair-1-on-host.png)。
- 第二对：[host任务阶段](../../results/external-c4-sampling-controls-20261030/pair-2-on-host-task.png)、[工具及guest曲线](../../results/external-c4-sampling-controls-20261030/pair-2-on-guest.png)、[host全流程](../../results/external-c4-sampling-controls-20261030/pair-2-on-host.png)。

对照图及两组的guest／host任务图均已实际查看，PNG／SVG放在`.artifacts`外。关闭组没有内存曲线。

host current仍为统计组总记账，含准备、镜像／后备缓存、控制、服务与guest，不是guest独占RAM。guest内存压力为`MemTotal−MemAvailable`，Cached含Shmem，与host file不是同一层；以上峰值不用于计算共享率或关闭采样节省内存。

## 真正关闭已实现，旧入口默认开启保持

三个现有脚本新增显式选项，关闭仅支持C4：

- [`run_host_external_workload.py`](../../scripts/run_host_external_workload.py)：`--sampling off`不创建host采样线程，也不调用内存报告；systemd和内层模式一致。
- [`validate_isolated_agentenv.py`](../../scripts/validate_isolated_agentenv.py)：C4专用关闭选项与host状态、light标志必须一致；只放行此明确模式，原任务和资源条件保持。
- [`run_external_workload.py`](../../scripts/run_external_workload.py)：关闭时不创建`RuntimeMonitor`，无采样代码和管理调用；原业务输入、runner、计时及验收不变。

57项离线检查通过，包括6项新增、13项旧外部任务、11项Cython及27项隔离存储检查；使用替代CLI，不触碰真实服务。首轮正式关闭组即新路径的必要运行验证，通过后完成剩余三次，没有额外短测。

调用两对固定对照，需源服务处于原停止状态、固定镜像和本地同伴仓库可用，并满足资源门槛：

```bash
bash scripts/run-c4-sampling-controls.sh \
  --output .artifacts/c4-sampling-controls-new-run \
  --upstream .artifacts/workload-and-upper-20261030/upstream
```

该驱动只运行固定四次，复用原隔离生命周期。普通[`run-external-workload-light.sh`](../../scripts/run-external-workload-light.sh)不加新选项仍开启采样；Django／Cython关闭模式仍拒绝，避免把未经验证的范围顺手扩大。

## 资源、清理与证据

每次分配前均满足严格>58 GiB可用磁盘、MemAvailable>64 GiB；四次串行32 GiB定容卷，没有降低原240／720／1050／1200秒预算。观测磁盘最低31.078 GiB，是已分配32 GiB之后的剩余空间，不与分配前门槛混淆。

四次卷边界、只读根、可写文件检查及全部自有清理均通过。源服务恢复停止，无活动Docker容器或Firecracker；四份成功卷均清理，无新失败卷。最终可用约63.072 GiB。没有删除已有其他工作、暂存或提交。

- [脱敏结果证据](evidence/external-c4-sampling-controls-20261030.json)
- 实现前后快照、仅本轮差异及57项检查：`.artifacts/external-c4-sampling-implementation-20261030/`
- 四次命令、预算、原始路径、验收、比较与最终清理：`.artifacts/external-c4-sampling-controls-20261030/`
- [公开曲线和逐工具数据](../../results/external-c4-sampling-controls-20261030/summary.json)
- 只读生成报告：[`report_c4_sampling_controls.py`](../../scripts/report_c4_sampling_controls.py)，数据来源不触碰任务页。

## 下一步建议

本范围已完成，不追加C4轮数。若继续检查短任务上的开销，建议先离线确定Django的同口径开／关对照范围：保留原28步和源码／语法验收，核对是否可复用C4的关闭路径，以及工具和采样控制的计时边界；明确最小改动及轮数后再确认实跑，不把C4的百分比直接套过去。
