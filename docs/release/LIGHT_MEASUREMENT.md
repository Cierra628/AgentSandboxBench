# 轻量测量：三项曲线和采集成本

普通单路径任务默认只记录每次工具时延、guest已用内存和guest page cache。使用guest内已有Python，每个guest一个采样进程；启动时上传`guest_memory_collector.py`与`sandbox_runtime_monitor.py`，退出前停止采集并下载结果。不需要逐页扫描或定制内核，普通模式不启动host／VMM采集器。

## 指标的准确含义

| 量 | 获取方法 | 用途和限制 |
| --- | --- | --- |
| 工具时延 | 原执行器`steps.tsv`的`duration_ns` | guest命令执行经过时间；保留退出码，不含模型等待和host API开销 |
| guest内存 | `/proc/meminfo`的`MemTotal−MemAvailable` | 内核估算的已用量；不是非空闲页总量，也不能减去Cached来推算匿名页 |
| guest page cache | 同次读数的`Cached` | guest全局逻辑缓存，包含Shmem；不重复相加，不是每进程量或host物理驻留 |
| host file（显式开启） | 实验cgroup的`memory.stat:file` | 包含文件缓存与Shmem；覆盖准备、控制程序、服务和所有guest，不是任务专属 |

内存值以KiB读取、除1024转换为MiB。周期目标为0.1秒，启动和停止时也取样；实际间隔和缺口以记录为准。短于采样间隔的命令不保证有期间内存点，曲线最高值仅是观测峰值。

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

该对照不包含后来host驻留查询，也不能替代完整副本模式的开关对照。当前完整副本查询成本最高约2.34秒，另列在结果报告，不宣称整体减速率已测完。

## 在已准备主机上使用

```bash
# 仅检查本机输入；没有原任务资源会失败，不表示离线样例有问题。
bash scripts/use-platform.sh check configs/platform-sphinx7590.json --offline
# 实跑须先满足准备条件及授权。
bash scripts/use-platform.sh run configs/platform-sphinx7590.json
bash scripts/use-platform.sh report .artifacts/platform-tasks/<运行目录> \
  --output .artifacts/sphinx-light-report
```

另外两任务替换为`platform-prettier6604.json`、`platform-xarray6992.json`。普通配置可用`monitor_mode: off`关闭采样；`light_monitor: false`是旧完整采样，不是关闭。显式host文件记账用`run-host-file-monitor.sh`和相应`*-host-file.json`，不能直接启动旧VMM再搬入统计组。完整副本模式另见[实跑说明](PREPARED_HOST.md)。

主要输出：`overview.png/svg`、`tools.tsv`、`memory.tsv`和`summary.json`；原退出码、任务检查和恢复判定独立保留。
