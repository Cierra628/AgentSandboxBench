# 同伴使用平台：先离线检查，再准备实跑输入

2026-11-05。这个仓库提供AgentENV任务重放、内存采样和报告代码，不包含已运行的服务、镜像、凭据或本机实验输入。拉取更新后可以先验证代码；完整实跑需要下面列出的资源。当前实跑证据来自已有主机，尚未验证全新机器从零部署。

## 先验证这份代码，不启动沙箱

需要Linux、Python 3.12、Bash，以及`requirements-release.txt`中的绘图依赖。以下检查使用合成输入、模拟的服务调用及自有临时进程，不连接Docker、不创建沙箱、不下载镜像，也不要求root：

```bash
bash scripts/check-platform-offline.sh
```

正常结束会输出`status=PASS scope=platform-offline`。缺依赖或测试失败会非零退出，先处理具体错误，不把离线检查当成真实服务验收。检查会在仓库的`.artifacts/`写临时绘图缓存；普通用户运行即可。不要用全量`unittest discover`替代这条命令：部分历史测试需要本机保存的实验输入，`test_sandbox_runtime_monitor.py`中的两项还需要未捆绑的上游guest-runner源码。默认检查使用独立的合成meminfo采样用例，不把缺源码的检查记为通过。

本次同步面向直接使用Git仓库的同伴；`prepare-release.sh`的v0.1清单是此前发布范围，不作为近期全部任务和调度的同步清单。

## 已支持的任务，不等于任意任务和调度都已接入

| 任务 | 单路径动作数 | 当前可用范围 |
| --- | ---: | --- |
| Prettier #6604 | 29 | 单路径及七角色GRPO／BPO／本项目TVCache；最新统一四类指标已验证 |
| Prettier #14400 | 26 | 原Exp1入口及host current轻量入口；不是#6604的调度输入 |
| Sphinx #7590 | 35 | 单路径；保留原诊断和测试结果，不支持七角色调度 |
| Xarray #6992 | 35 | 单路径；保留原诊断和测试结果，不支持七角色调度 |
| Django #16819 | 28 | 外部固定轨迹单路径；原源码及语法检查 |
| C4 | 36 | 外部固定轨迹单路径；原9898文件内容／结构往返及约束检查 |
| Cython | 83 | 外部固定轨迹单路径；两种目录各18项测试及扩展导入检查；另有联网限制，见后文 |
| Matplotlib #24177 | 95 | 单guest固定轨迹；最终两轮各60 passed及完整补丁；PSS／P1等已验证 |
| 大规模文本编辑 | 50 | 单guest固定轨迹；两种solve方式及逐字节输出；PSS／P1等已验证 |

以上实跑证据按任务类型共8种，Prettier两个题号分开计为9项。新两项各一次的结果见[汇总报告](../results/external-matplotlib-text-20261105/REPORT.md)及[实现记录](records/EXTERNAL_MATPLOTLIB_TEXT_IMPLEMENTATION.md)；HF、PDF.js、Log4j2、token计数及CompCert由同伴负责。具体分工和数据要求见[多任务测量计划](records/WORKLOAD_MEASUREMENT_PLAN.md)。新增任务必须补执行和结果检查分支，不能只修改task_id或动作数。

看已有数据先打开[8种任务指标与曲线索引](../results/workload-metrics-index-20261105/REPORT.md)。它区分所选单guest和三调度、旧／新采集组合，列出PSS、指定文件P1和新版时间边界的缺项；不同批次不能拼成“一次全量采集”。同伴可据此判断哪些数据已可复用，而不是默认重跑全部任务。

五项旧任务的新版PSS／初始下层P1／任务窗口已补齐，入口和结果见[实现记录](records/TASK_METRICS_UPGRADE.md)及[五项汇总](../results/task-metrics-upgrade-20261105/REPORT.md)。旧索引所选记录保留，新结果单列。

## 实跑前必须准备的资源

1. **后端与权限。** Linux上的KVM、ublk、cgroup v2、systemd、Docker及所需挂载／loop权限；真实实验入口需要root。AgentENV固定源码和测量补丁的准备方法见[已准备主机说明](release/PREPARED_HOST.md)。
2. **自己的源服务。** 用`ASB_AGENTENV_SOURCE_SERVICE`指定自己管理的AgentENV源容器。常规隔离入口检查该容器正在运行，且镜像与`scripts/validate_isolated_agentenv.py`的`IMAGE`一致；私有服务在新cgroup中创建，不迁入池中的旧VMM。仓库不包含这个运行时镜像，更换镜像不能跳过兼容性验证。
3. **运行程序和服务依赖。** 常规任务入口需要本机`runtime/bin/aenv`，源容器需包含已准备的依赖。`runtime/`、服务凭据和数据库不随Git复制；不要把另一台机器的令牌写入配置或提交。`runtime/bin/aenv`缺失时，先完成对应AgentENV客户端准备，而不是直接执行实验脚本。
4. **原三任务固定输入。** `configs/platform-*.json`中的`inputs`、`reference`、`frozen_export`等指向原主机本地路径。需要私下取得对应冻结轨迹、检查参考及已验证卷备份，再在自己的一份配置中填写实际路径。它们不是Git拉取后自动存在的文件；不能拿短轨迹代替完整输入。
5. **外部任务输入。** 使用下面指定版本的同伴仓库，其脚本和配置记录固定镜像摘要；本机还需已有镜像缓存或可用直连。CompCert原配置为32 GiB，当前8 GiB受控创建入口不支持，需单独接入。
6. **空间和内存。** 启动前按入口检查实际负载及预算。例如外部任务32 GiB卷要求可用磁盘严格大于58 GiB，内存保护要求大于64 GiB；24 GiB卷要求可用盘大于50 GiB。不同调度或副本模式卷大小不同，以本次入口的检查为准，不降低门槛。失败现场保留，定向删除前确认授权、挂载／loop和使用者。

### 原三任务：先检查固定输入

下面的离线输入检查不创建服务，但要求配置指向的冻结输入已经存在：

```bash
bash scripts/use-platform.sh check configs/platform-prettier6604.json --offline
# Sphinx、Xarray分别使用platform-sphinx7590.json、platform-xarray6992.json。
```

输入和上述运行条件齐全后，普通guest轻量测量使用：

```bash
export ASB_AGENTENV_SOURCE_SERVICE=自己的源服务容器名
bash scripts/use-platform.sh run configs/platform-prettier6604.json
```

普通入口默认不增加host采样。若需要guest轻量采样和host总记账曲线，使用明确开启host current的配置和外层统计组入口：

```bash
bash scripts/run-host-file-monitor.sh configs/platform-prettier6604-host-current.json
# 另两项：platform-sphinx7590-host-current.json、platform-xarray6992-host-current.json。
```

这些是会创建私有服务和guest的真实实验命令，不属于本次离线同步检查。

### 外部任务：使用固定的上游版本

上游为`https://github.com/RyanShen30/AgentSandboxWorkloadResults`，固定提交`cbe767b2aa23ceb59875b1cb0a7a6141c749312a`。取得对应副本后，用其真实路径作为第二个参数。不要使用更新后的默认分支而不核对输入和退出序列。

```bash
export ASB_AGENTENV_SOURCE_SERVICE=自己的源服务容器名
env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
    -u http_proxy -u https_proxy -u all_proxy \
  bash scripts/run-external-workload-light.sh \
    configs/external-c4-light.json /本机路径/AgentSandboxWorkloadResults
# Django使用configs/external-django16819-light.json。
```

Django／C4的此入口创建新的外层统计组、私有服务及单guest；host current／file与guest meminfo均0.1秒采样，关闭PSS和mincore，保存原逐工具计时。结果目录由终端的`result_dir`给出，摘要列明原始数据和图表路径。

**新两任务：**将上述配置替换为`configs/external-matplotlib24177-light.json`或`configs/external-text-editing-light.json`，上游版本相同。它们另开单VMM PSS周期1秒及终点查询、实际初始下层文件一次mincore P1，保存查询成本；成功报告另存到新的`results/external-<task_id>-<run_id>/`。新两项会清空各层代理并使用明确长超时，不改变旧三任务默认值。本机两项各一次完整验证已通过；其他主机仍须准备自己的输入和服务。已有完整OCI输入时追加`--oci-input /本机路径/输入目录`，其中包含`request.json`、原始`requested-manifest.json`及`oci/`；入口核对固定摘要和平台，只读挂载并关闭远程回退。镜像不随Git复制，本次17891下载例外不作为其他机器的默认联网设置。

**Cython的旧入口有额外限制：**它依赖显式提供的本机HTTP代理和临时guest转发来安装依赖，不能直接套用上面的无代理命令。该逻辑一并保留，不代表当前无代理运行已通过；本项目禁止使用17891，不自动选择其他代理。要在当前网络规则下重新运行，应先单独确认本地依赖准备或直连适配方案。Django／C4若缺本地镜像，直连失败就停止，不回退代理。

### 旧任务显式开启新版采集

Sphinx／Xarray使用`platform-sphinx7590-endpoint-metrics.json`或`platform-xarray6992-endpoint-metrics.json`，通过原host外层入口并加`--persistent-input --local-no-proxy`。仍需准备对应持久输入。

Django／C4／Cython在原外部任务命令中显式加`--endpoint-metrics`，开启单VMM PSS周期1秒和终点查询、实际下层文件一次P1及精确任务窗口；普通计数仍0.1秒，原入口不加参数时保持原行为。固定本地OCI可配`--oci-input`。

Cython新版默认无代理；直连不通不能自动回退。只有已有明确授权时，才可显式添加`--guest-relay-proxy http://127.0.0.1:端口`，接入原临时guest转发；结束删除本次进程、临时地址和规则，不改全局设置。本次17891授权仅限这一份Cython验证，不作为同伴的默认授权。

### 三调度的四类指标

Prettier #6604的GRPO／BPO／TVCache配置分别为`configs/platform-prettier6604-{grpo,bpo,tvcache}.json`。GRPO实际执行189动作；BPO／TVCache复用前缀，实际84、逻辑189动作，各保存2次、恢复6次；TVCache是确定性前缀树顺序执行，不是动态缓存淘汰。

`scripts/run-schedule-smoke.sh`是原主机三调度验证批处理，绑定了`run_light_monitor_controls.py`中的源服务名称、容器ID及原备份路径；它不是可直接换台机器运行的通用入口。另一个主机先准备自己的输入和服务，使用原配置生成的隔离命令，并显式增加`--schedule-smoke-metrics`；不要通过删掉容器身份检查来套用旧批处理。该选项只支持完整监控的Prettier七角色三调度，不接受轻量模式。

## 采集量、时点与边界

- **普通轻量路径：**0.1秒读取guest meminfo，画`MemTotal−MemAvailable`及`Cached`；Cached含Shmem。显式host current路径读取实验cgroup的`memory.current`和`memory.stat`的file／anon／kernel／shmem等字段。外部任务的旧轻量入口没有PSS和文件驻留，不能补称四类量已齐全。
- **三调度四类量：**普通计数0.1秒、周期PSS1秒；原动作和下载完成后、官方检查和删除之前，额外一次七个实际VMM的PSS求和／7，以及按设备号:inode去重的19个初始下层文件mincore驻留。保存查询窗口及成本；其他任务不能硬套19个文件。
- **时间：**主任务窗口从调度开始、尚未创建guest，到原路径及下载返回；逐工具时间按guest单调时钟差值累计。控制程序整轮另含检查和清理，不能当纯工具时间；并发累计也不等于墙钟。
- **物理意义：**current是组内总记账；PSS是VMM映射驻留的共享分摊；file是组内文件类记账；mincore是指定文件的全局驻留；guest Cached是guest层缓存。它们不是同一个量，不相加构造总物理内存。mincore大于file时记录覆盖不足，净差不当组外容量或共享节省；未覆盖upper等对象保持未知。

首次新任务各完整运行一次，先确认原动作、预期非零退出、关键输出、采集和清理，不默认启动重复矩阵。每次用新结果目录，失败保留原路径，检查器错误先用已保存日志排查，不静默重跑。
