# Cython轻量测量：原83步完整通过

## 编译、两种目录下的测试和原预期退出均通过

2026-10-30完成一个新AgentENV guest的Cython扩展构建任务：2 vCPU、8192 MiB RAM、原83步，包含61步Bash、20步read和2步write。使用[固定配置](../../configs/external-cython-light.json)及同伴仓库提交`cbe767b2aa23ceb59875b1cb0a7a6141c749312a`；105份原输入完整上传，每步独立Bash、`/app`目录和umask 077保持。

- 原退出序列全部保持：第41／57／68／72步退出1，第78步退出129，其他退出0。
- 第76步原NumPy 2.3.0和安装包示例断言通过，第83步仍输出`final check: numpy 2.3.0`。
- 第82步在源码目录和`/tmp`安装包目录各有18 passed，实际分别0.95／0.94秒；原测试排除项保持，不只依靠管道退出0。
- 原附加验证从`/tmp`导入`cinvariants`、`spacecurves.chelpers`和`spacecurves.ccomplexity`，实际均为`.so`扩展；源码提交记录为`441c807dbec2ee32e1da572e24e58d52a4eb7afa`。

本轮按原命令获取源码和依赖，没有预克隆、预装或静默改锁定版本。没有额外执行语法检查或官方隐藏测试；验收字段使用原两种目录的18项测试及编译扩展检查。

## 工具累计228.72秒，冷创建与控制程序分开

| 用时 | 本轮结果 |
| --- | ---: |
| 83步工具累计 | 228.722721秒 |
| 首工具开始至末工具结束 | 228.848621秒 |
| 执行整段工具的CLI调用 | 228.907273秒 |
| 冷创建，含镜像获取、转换及启动 | 49.188568秒 |
| 原HEAD网络检查，采样与业务开始前 | 7.220805秒 |
| guest控制程序整轮，含创建、准备、检查和清理 | 287.188724秒 |

工具计时包括原脚本执行及少量计时调用成本，跨度另含段间日志和调度；CLI窗口另含命令传递和返回。冷创建和HEAD均已包含在控制程序整轮中，不重复相加。该任务含未完全锁定的Git／pip网络操作，单轮用时不能单独归因于采样或与同伴用时相减求减速。

## 两端内存曲线已放到results目录

**查看host运行过程，优先打开[任务阶段放大图](../../results/external-cython-light-20261030/host-task.png)。** 虚线表示host执行整段工具的CLI开始和返回；纵轴为MiB。

- [工具时延、guest内存压力与Cached](../../results/external-cython-light-20261030/guest.png)
- [host全流程current及file／anon／kernel](../../results/external-cython-light-20261030/host.png)
- [host相对准备基线的变化](../../results/external-cython-light-20261030/host-increase.png)
- [数值摘要](../../results/external-cython-light-20261030/summary.json)和[逐工具用时](../../results/external-cython-light-20261030/tools.tsv)

PNG／SVG均保留，三张主要图已实际查看，不必进入`.artifacts`加载原始目录。

| 观测量 | 本轮结果／MiB |
| --- | ---: |
| guest `MemTotal - MemAvailable`峰值 | 718.879 |
| guest `Cached`峰值，含Shmem | 230.348 |
| host准备基线，尚未下载新镜像 | 405.664 |
| host全流程`memory.current`观测峰值 | 4769.098 |
| 相对准备基线的峰值增量 | 4363.434 |
| host工具CLI窗口首／末采样 | 2617.137 / 4733.223 |
| host工具CLI窗口current峰值 | 4769.098 |
| host全流程file峰值 | 3728.219 |
| host工具CLI窗口file／anon峰值 | 3725.652 / 1015.523 |

host current是本次cgroup（Linux资源统计组）及其后代的总内存记账，包括准备、控制程序、代理、私有服务、镜像／后备缓存和guest，不是任务进程独占内存。基线在首次镜像获取之前，因此相对基线的增量也包含镜像准备。file是其中的文件记账部分，本轮没有逐文件驻留清单，不能把全部file归给任务文件或共享文件。

guest `MemTotal - MemAvailable`用于观察不可轻易回收的内存压力；Cached属于guest自身的缓存层，不能与host file直接相减求节省。保留默认回收，曲线起伏未单独归因于DAMON。这里只有一个guest、一轮成功运行，不输出共享节省或部署密度收益。

## 0.1秒采样的成本与覆盖

| 项目 | host | guest |
| --- | ---: | ---: |
| 名义间隔 | 0.1秒 | 0.1秒 |
| 有效点 | 2906 | 2279 |
| 观测时长 | 290.707秒，首末点之间 | 229.052秒，采样器运行期 |
| 采样器CPU用时 | 0.851683秒，线程CPU | 1.143157秒，进程user+system |
| 约占一个CPU核 | 0.293% | 0.499% |
| 最大相邻采样间隔 | 0.100111秒 | 0.127072秒 |

host读取计数器累计墙钟0.668758秒。guest样本覆盖首工具前至末工具后，采样器停止并回收；host覆盖准备、执行及清理。没有PSS、周期mincore、冻结、根扫描、额外预读或强制驻留。本轮无关闭采样组，上述CPU占比不是任务减速率。

## nft代理修正已在真实guest中验证

前两次出网失败记录保留在[首次记录](EXTERNAL_CYTHON_VALIDATION.md)和[规则修补](EXTERNAL_CYTHON_RELAY_FIX.md)，均未进入83步，不混入本轮时延或曲线。

本轮用与固定服务一致的`iptables-nft`及`iptables-nft-save`。仅允许实际guest来源`/32`和接口到本次代理地址`/32`及随机TCP端口，其他拒绝规则保持。原HEAD检查成功，允许规则计数从0增加到21，然后才启动guest采样和业务。这验证了修正后的实际访问路径；前两次未保存的对应nft命中计数不补造。

规则、地址及代理由外层持有；任务结束后精确删除并确认不存在。此准备检查不属于周期采样，代理的运行成本仍包含在整组记账与整轮流程中。

## 清理与证据完整

用户确认后，先核对并仅删除第二份32 GiB失败卷`.artifacts/storage-isolated-service/20261030T091812Z-2649167/volume.ext4`，释放约32 GiB；两次失败的卷外记录均保留。本轮启动可用63.108 GiB、MemAvailable约479.328 GiB，满足严格>58 GiB及>64 GiB；32 GiB定容卷预算不变。

任务、计时、采样、报告、离线验收、可写文件边界、只读根和固定卷分配检查全部通过。成功卷按原流程清理；实际确认无活动Docker容器或Firecracker，本次代理进程／地址消失，legacy和nft两套表均无本项目规则残留，源服务恢复停止。没有新失败卷，结束可用约63.097 GiB。

本轮复用有效的固定镜像清单和离线检查，没有为相同证据追加测试或实跑。固定镜像为：

`docker.io/alexgshaw/build-cython-ext@sha256:3612a38fadb89a96f74a1a951fb0b0af734198fd160571eeaba6401593234594`

- [脱敏结果](evidence/external-cython-light-20261030.json)
- 本地驱动、定向删除收据、离线验收及最终清理核对：`.artifacts/external-cython-live-20261030-nft/`
- host工作流程、规则计数及代理收据：`.artifacts/host-external-workload/20261030T094445Z-2654153/`
- 原83步输出与验证：`.artifacts/storage-isolated-service/20261030T094445Z-2654162/external-workload/`
- [离线接入与原复现入口](EXTERNAL_CYTHON_IMPLEMENTATION.md)

## 下一步建议

Django、C4和Cython三项新任务各一轮有效轻量曲线已齐全，Prettier upper扩展继续复用。后续[C4](EXTERNAL_C4_SAMPLING_CONTROLS.md)与[Django](EXTERNAL_DJANGO_SAMPLING_CONTROLS.md)两端开关对照及[Django已准备镜像的host只创建对照](EXTERNAL_DJANGO_HOST_CREATION_CONTROLS.md)均已完成，不再重复。

2026-10-31后续[两对四次采样开关对照已完成](EXTERNAL_CYTHON_SAMPLING_CONTROLS.md)：原332步与验收／版本／清理均通过，工具变化+0.13%／−15.11%，直接CPU约单核host0.294%～0.297%、guest0.504%～0.517%。慢创建的镜像获取重试和第36步混合安装／构建差异分开报告；不将负值解释为固定加速，也不与本单轮混成配对。新图和摘要位于results，本阶段不再追加轮次。
