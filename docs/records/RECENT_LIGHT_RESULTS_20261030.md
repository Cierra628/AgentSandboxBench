# 近期轻量实验情况（2026-10-30）

## 新增三个任务的有效曲线，Prettier驻留范围已扩大

近期完成了Django、C4和Cython各一轮新任务轻量测量，以及Prettier完整GRPO的upper及更多普通文件驻留扩展。已有Prettier、Sphinx、Xarray完整轻量结果继续保留，没有为任务扩展重复运行它们。

新任务仍采用host／guest每0.1秒采样、默认回收；host主指标为`memory.current`，file／anon／kernel作分项，guest读取meminfo。没有PSS、周期mincore、冻结、根扫描或强制驻留。工具、冷创建和控制程序整轮用时分别记录。

## 三项新任务：本机真实结果

内存单位为MiB；峰值均为采样观测值。guest内存列为`MemTotal - MemAvailable`，Cached包含Shmem。

| 任务 | 工具累计／秒 | guest内存峰值 | guest Cached峰值 | host全流程current峰值 | host执行阶段current峰值 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Django，28步／1 guest | 10.812 | 257.219 | 65.211 | 8239.926 | 6744.074 |
| C4，36步／1 guest | 45.754 | 1264.043 | 2920.855 | 20284.461 | 20284.461 |
| Cython，83步／1 guest | 228.723 | 718.879 | 230.348 | 4769.098 | 4769.098 |

- **Django**：原退出序列及原语法检查通过，未执行完整Django测试套件。有效结果来自计时权限问题修复后的那一轮；此前缺时间戳的执行不计入有效时延。
- **C4**：原退出序列保持，9898文件内容往返、目录结构、分片约束及原语法检查通过；没有运行官方隐藏测试。一轮直接通过，没有追加重复。
- **Cython**：原83步退出序列、两种目录各18通过、NumPy2.3.0和三个编译扩展通过。前两次出网失败未进入业务，不纳入有效曲线；nft修正后一轮完整通过。

| 计时或成本 | Django | C4 | Cython |
| --- | ---: | ---: | ---: |
| 冷创建／秒，含首次镜像获取与转换 | 210.777 | 315.145 | 49.189 |
| guest控制程序整轮／秒，含准备和清理 | 224.755 | 362.645 | 287.189 |
| host采样CPU约占单核 | 0.285% | 0.345% | 0.293% |
| guest采样CPU约占单核 | 1.321% | 0.739% | 0.499% |
| host／guest有效采样点 | 2296 / 113 | 3663 / 457 | 2906 / 2279 |

host执行阶段由CLI请求开始至返回界定，仍包括服务、控制及准备留下的缓存。host全流程还包括首次镜像下载和转换；它不是任务进程独占量，也不受guest的8 GiB RAM配置限制。因此不能从这些绝对值直接推算部署密度或共享节省。

guest `MemTotal - MemAvailable`考虑了可回收文件缓存，可以小于Cached，两者不能简单相加或相减求匿名内存。host file和guest Cached又是不同层；mincore和current存在重叠，不作额外加项。

上述首次单轮没有关闭采样对照，CPU占比不是任务减速率。Django和C4后来分别完成了独立两对开／关对照，见下节；Cython尚无关闭采样组，三项都未补同调度多沙箱共享对照。

### 图与报告

- Django：[工具及guest曲线](../../results/external-django16819-light-20261030/guest.png)、[host任务阶段](../../results/external-django16819-light-20261030/host-task.png)、[完整报告](EXTERNAL_WORKLOADS_LIGHT.md)。
- C4：[工具及guest曲线](../../results/external-c4-light-20261030/guest.png)、[host任务阶段](../../results/external-c4-light-20261030/host-task.png)、[完整报告](EXTERNAL_C4_VALIDATION.md)。

- Cython：[工具及guest曲线](../../results/external-cython-light-20261030/guest.png)、[host任务阶段](../../results/external-cython-light-20261030/host-task.png)、[完整报告](EXTERNAL_CYTHON_LIGHT.md)。

图均已放在`results/`，不必进入`.artifacts`加载原始目录。

## Django新增两对开／关对照：四次原28步通过

另行完成关→开、开→关四次／112步，不合并首次单轮。工具累计关闭／开启11.060／12.112秒（+9.51%）、10.741／10.353秒（−3.62%）；开启均值11.232秒、关闭10.900秒，配对中位数+2.948%不作固定减速率。整轮增幅+10.66%／+39.32%也保留，第二对主要多在创建调用。

采样CPU约单核host0.287%～0.334%、guest1.269%～1.419%，四个阻塞采样管理调用0.394～0.399秒。创建阶段host采样已运行、guest采样未开始，镜像准备内的网络／转换／host采样影响尚未分离。任务、采集、报告和全部自有清理通过，无新失败卷，不追加轮数。

[完整报告](EXTERNAL_DJANGO_SAMPLING_CONTROLS.md) · [用时图](../../results/external-django-sampling-controls-20261030/timings.png) · [数据摘要](../../results/external-django-sampling-controls-20261030/summary.json)。

## C4新增两对开／关对照：四次全部通过

另行完成关闭→开启、开启→关闭四次新guest／144步，不用首次45.754秒单轮充当新开启组。工具累计关闭／开启分别39.882／43.591秒（+9.30%）、45.959／44.717秒（−2.70%）；两对方向不一致，配对中位数+3.298%不作为固定减速率。开启均值44.154秒、关闭42.921秒，完整原任务及两端开关真实性、报告、卷边界与清理通过。

采样CPU约单核host0.291%～0.355%、guest0.796%～0.816%；四个阻塞采样管理调用0.471～0.575秒，与业务重叠的采集存活时间不加作阻塞。关闭内存未测量，不记0。两组开启的内存曲线及[用时图](../../results/external-c4-sampling-controls-20261030/timings.png)已在results。

[完整开销报告](EXTERNAL_C4_SAMPLING_CONTROLS.md) · [数据摘要](../../results/external-c4-sampling-controls-20261030/summary.json)。本范围完成后停止，不追加轮数以缩小波动。

## Prettier GRPO：19个lower扩大为211个唯一普通文件

原7个guest、189步保持，每实例原评测仍为0通过／2失败。原19个lower驻留5308.066 MiB；新增192个唯一普通文件驻留1788.129 MiB，合计211个、7096.195 MiB。其中upper数据／索引14个、68.594 MiB。

新增范围包括upper、其余镜像缓存、依赖、日志和元数据，按设备号＋inode去重。新增枚举及驻留查询约0.050724秒，全部指定对象查询约0.340877秒，前者已经包含在后者中。

这是任务结束时的指定普通文件驻留，不是整个host内存，也不是文件共享收益。未覆盖的可写设备块缓存、VMM中的guest RAM、已删除仍打开的文件、外层loop缓存及私有服务目录外文件继续单列未知。沿用原GRPO的RAM输入准备，不能与新任务单guest的绝对current混算。

[文件分类图](../../results/prettier-grpo-expanded-20261030/file-cache-categories.png) · [完整报告](PRETTIER_GRPO_EXPANDED_FILES.md)。

## Cython代理问题已收尾

前两次止于出网检查，均未开始原83步。临时规则第一次使用了主机默认legacy工具，而固定服务使用nft；已修正并通过真实HEAD，nft允许规则计数0→21后才运行任务。两份失败卷经用户分别确认后定向删除，卷外记录保留；成功轮的卷和全部运行资源均按原流程清理。两套表均无本项目临时规则残留。

本阶段三项新任务的有效单轮已齐全，Django／C4各有两对独立开／关对照。下一步若研究创建等待，只离线整理现有镜像阶段证据及缺项，不增加完整任务轮数，也不把Cython动态网络安装差异混作采样成本。

[首轮记录](EXTERNAL_CYTHON_VALIDATION.md) · [代理修补](EXTERNAL_CYTHON_RELAY_FIX.md) · [Cython有效结果](EXTERNAL_CYTHON_LIGHT.md) · [近期结果来源](evidence/recent-light-results-20261030.json)。
