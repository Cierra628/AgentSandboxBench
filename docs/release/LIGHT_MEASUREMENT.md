# 轻量测量：三项曲线和采集成本

普通单路径任务默认只记录每次工具时延、guest已用内存和guest page cache。使用guest内已有Python，每个guest一个采样进程；启动时上传`guest_memory_collector.py`与`sandbox_runtime_monitor.py`，退出前停止采集并下载结果。不需要逐页扫描或定制内核，普通模式不启动host／VMM采集器。

**最新开关结果：** [三任务各两对的统一报告](../../results/local-no-proxy-paired-summary-20261101/REPORT.md)提供12次运行、6对对照的时延、采样成本、内存量和21张图入口；逐对差值可从保存表格离线复算。当前无代理／持久化输入条件下，guest＋host current一起开关，结束后共同输入查询保留；Xarray获准退出差异、Python历史严格FAIL及原评测失败分别记录，不合算跨任务减速率。

此前[三任务各一轮汇总](../../results/local-no-proxy-three-tasks-20261031/REPORT.md)保留原九图及直接成本，不插入新配对。上述新批次与下文2026-10-19的旧guest-only开关批次分开使用。

## 指标的准确含义

| 量 | 获取方法 | 用途和限制 |
| --- | --- | --- |
| 工具时延 | 原执行器`steps.tsv`的`duration_ns` | guest命令执行经过时间；保留退出码，不含模型等待和host API开销 |
| guest内存 | `/proc/meminfo`的`MemTotal−MemAvailable` | 内核估算的已用量；不是非空闲页总量，也不能减去Cached来推算匿名页 |
| guest page cache | 同次读数的`Cached` | guest全局逻辑缓存，包含Shmem；不重复相加，不是每进程量或host物理驻留 |
| host总记账（显式开启，现主指标） | 实验cgroup的`memory.current` | 覆盖组内准备、控制程序、服务和所有guest；不包含组外页和未记账分配，详见[current方案](../records/HOST_CURRENT_PRIMARY.md) |
| host file（显式开启，组成指标） | 实验cgroup的`memory.stat:file` | 包含文件缓存与Shmem；不是guest Cached在host的对应页，也不是任务专属 |

guest的meminfo以KiB读取，除1024转换为MiB；host cgroup计数以字节读取，除2的20次方转换为MiB。周期目标为0.1秒，启动和停止时也取样；实际间隔和缺口以记录为准。短于采样间隔的命令不保证有期间内存点，曲线最高值仅是观测峰值。

时间横轴采用单调时钟，不受系统校时影响；不同会话按host记录的开始时间排列，真实采样空缺不插值连接。曲线覆盖采样就绪至任务检查结束、删除guest之前，包含等待和检查；多guest每条线代表一个guest／会话，不能直接相加当host物理量。

AgentENV默认回收保持开启，Cached可能因回收而下降；不同运行曲线并非必须一致。重复实验应使用同一配置、固定选样规则，并报告各轮及均值／波动，不能只挑近似单调上升的曲线。

## 已有三任务曲线

以下固定展示2026-10-19开关对照第一轮的开启组，不按峰值或耗时挑选。它们是普通单路径guest-only模式，和之后完整GRPO／BPO的host副本模式分开。

| 任务 | 工具数 | 周期点 | 已用内存峰值/MiB | Cached峰值/MiB |
| --- | ---: | ---: | ---: | ---: |
| Sphinx #7590 | 35 | 208 | 304.750 | 106.719 |
| Prettier #6604 | 29 | 214 | 611.422 | 186.039 |
| Xarray #6992 | 35 | 658 | 803.188 | 325.492 |

- [Sphinx三项曲线](../records/evidence/sphinx-light-100ms-20261019.png)
- [Prettier三项曲线](../records/evidence/prettier-light-100ms-20261019.png)
- [Xarray三项曲线](../records/evidence/xarray-light-100ms-20261019.png)

图上部为逐次工具时延，下部两幅分别为guest内存和Cached。它们不展示host物理共享收益；后者看[指定文件对照结果](RESULTS.md)。曲线原始逐点日志不在公开包内，图片复用已验证的原报告。

## 轻量不等于零成本

三任务各3对开关对照，共18次、594个原动作；9次开启组共3214个周期点，关闭组无采样。逐对先计算`(开启/关闭−1)×100%`再取中位数；不是用两列耗时的中位数相除。

| 任务 | 工具耗时配对变化中位数 | 三对范围 | 控制程序配对额外用时中位数 |
| --- | ---: | ---: | ---: |
| Sphinx | +3.19% | −1.76%～+4.27% | +2.852秒 |
| Prettier | −2.53% | −5.95%～+15.44% | +5.653秒 |
| Xarray | +2.07% | +1.95%～+2.50% | +3.885秒 |

负值只说明这批没有观察到一致减速，不表示监控让任务加速。控制程序额外用时包含准备、检查、监控启停／就绪／下载，不包括外层镜像缓存准备和私有服务部署。随包[九对原始数值摘录](../../examples/light-monitor-controls.json)可复核配对百分比；原任务失败保持，不能把测量通过改写成模型修复成功。

该对照不包含后来host驻留查询，也不能替代完整副本模式的开关对照。历史独立133文件组的查询合计约2.34秒；新版19文件组成本见下，新版133文件的真实查询尚未运行。

后续[离线分解与优化](../records/LIGHT_MONITOR_OVERHEAD.md)发现：Prettier直接采样控制实际为0.575～0.795秒，整轮几秒差值主要出现在采样开始前的冷启动。新版已将上传、初始化和收尾合并，阻塞调用10→5，采样及可靠回收保持。上表保留原版本的观察值，新版实跑结果如下。

[新版Prettier三对实跑](../records/LIGHT_PRETTIER_OPTIMIZED_CONTROLS.md)已完成，六次174动作及601个采样点通过。直接控制中位数0.422秒，历史值0.724秒；本轮开关对照工具变化中位数+2.05%、整轮+5.87%。采样器约占一个CPU核心的0.67%～0.86%，峰值RSS16.26～16.75 MiB。原三任务表保留历史版本，当前单沙箱可继续使用这一轻量方案，不将41.71%的直接控制耗时下降解释为整轮提速。

[多沙箱和host成本优化](../records/MULTI_HOST_OVERHEAD.md)另完成一组七沙箱GRPO：189动作／2166点保持，最终并发收尾0.514秒，原两次19文件host查询0.144秒。只改变轻量最终收尾与驻留位统计，PSS关闭、0.1秒及查询范围不变。它没有关闭测量对照，不能直接给出整体并发减速率。

[七沙箱关闭采样入口](../records/GRPO_MONITOR_OFF.md)使用`platform-prettier6604-grpo-light.json`与`platform-prettier6604-grpo-off.json`保留相同189动作，两组均不带host／PSS／物理查询；关闭组只保留工具表和完整任务审核，内存标为NOT_MEASURED。

[七沙箱三对真实对照](../records/GRPO_LIGHT_REPEATS.md)已完成六次1134动作、5767个开启样本；保留[第一对](../records/GRPO_LIGHT_PAIR.md)，再交替执行两对。控制程序整轮变化分别+10.18%／+2.60%／+2.20%，中位数**+2.60%（多0.771秒）**；创建至全部分支检查结束中位数+1.88%，原动作及下载窗口+1.31%，工具累计−1.06%（−1.88%～+3.53%）。负值不表示采样加速，第一对较大执行／下载增量也未在后两对重现。当前普通七沙箱可使用这一轻量方案，性能影响按三对范围报告，不套用单沙箱结果，也不外推到带host查询的完整副本模式。

[host驻留查询独立开关一对](../records/HOST_QUERY_CONTROL.md)另完成378动作／4638点，两组都保留guest／host周期采样、PSS关闭和48 GiB卷，仅切换P0／P1驻留查询。开启两阶段处理合计0.133678秒，关闭仍做计数与身份检查，处理合计0.001642秒且容量为未测。整轮36.946→40.189秒（+8.78%），同时冷启动请求跨度增加4.568秒、原动作／下载窗口缩短2.396秒；[离线日志分解](../records/HOST_COLD_WAIT_OFFLINE.md)将六个慢请求的主要等待定位在创建日志之前，具体原因仍未知，不将差值全归入查询成本。它验证了开关与初步差值，不能给出稳定的host查询减速率，也不是关闭全部host测量的对照。

## 在已准备主机上使用

```bash
# 当前本机实跑：须先满足源服务就绪、资源预算及授权；只复用本地材料。
export ASB_AGENTENV_SOURCE_SERVICE=asb-source-rebuilt-20261018T160925Z-2726362
bash scripts/run-host-file-monitor.sh configs/platform-sphinx7590-host-current.json \
  --persistent-input --local-no-proxy
# 对已保存结果离线汇总，不启动服务、查询驻留或联网。
bash scripts/summarize-local-no-proxy.sh --output .artifacts/local-no-proxy-summary-recheck
# 已有单轮原始结果也可离线生成自己的报告。
bash scripts/use-platform.sh report .artifacts/platform-tasks/<运行目录> \
  --output .artifacts/sphinx-light-report
```

另外两任务替换为`platform-prettier6604-host-current.json`、`platform-xarray6992-host-current.json`；上述三份开启配置均保留两端0.1秒采样，分别要求启动准备前磁盘严格>54／56／62 GiB（Prettier／Sphinx／Xarray），MemAvailable>64 GiB及各自原保护。固定源服务就绪方式、任务状态和证据见上述三任务汇总及各原报告；缺本地材料就停止，不下载或回退17891。

Prettier完整本地模式已接通并完成两对关闭／开启：使用`platform-prettier6604-host-current-off.json`，仍带`--persistent-input --local-no-proxy`；停止双端周期采样、边界读取和guest采样标记，运行内存未测，结束后输入查询保留。本批工具增加0.72%／5.37%，控制程序增加3.13%／6.34%，阶段成本和曲线见[真实四次报告](../../results/prettier-local-no-proxy-sampling-controls-20261031/REPORT.md)，接口范围见[方案与实现](../records/PRETTIER_LOCAL_NO_PROXY_SAMPLING_PLAN.md)。

Sphinx／Xarray本地关闭也已接通，使用各自`platform-*-host-current-off.json`和相同两个选项，104项相关离线回归通过。Sphinx已完成[两对四次真实对照](../../results/sphinx-local-no-proxy-sampling-controls-20261031/REPORT.md)，工具增加1.18%／0.71%，控制程序增加2.56%／5.67%；[Xarray四次](../../results/xarray-local-no-proxy-sampling-controls-20261101/REPORT.md)工具变化−4.44%／＋2.60%，控制程序−1.76%／＋1.74%，两对方向相反，原退出例外及945通过12失败保持。接口见[Python任务方案与实现](../records/PYTHON_LOCAL_NO_PROXY_SAMPLING_PLAN.md)。它们仍保留persistent-sampling来选择原重放路径，实际关闭由monitor-mode决定，结束后116文件查询保留。历史普通关闭保持原行为；只设`light_monitor: false`是旧完整采样，不是关闭。本机后续实际运行不走旧代理路径。显式host记账必须先建统计组再创建新沙箱，不能启动旧VMM后搬入。完整副本模式另见[实跑说明](PREPARED_HOST.md)。

主要输出：`overview.png/svg`、`tools.tsv`、`memory.tsv`和`summary.json`；原退出码、任务检查和恢复判定独立保留。
