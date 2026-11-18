# 新仓库任务的轻量测量：Django首轮完成

后续已另行完成[Django两对两端采样开关对照](EXTERNAL_DJANGO_SAMPLING_CONTROLS.md)，不混入本文首次有效单轮。其他新任务的最新情况见[近期汇总](RECENT_LIGHT_RESULTS_20261030.md)。

## 已取得28步时延和两端内存曲线

2026-10-30完成同伴仓库的Django #16819：一个新guest、2 vCPU、8192 MiB RAM，原28次Bash调用顺序和退出码全部保持，包括第17步预期退出1。原源码检查和`python -m py_compile django/db/migrations/operations/models.py`通过；原仓库没有在这次重放中执行完整Django测试套件。

沿用本项目的轻量采样：guest读取`/proc/meminfo`，host读取新统计组的`memory.current`和`memory.stat`，均每0.1秒。没有PSS、周期mincore、冻结、guest根扫描或强制驻留，未改变默认回收设置。本轮是单沙箱接入验证，没有测跨沙箱共享收益。

| 指标 | 本轮有效结果 |
| --- | ---: |
| 28步工具累计 | 10.811649秒 |
| 首步开始至末步结束 | 10.852950秒 |
| 执行整段脚本的CLI调用 | 11.048630秒 |
| 冷创建，含镜像获取、转换及guest启动 | 210.777209秒 |
| guest控制程序整轮，含准备、冷创建、采样控制和清理 | 224.754803秒 |
| guest `MemTotal - MemAvailable`峰值 | 257.219 MiB |
| guest `Cached`峰值，包含Shmem | 65.211 MiB |
| host准备基线，尚未开始获取新镜像 | 396.348 MiB |
| host全流程`memory.current`观测峰值 | 8239.926 MiB |
| 该峰值相对准备基线增量 | 7843.578 MiB |
| host任务执行CLI窗口首／末采样 | 6667.363 / 6744.074 MiB |
| host任务执行CLI窗口观测峰值 | 6744.074 MiB |

工具累计只加28段原脚本的执行区间，不含冷创建。两个计时程序读数之间包括脚本执行及少量计时调用开销。脚本跨度还包括段间调度和写日志；CLI窗口另含命令传递和返回。控制程序整轮包含上述环节，不能将224.75秒当成任务执行时间。

host主指标是**整个统计组的内存记账**，包含镜像准备、控制程序、私有服务和guest。准备基线在镜像下载之前，峰值增量因此包含下载与转换产生的缓存，不是Django任务进程独占内存。任务窗口两端采样相差约76.711 MiB，也包含同期服务与控制变化。

## 看任务变化用放大图，看冷启动用全流程图

图已实际查看，均放在`.artifacts`外：

- [逐工具时延、guest内存和Cached](../../results/external-django16819-light-20261030/guest.png)
- [host任务执行阶段放大图](../../results/external-django16819-light-20261030/host-task.png)：虚线为host端CLI执行请求的开始和返回，两条时间轴与guest单调时钟分别使用，不直接相减。
- [host全流程及anon／file／kernel分项](../../results/external-django16819-light-20261030/host.png)：包含首次镜像准备及清理，末尾下降是资源释放。
- [相对准备基线的host变化](../../results/external-django16819-light-20261030/host-increase.png)
- [逐工具数值](../../results/external-django16819-light-20261030/tools.tsv)及[完整摘要](../../results/external-django16819-light-20261030/summary.json)

host任务窗口的file峰值6052.707 MiB、anon峰值483.262 MiB；全流程file峰值7970.461 MiB。这里file包含不同缓存对象，不能与guest Cached视为同一层。本轮没有逐文件驻留查询，也没有据它计算物理去重或共享率；Prettier的upper扩展结果见[独立报告](PRETTIER_GRPO_EXPANDED_FILES.md)。

## 采样成本和有效覆盖

| 采集项 | host | guest |
| --- | ---: | ---: |
| 有效采样点 | 2296 | 113 |
| 观测时长 | 229.626秒，首末点之间 | 11.160秒，采样器运行期 |
| 采样器CPU用时 | 0.654369秒，线程CPU | 0.147417秒，进程user+system |
| 约占一个CPU核 | 0.285% | 1.321% |
| 最大相邻采样间隔 | 0.100168秒 | 0.101055秒 |

host读取计数器的累计墙钟为0.516961秒。上述CPU比例分别除以各自观测时长，不代表任务减速率；本轮没有关闭采样对照。guest样本覆盖首工具前到末工具后，采样器已正常退出并回收；host样本覆盖准备、任务和清理。

## 固定来源与原任务语义

来源为[AgentSandboxWorkloadResults固定提交](https://github.com/RyanShen30/AgentSandboxWorkloadResults/tree/cbe767b2aa23ceb59875b1cb0a7a6141c749312a)，版本`cbe767b2aa23ceb59875b1cb0a7a6141c749312a`。其10类任务包括Cython、C4重分片、HF推理、PDF.js、Log4j、文本编辑、token统计、Matplotlib、Django和CompCert；C4已完成[一轮真实轻量验证](EXTERNAL_C4_VALIDATION.md)；其余8项尚未接入本项目。

本轮只复用公开固定镜像、28份实际脚本和参考退出序列，不采用[原采集协议](https://github.com/RyanShen30/AgentSandboxWorkloadResults/blob/cbe767b2aa23ceb59875b1cb0a7a6141c749312a/experiments/agentenv-memory/README.md)的冻结与guest根扫描。逐份核对`replay-inputs.json`中实际脚本的原完整性字段，不将`command`元数据重新拼成脚本；在一个持续Bash中执行，保留工作目录、变量及默认管道退出语义。源码检查、独立语法验证及持续Shell均使用原`safe.directory=/testbed`。

固定镜像为`docker.io/swebench/sweb.eval.x86_64.django_1776_django-16819@sha256:6c46d22aaf692840f16209fe29b1d0c940bed043fce8047eb12169215222f044`。AgentENV本轮实际解析到linux/amd64清单`sha256:63e2e4d9ac42c73b2504606f895d6030d735885e9c0bacc8a2add11c86166771`；10层压缩总量约1184.575 MiB。新服务使用32 GiB定容卷，分配前仍要求可用磁盘严格>58 GiB及MemAvailable>64 GiB。

## 已处理的失败与本轮收尾

此前两次尝试分别停在统计组前缀检查、私有服务直连Docker Hub连接重置；均没有业务调用。前缀已遵守现有`asbhost-*`规则；仅新外部任务按变量名将已有代理从Shell经systemd转发到私有服务，代理值不写入命令收据。实际新服务的镜像获取已通过，不再只是主机清单测试。

用户确认后，只删除获准的旧32 GiB失败卷`.artifacts/storage-isolated-service/20261030T033634Z-2561206/volume.ext4`，释放约32.000 GiB，可用31.226→63.226 GiB；删除前核对身份、无挂载／loop／使用者，卷外失败日志保留。

代理修补后的第一次完整执行，28步及语法检查通过，但上传不保留计时程序的执行权限，56次计时调用均报`Permission denied`，逐工具时间戳为空。原自动结果错误地将空时间换算为0并报告成功，离线验收发现后改判为**任务通过、时延采集失败**；原始文件和错误图保留，不与本轮合并，也不使用零用时结论。

现已在上传后设置自有计时文件的执行权限并试读一次；任一计时调用失败即退出，返回后还核对时间戳、顺序和差值。7项纯本地测试通过，包括缺执行权限时在任务前退出、恢复权限后持续Shell及预期非零退出、空或错误时间拒绝通过。随后只重跑一次，取得本文有效数据。

有效轮的任务、采集、报告、32 GiB边界和自有清理全部通过。两次已完成执行的卷均自动释放，源服务恢复停止，无本轮活动guest／服务或新失败卷；结束可用约63.214 GiB。原始错误PASS记录与离线改判一起保留，不改写原始日志。

- [有效轮脱敏证据](evidence/external-django16819-light-20261030.json)
- [原镜像传输失败与获准清理记录](evidence/external-workload-django-20261030.json)
- 有效驱动与离线验收：`.artifacts/external-django-live-20261030-clock/`
- 有效原始结果：`.artifacts/storage-isolated-service/20261030T041039Z-2573901/external-workload/`
- 时延采集失败轮：`.artifacts/external-django-live-20261030-proxy/`

## 复现与下一步

以下命令会真实创建私有服务和沙箱；固定源服务须已健康、空间与内存须满足上述门槛。上游目录须切换到固定提交。

```bash
export ASB_AGENTENV_SOURCE_SERVICE=asb-source-rebuilt-20261018T160925Z-2726362
bash scripts/run-external-workload-light.sh \
  configs/external-django16819-light.json \
  .artifacts/workload-and-upper-20261030/upstream
```

Django首轮接入和Prettier upper扩展均已完成，本轮停止实跑。C4已完成[一轮真实验证](EXTERNAL_C4_VALIDATION.md)，原36步、往返结果及两端轻量曲线已交付。下一步建议离线核对Cython编译任务，不重跑本文Django结果。
