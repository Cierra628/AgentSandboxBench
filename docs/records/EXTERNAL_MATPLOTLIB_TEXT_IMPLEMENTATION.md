# Matplotlib与文本编辑：接入及完整验证

最新：[两项各一次完整验证已通过](../../results/external-matplotlib-text-20261105/REPORT.md)。95／50步原结果、采集、真实图表及清理完整；以下保留接入过程与当时阶段。

## 2026-11-05：本次获准用17891下载，运行阶段保持本地输入

用户在Docker Hub直连失败后，明确允许通过17891下载这两个固定镜像。这是本次镜像准备的例外，不扩大到guest、Git／pip或其他任务，也不改全局代理设置。使用现有`skopeo`按固定摘要选择linux/amd64，下载到`.artifacts/external-oci-20261105/<task_id>/oci/`；保留原始索引／清单、下载日志和磁盘保护收据。OCI层的摘要检查用于确认下载完整性，不当作任务采样。

新增显式`--oci-input`，仅接受这两个任务。外层转为隔离入口的`--external-workload-oci-input`，只读挂载已校验的OCI目录；私有服务依赖目录的regctl包装程序只提供这份清单／配置及本地复制，不允许远程回退。原生镜像转换保持，源服务文件不改。默认不传此参数的旧任务行为保持。新增3项合成OCI检查及原新任务7项检查通过，首次真实运行兼作本地镜像输入入口验证，不额外短测。

两个镜像均已下载校验，顺序各一次真实运行通过，见上方汇总。运行阶段各层代理清空；原生转换、身份链及本地OCI入口均已实跑验证。成功下载后的辅助收据错误与离线修复已在实跑报告单列，不重跑任务。

## 实跑前的离线接入记录

以下记录当时的完成范围和后续计划；所列两次实跑现已按上方报告完成。

2026-11-05。已在原外部任务入口接入这两项的执行、结果检查及测量，没有启动服务、沙箱或镜像下载。原Django／C4／Cython的执行方式和采样默认值保留。当前仍是6种任务有本机实跑证据，不把本次离线检查计为新增任务跑通。

## 固定输入与原检查

上游为`RyanShen30/AgentSandboxWorkloadResults`，固定提交`cbe767b2aa23ceb59875b1cb0a7a6141c749312a`，使用`provenance/agentenv-runs.json`指定的primary记录。配置记录公开源镜像摘要，不使用上游仅本机有效的`aswr.local`别名。两项均为单guest、2 vCPU／8192 MiB、umask 0077，逐步独立Bash；不搬入上游冻结业务进程、根目录扫描和校准负载。

| 任务与配置 | 原输入 | 保留的非零退出 | 完成检查 |
| --- | --- | --- | --- |
| Matplotlib #24177：`configs/external-matplotlib24177-light.json` | 95个bash动作，目录`/testbed` | 29／42／47／48／63／78步为1，74步为4 | 88及93步各60 passed；原导入检查；完整最终源码补丁与上游参考一致 |
| 大规模文本编辑：`configs/external-text-editing-light.json` | 50步：42个bash、2个read、6个write；8份辅助文件，目录`/app` | 19／37步为1，43步为127 | 47步完整输出匹配标记；50步两种solve方式匹配标记；原`cmp -s /app/output.csv /app/expected.csv`成功 |

Matplotlib原脚本含`exec bash -lc`，不能source进总控制Shell。第95步在guest内执行原`git add -A`，所以最终补丁用原`git diff HEAD --binary`取得，包含已暂存和未暂存改动；不改为只取未暂存差异。未将上游332 KB补丁打包进本项目。测试和输出检查是上游原任务范围，不称为隐藏测试或套用Prettier的“0通过2失败”。

文本编辑的两个read脚本和六个payload保持原路径及内容；不预先读取CSV。原线程数、输出环境和登录Shell保持。`load()`先检查完整动作、辅助文件和Matplotlib参考补丁是否存在；既有脚本完整性协议保留。

## 新两任务的采集

入口仍为`bash scripts/run-external-workload-light.sh CONFIG UPSTREAM`，新配置显式启用以下组合；不改变旧三项的“无PSS／无mincore”行为。

- **普通计数0.1秒：**guest `/proc/meminfo`；外层实验cgroup的`memory.current`和`memory.stat`组成。保留创建前基线、全流程曲线、任务窗口内current观测峰值及样本中位数。cgroup覆盖输入准备、控制程序、私有服务及guest的组内记账，不保证覆盖预先记到组外的页。
- **VMM PSS周期1秒及终点一次：**通过本次私有服务cgroup、sandbox ID和Firecracker身份绑定实际进程，读取其`/proc/PID/smaps_rollup`。PSS按映射共享比例分摊；本轮单guest，N=1，PSS总量与PSS/N相同，不包括全部服务或未映射缓存。启动时首次PSS读取失败就停止；不以零补缺。
- **初始下层文件mincore P1一次：**只读GET `/vm/config`，结合实际rootfs配置和服务已打开的文件描述符确认文件，按设备号:inode去重；不硬套19文件。动作和结果下载完成后、结果检查及删除guest前查询。只映射为`PROT_NONE`查询驻留，不触碰内容页；前后复查文件身份和同一外层cgroup的current／file。
- **逐工具及整段计时：**guest单调时钟记录每步起止、时长、退出码；工具累计包括预期非零退出。host任务窗口从冷创建调用前至结果下载命令返回；动作及下载窗口从重放调用前至同一终点。控制程序整轮另含计时器编译、终点查询、结果检查及清理。没有保存或恢复，次数明确为0。
- **成本：**复用guest／VMM／host采样收据中的CPU、时长、点数，计算实际最大采样间隔；终点PSS和mincore各存查询起止与当前线程CPU，初始化元数据查询也单列。这些成本不是任务减速率，本轮不加采样开关对照。

`external_task_metrics.py`复用已有绑定、VMM采样、下层清单和mincore实现；guest轻量采样器本身不强塞PSS。初始身份清单、终点清单及失败信息、周期PSS表均保存；内外两层验收都要求终点值完整、PSS可读、周期采样成功停止。功能检查与采集状态分列。

P1只是初始不可变下层文件的全局驻留，不含upper、额外快照或guest缓存与host物理页的完整对应关系。P1大于组file时记录净不足，实际组外容量仍留空；净差不加回current，也不当共享节省。PSS、current、file、mincore和guest Cached不相加。默认回收保持，不加P0冷起点、周期mincore、预读、强制驻留或暂停沙箱。

## 超时、代理和输出

新两项采用重放900秒、控制程序1800秒、隔离流程2100秒、外层2400秒的明确超时。文本编辑上游工具累计已超过旧240秒预算。旧`validate-isolated-storage.sh`固定900秒，新两项从同一统计组调用原Python隔离入口，由外层2100秒预算约束；旧入口及旧三任务的调用路径不改。

新两项的systemd子进程、隔离控制程序、私有服务、冷创建guest环境及重放Shell都清空六个代理变量，不使用17891，不改全局代理、网络策略或原服务。此轮无任何联网；后续缺镜像时只按既有规则作有界直连，失败即停止，不回退代理。

真实运行沿用32 GiB私有卷、启动前可用磁盘严格大于58 GiB及现有内存保护。成功后，精简报告、指标摘要和图复制到新的`results/external-<task_id>-<run_id>/`，不覆盖已有结果；原始动作日志和完整补丁留在本机`.artifacts/`，不复制到发布报告。终端打印的外层`result_dir`内`result.json`记录`published_report`和原始路径。新图纵轴写明具体量及单位。

## 离线验证与证据

- 两项固定输入加载通过：95及50步、工具类型、原退出序列、镜像／2 CPU／8 GiB与primary记录一致；147份Shell语法检查通过（145份原动作加2份runner）。
- 58项相关单元测试通过：本轮47项，另复用11项未受后续新任务分支影响的Cython检查。新测试覆盖独立Shell的`exec`、辅助文件、专属结果、完整补丁、计时边界、代理清空、内外层失败及清理、PSS权限和进程变化、文件变化、查询成本和结果发布。
- 首次旧外部任务测试中，抽取原生代理分支的测试未提供新分支所需的task_id，报`NameError: external_value`；已补齐旧Django测试上下文，保留原日志后通过。没有放宽检查或重跑真实任务。
- 三个入口`--help`、修改代码的语法和差异空白检查通过。使用合成数据检查新guest／host图的具体量标签，并查看实际图片；它们不是新任务运行数据。

本机证据：`.artifacts/external-matplotlib-text-offline-20261105T063335Z-2846741/`，其中`inputs.json`、`tests.json`及逐组日志记录检查；`synthetic-preview/`只用于图表检查。必要新测试已加入`bash scripts/check-platform-offline.sh`；本轮按受影响范围检查，没有重跑整套平台测试或正式九次对照。

## 下一步：各一次完整功能验证

先复查本地镜像、实际负载、空间及原保护条件，再顺序执行以下两项，每项一次原路径，不额外短测或重复：

```bash
bash scripts/run-external-workload-light.sh configs/external-matplotlib24177-light.json /本机路径/AgentSandboxWorkloadResults
bash scripts/run-external-workload-light.sh configs/external-text-editing-light.json /本机路径/AgentSandboxWorkloadResults
```

完成条件为原动作／退出码／专属结果、身份链、四类量、查询成本和清理都有真实证据。异常保留首个错误与目录，不自动重跑。此计划供用户检查，当前尚未执行；离线通过不替代真实镜像、PSS权限及任务运行验证。
