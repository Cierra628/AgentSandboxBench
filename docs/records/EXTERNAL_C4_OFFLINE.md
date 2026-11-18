# C4重分片任务离线核对：推荐作为第二个新任务

后续进展：已完成[C4离线接入](EXTERNAL_C4_IMPLEMENTATION.md)及[一轮真实验证](EXTERNAL_C4_VALIDATION.md)。本页保留接入前的源码差异和输入依据。

## 输入规模合适，但要保留它自己的执行方式

推荐继续接入`reshard-c4-data`。固定镜像带有`/app/c4_sample`样本，同伴保存的初始清单显示9898个普通文件、817461269字节，即779.592 MiB；不是下载整套C4数据集。原36步反复读取样本、生成分片、解包并比较，适合观察文件读写时内存和缓存的变化。

本轮只核对已有代码和公开结果，没有启动服务／guest、执行任务或查询驻留，没有网络请求、大数据下载或运行入口修改。结论是**值得接入、存在明确的有限改动**，不是本机已经跑通C4。

来源固定为[同伴仓库提交](https://github.com/RyanShen30/AgentSandboxWorkloadResults/tree/cbe767b2aa23ceb59875b1cb0a7a6141c749312a)：

- 提交：`cbe767b2aa23ceb59875b1cb0a7a6141c749312a`。
- 主记录：`results/agentenv/reshard-c4-data/runs/reshard-c4-data-20261025T054616Z-971984/`，不是同目录较早的另一轮。
- 镜像：`docker.io/alexgshaw/reshard-c4-data@sha256:92d209e704785e17ad6b3f740e813dd72bc7ac33982ce435d197ab9d890eac2a`。
- 同伴保存的镜像记录：linux/amd64、9层、压缩层合计1432498021字节，即1.334 GiB。远端镜像当前是否可取得，以及本机实际转换过程，本轮离线未验证。
- 配置：2 vCPU、8192 MiB RAM；同伴使用32 GiB服务卷。

## 不能只把Django配置里的任务名换掉

| 差异 | 已核对事实 | 最小接入要求 |
| --- | --- | --- |
| 输入类型 | 27步Bash、9步write，共36步 | 仅明确接入这个固定任务，保留现有Django路径 |
| 随附内容 | 36份`.sh`加9份`.payload`，后者共67755字节 | 全部带入，不能只上传脚本；保留`/probe/input/NNN.payload`引用 |
| Shell状态 | 每步新建`/bin/bash`，从`/app`开始，`umask 077` | 不套用Django持续`source`的Shell；不额外开启pipefail |
| 源码前置检查 | 非Django仓库，也不要求其Conda环境 | 使用C4镜像和路径／工具检查，不执行Django的git检查 |
| 成功条件 | 预期失败、全量往返、分片约束和原语法检查 | 不仅检查36个退出码，更不能只用语法检查代表任务通过 |
| 上层收据与图 | 当前多处固定28步和Django标题 | 改为当前已核对任务的动作数与名称，不削弱边界检查 |

实际源码依据：

- [`scripts/external_workload.py`](../../scripts/external_workload.py)的`load()`在接入前明确只收Django、28步和全部Bash；`guest_runner()`固定`/testbed`并逐步`source`。核对时用本地候选配置调用，确认C4被预期拒绝，没有放宽入口。
- [`scripts/run_external_workload.py`](../../scripts/run_external_workload.py)接入前只复制`.sh`，并使用Django源码／Conda前置检查和结尾语法检查。
- [`scripts/run_host_external_workload.py`](../../scripts/run_host_external_workload.py)及[`scripts/validate_isolated_agentenv.py`](../../scripts/validate_isolated_agentenv.py)接入前有28步验收，图标题也固定Django。
- 同伴主记录的`guest-worker.sh`共36次调用`/probe/tool_timer … /app 900`；该任务不在原`SESSION`集合中。其固定`workload_timer.c`明确先`chdir()`再`fork()`，子进程用`/bin/bash`执行单份脚本。

因此建议改动集中在上述4个现有脚本，新增一个固定C4配置和必要回归用例，不建立另一套采样系统。一个长执行请求中顺序运行36个独立Bash即可；不需要逐步RPC，也不需要原仓库的冻结／全根扫描程序。复用Django已验证的计时程序权限设置、试读、失败退出和时间戳校验。

## 保留预期失败，检查真正的往返结果

参考退出码使用主记录`steps.csv`，不使用`replay-inputs.json.source_exit_code`：

| 步骤 | 预期退出码 |
| --- | ---: |
| 3 | 2 |
| 11、12、16、18 | 1 |
| 其他步骤 | 0 |

原轨迹包含开发中的错误实现和故意损坏分片；不能提前修好业务脚本或删除失败步骤。第30～32步还会打印内部解包结果，即使整体Shell退出0，也不代表这些内部操作全部成功；保留完整输出，不将内部失败统一改成平台失败。

第33步进行目录条目数、文件大小限制及内容／结构往返比较；第34步再次比较结构并输出9898文件的结论。接入时至少保存并核对：

- 第33步退出0，输出含`CONSTRAINT VIOLATIONS: NONE`和`FINAL VERDICT: BYTE-EXACT ROUNDTRIP ON c4_sample`。
- 第34步退出0，输出含`9898 files, structure + content`。
- 原附加验证：`test -s /app/compress.py && test -s /app/decompress.py && python3 -m py_compile /app/compress.py /app/decompress.py`。

第35步会按原任务清理`/tmp/finalout`等输出，因此验证必须使用第33／34步已经保存的输出，不能在第36步后才要求这些目录仍存在。原业务第26／33步自带内容摘要比较，应保留；采样程序不另读样本或增加内容摘要计算。任务初始文件数／规模可利用原第1／2步输出核对，不加一次预扫描。

同伴摘要报告全量往返通过，公开了检查逻辑；完整stdout日志未随仓库提供。本机接入后仍须采到并核对本轮输出。它是固定样本的往返与约束验证，不是官方隐藏测试。

## 当前资源允许规划一轮，不把逻辑大小当实际磁盘峰值

| 已保存证据或当前资源 | 数值 | 用途 |
| --- | ---: | --- |
| 同伴镜像压缩层合计 | 1.334 GiB | 估计镜像传输规模，不是解包占用 |
| 同伴初始guest普通文件逻辑量 | 2.553 GiB | 含样本、依赖及镜像里的其他缓存 |
| 同伴工具边界的普通文件逻辑峰值 | 4.223 GiB，第34步 | 已观察到的边界量，不是步内最高点 |
| 同伴`/tmp`逻辑峰值 | 1.670 GiB，第34步 | 已包含在上一行，不能重复相加 |
| 本机可用磁盘 | 约63.215 GiB | 32 GiB卷的分配前门槛为严格>58 GiB |
| 本机MemAvailable | 约479.229 GiB | 当前满足既有>64 GiB保护条件 |

建议首轮沿用32 GiB定容服务卷、原磁盘和内存保护。依据是同伴同一后端已有32 GiB完整执行、已知工作集和当前资源，而不是声称已经测出本机磁盘峰值。真实启动前仍重查，资源不足时不降低门槛或自动删除其他内容。

源码还有两点影响预算：最终`compress.py`会先将全部分片内容保存在进程内存中，再写出；`decompress.py`在删除旧分片前先建立临时还原目录。原始样本、输出和临时还原会并存。第30步还有稀疏写入，逻辑大小也不等于分配量；镜像历史层、转换缓存和步内瞬间占用不由上述边界总数完全描述。

原样本是后续共享实验的候选，分片和解包输出主要是每个guest自己的写入。不能把同伴约2820.8 MiB的guest缓存峰值全部算作可共享输入，更不能由它推算host节省。该值还使用`Cached-Shmem`及冻结扫描协议，只用于筛选；本项目保留`Cached`含Shmem的口径，不要求新曲线与它相同。

## 下一阶段的最小范围与完成条件

1. **先离线接入。** 仅增加固定C4配置、完整payload上传、独立Bash执行和C4验证；Django入口保持原行为。必要测试覆盖payload缺失、Shell工作目录／变量不跨步、原预期失败、输出证据缺失、计时失败和两任务名称／动作数。完成后再安排实跑。
2. **随后一轮正式验证。** 在分配新卷前检查固定镜像清单及平台，成功后一个新guest执行原36步，2 vCPU／8 GiB，host／guest每0.1秒，默认回收。采样仍是current及分项、guest meminfo、工具时延和采集成本，无PSS、周期mincore、冻结、根扫描或额外预读。
3. **原动作与结果、采集、报告和清理通过即结束。** 保存冷创建与业务窗口分开的耗时，guest曲线、host全流程与任务放大图复制到`results/`。不以同伴39.582秒或缓存峰值作为本机性能阈值；没有关闭采样组或多沙箱对照时，不计算减速或共享率。

本阶段离线核对已完成；本轮没有修改运行入口或自动开始上述实施／实跑。

证据：[逐步核对与资源快照](evidence/external-c4-offline-20261030.json)。本地复核脚本与输出在`.artifacts/external-c4-offline-20261030/`，只解析公开输入、运行`bash -n`语法检查、读取当前资源并确认现入口仍拒绝C4，没有执行任何C4工具。
