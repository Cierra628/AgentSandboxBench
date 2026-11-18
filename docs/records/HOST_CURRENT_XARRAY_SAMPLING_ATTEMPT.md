# Xarray采样开关对照：创建阶段失败及离线核查

后续已获准删除本次失败卷，并在新目录完成[原关闭／开启一对](HOST_CURRENT_XARRAY_SAMPLING_PAIR.md)。本页保留首次失败及原因，后续成功不改写原FAIL。

## 结果：没有任务执行，开启组未启动

2026-10-28原计划关闭→开启各一次Xarray #6992完整35步。本次关闭组在创建guest的HTTP请求上等待600秒后超时，未返回sandbox ID，没有步骤表，完成任务动作0次。驱动按约定停止，没有启动开启组。

外层流程712.440秒，任务控制程序600.221秒；这些是失败等待及准备时间，不是任务耗时或采样开销。官方评测未执行，原945通过／12失败只是固定参考，不能当成本次结果。guest／host内存、采样成本和工具耗时均无有效结果，记录为null，不补零。

[脱敏证据](evidence/host-current-xarray-sampling-attempt-20261028.json)中，实验状态保持FAIL，离线核查单独为PASS。没有重跑任务或放宽检查规则。

## 原因：远端清单请求先于本地缓存查询

私有服务14:59:50 UTC就绪，15:04:52记录第一次`regctl manifest get --format raw-body`失败并重试，错误为`context deadline exceeded`；15:09:52客户端超时后清理服务。请求使用固定Xarray镜像digest，不是下载全部镜像层。本次没有证据进一步区分DNS、连接、TLS、代理或远端响应等待。

失败卷只用`debugfs`只读查看目录及服务日志，没有挂载、修复文件系统或读取任务负载。`/env/image-cache/configs/`中已存在固定digest对应的`image.json`和`image.metadata.json`。这证明配置文件已复制进来；没有到达后续缓存校验，不能据此宣称缓存有效性或guest功能通过。

当前源码说明了这条依赖顺序：

- `AgentENV/src/image/resolver.rs:191–212`：`resolve()`先取manifest。
- `AgentENV/src/image/oci_image.rs:861–873`：通过`regctl manifest get --format raw-body`请求远端清单。
- `AgentENV/src/image/resolver.rs:318–340`：拿到manifest digest后才打开本地镜像配置缓存。
- `scripts/agentenv_cold_start.py`：创建请求的600秒超时。

本次实际请求由私有服务日志确认；源码用于解释执行顺序，不替代本轮运行证据。已有转换缓存不等于创建流程完全离线，因此不能通过重新转换镜像或增加HTTP超时解决这次等待。

## 处理选择：先检查远端元数据，不原样重试

本段未修改后端、采样器、源服务或任务配置，没有启动新服务或guest。恢复实验时，优先在分配8 GiB卷前，用有短超时的固定镜像manifest查询检查远端元数据路径；成功后仍按原条件完成关闭→开启一对，保持默认回收、2 CPU／4096 MiB、8 GiB卷、0.1秒和原诊断规则，不追加相近校准。

若同一路径继续失败，再考虑只为私有服务提供经固定digest验证的本地manifest；必须保持镜像身份、运行元数据及本地层校验，未知命令原样委托或明确失败，不改源服务。本地已有3043字节的历史`registry-manifest.json`，但本段只查看JSON结构，未验证其内容与固定digest的对应关系，也没有实现或启用替代路径。旧`xarray6992_offline_image.py`只拦截`image copy`，不能直接解决`manifest get`，不把它当现成修复。

若改变准备路径，两组必须使用同一实现，创建／准备差异单列；不与旧远端启动样本混算采样净开销。

## 清理与空间

原记录确认私有容器、挂载和loop已回收，源服务恢复原停止状态。续接时又核对失败卷无挂载、loop关联和打开使用者，无Firecracker，固定源身份及停止状态一致。

保留的失败卷：

```text
.artifacts/storage-isolated-service/20261028T145943Z-2278267/volume.ext4
```

逻辑容量8 GiB，实际分配约8 GiB，未获本卷删除授权。本次结束可用约30.634 GiB，低于严格超过34 GiB的启动／分配前门槛；下一轮实跑因此暂停。删除前仍需重新检查资源身份与使用者，原始日志、失败结果、目录清单和脱敏分析全部保留，不删除其他文件。

## 证据位置与下一步

- 原驱动及日志：`.artifacts/host-current-xarray-pair-20261028/`。
- 工作流：`.artifacts/platform-tasks/20261028T145801Z-2277290/`。
- 原任务及失败卷：`.artifacts/storage-isolated-service/20261028T145943Z-2278267/`。
- 本段只读分析：驱动目录下`failure-analysis/analyze_failure.py`与`summary.json`；原服务日志仅本地保存，不上传。

下一步：取得这一份失败卷的定向删除授权后释放空间；先检查固定manifest能否在短超时内取得，再在新目录补原关闭→开启一对，共70动作。完成条件为任务／诊断结果、真实采样开关、计时与成本、报告、最终卷检查及自有清理通过。网络仍失败则停在该边界，不再次等待完整600秒，不增加任务轮数。

## 随后恢复：删除已获授权，远端清单检查通过

用户随后明确允许删除此卷并继续。重新核对无挂载、loop关联和打开使用者后，已只删除上述`volume.ext4`，其余日志及失败记录保留；可用空间恢复约38.634 GiB。清理记录在`.artifacts/host-current-xarray-resume-20261028/cleanup.json`；前面的失败及当时资源状态仍按原时间保留。

恢复入口在分配新卷前，用同一固定源服务内的原regctl查询固定镜像清单，25秒超时保护；实际2.918秒成功，与历史固定清单逐字节一致。该检查只请求元数据，不创建guest，不读取任务文件；没有改成离线镜像路径或修改源服务。原失败的具体网络等待原因仍未知，本次只确认请求已经恢复。新一对的记录单独保存在`.artifacts/host-current-xarray-resume-20261028/`，清单检查不计入任务控制程序或采样开销。
