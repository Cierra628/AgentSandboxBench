# AgentSandboxBench

一个研究AgentENV沙箱内存的小项目：重放工具命令，画出时延、内存和page cache曲线，再比较多沙箱共享文件能省多少缓存。

## 先看看结果

仓库带了一份真实实验的脱敏数据，可以直接复算，不用启动沙箱。需要Linux和Python 3.12：

```bash
bash scripts/report-host-copy-summary.sh --example --output .artifacts/example-report
cat .artifacts/example-report/SUMMARY.md
```

输出目录需要是新目录。

- [三个任务的曲线和轻量测量方法](docs/release/LIGHT_MEASUREMENT.md)：Prettier、Sphinx、Xarray。
- [共享文件对照结果](docs/release/RESULTS.md)：Prettier完整GRPO／BPO各一对，七沙箱共享初始文件，相比七套独立副本，结束时分别少驻留约24.696／24.694 GiB。大部分差值在准备阶段就已形成，每种调度目前只有一对。

## 测了什么

| 指标 | 怎么测 |
| --- | --- |
| 工具时延 | 记录guest里每次原命令的执行时间和退出码 |
| guest内存和page cache | 默认每0.1秒读`/proc/meminfo`，画`MemTotal−MemAvailable`和`Cached`；Cached包含Shmem |
| host文件缓存记账 | 显式开启后读实验cgroup的`memory.stat:file`，覆盖准备程序、服务和所有沙箱 |
| 指定文件的host实际驻留 | 准备后和任务结束时用`mincore`查询驻留页，按设备号／inode去重，比较实际共享文件与独立副本 |

后两项和guest Cached不是同一层。共享收益只覆盖选定的初始文件，不代表整个host的内存节省。驻留查询不读取数据页或额外暂停guest；显式副本对照关闭PSS，保留AgentENV默认回收。

## 自己跑

完整实验需要先准备AgentENV服务、任务镜像和冻结轨迹，这些大文件和运行状态没有放进仓库。准备条件、后端补丁、构建和实跑命令都在[运行说明](docs/release/PREPARED_HOST.md)。目前验证过已有主机上的运行，还没有验证新机器从零部署。

只检查代码可以运行下面这条命令；图表测试需要[绘图依赖](requirements-release.txt)，不会启动真实沙箱：

```bash
bash scripts/check-release-offline.sh
```

[样例来源](examples/host-copy-summary/README.md)和[第三方说明](docs/release/THIRD_PARTY_NOTICES.md)保留了数据、许可等细节。凭据、镜像、数据库、本地实验产物和未脱敏日志不上传。
