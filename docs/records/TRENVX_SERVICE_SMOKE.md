# TrEnv-X 完整服务文件恢复 smoke（2026-09-27）

固定 TrEnv-X / IncrementalDAX 源码首次在本项目的隔离完整服务中通过小文件 checkpoint/restore：实际 template-manager、orchestrator、envd、Cloud Hypervisor，以及学长 controller 的捕获、封存、恢复与清理路径均已执行。之后另用同一隔离服务入口通过复制抛错与复制子进程超时两种受控恢复测试。**这些是功能 smoke，没有性能对照。** Exp1 冻结轨迹另见 [Exp1 记录](TRENVX_EXP1.md)。

## 运行与边界

成功运行 `20260927T034748Z-2077790` 的原始证据在本机 `.artifacts/trenvx-service-smoke/20260927T034748Z-2077790/`。脱敏摘要见 [service-smoke evidence](evidence/trenvx-service-smoke-20260927.json)。

使用固定源码、Go 1.23.0 服务构建、既有定制 CH 和 6.1.134 guest kernel；host 为 6.19.9+，guest 为 2 vCPU / 4096 MiB。外层建立独立 network/mount/PID namespace，内部使用无外部连通性的 dummy 默认路由、私有 `/run/netns`、`/etc/hosts` 和 `/tmp`。服务只在该 namespace 的 loopback 监听；cgroup 和数据目录按 run_id 独占。退出 namespace 会终止其子进程，随后核对所属 cgroup 和 loop 设备清理。没有修改共享服务或全局清缓存。

本轮复制现有 `/var/lib/trenvx/templates/prettier-14400-ch-dax/image/rootfs.ext4`，按文件哈希标识输入，**不是从镜像 digest 开始的全新重建**。副本解除 ext4 read-only 特性、增加 256 MiB、替换 envd/overlay-init 并添加 guest envd unit drop-in，然后恢复 read-only 特性。任务 parent 为 `/sys/fs/cgroup/system.slice/envd.service/tasks`。原模板没有改动；两次测试脚本失败运行的镜像已在验证后压缩归档，后续轻量采样运行的归档见 [Exp1 指标记录](EXP1_METRICS_AUDIT.md)。

恢复语义为文件状态继承。新 guest 使用新构建的模板、DAX checkpoint lower 和独立 writable upper，恢复选定 `/tmp` 文件。父实例继续运行不等于父进程在子 VM 恢复；不能据此声称通用进程级 fork。

## 实际结果

| 检查 | 结果与证据 |
| --- | --- |
| 模板及完整服务创建 | PASS；`template-build.log`、`orchestrator.log`、`parent.json` |
| 真实工具结果 | stdout=`asb-out`、stderr=`asb-err`、退出码 127；原始结果及离线核对见 `expected-failure.json`、`output-audit.json` |
| controller 捕获 | PASS；`controller/checkpoint-1-quiesce.json` 确认 frozen、paused、copied、resumed、runtime_removed、thawed、complete |
| 文件封存与新模板 | PASS；使用既有 `archive_upper/build_layer`，随后实际 template-manager clone-template；见 `controller/checkpoint-1-template-manager.log` |
| root 和 `/tmp` 恢复 | PASS；恢复 checkpoint 时的文本，子实例继续写入；见 `restored-files.json` |
| 父子写入隔离 | PASS；父实例修改未进入已捕获文件，子实例修改不影响父实例；见 `parent-isolation.json` |
| 后台写入边界 | 父实例解冻后文件继续增长；新 guest 继承的 writer 文件在 300 ms 检查窗口内不增长。不是任意进程状态恢复测试。 |
| DAX 层挂载 | `/dev/pmem1` 为 `ro,dax=always`；见 `restored-files.json`、`output-audit.json`。没有进行驻留页测量。 |
| 清理 | SDK 任务清理、两个实例删除、服务退出通过；所属 cgroup 已删除，无本次镜像关联的 loop 设备。见 `sdk-cleanup.json`、`result.json`。 |

## 复制阶段故障恢复

运行 `20260927T113837Z-2101631` 使用 `--scenario fault-copy`：在真实 guest 冻结、CH 暂停之后，本次 controller 进程的 upper 复制方法恰好一次抛出受控 `OSError`。没有修改共享服务、模板或内核。预期失败被捕获，`controller/checkpoint-1-quiesce.json` 显示 `frozen=true`、`paused=true`、`resumed=true`、`runtime_removed=true`、`thawed=true`、`complete=false`；未生成 checkpoint 模板。

故障后 CH 报 `Running`，父 guest 的后台 writer 在 300 ms 内继续增长，后续工具调用返回 0。SDK 删除、服务退出、所属 cgroup 删除及 loop 检查均通过，运行结果为恢复用例 `PASS`；这不表示 checkpoint 成功。原始证据在本机 `.artifacts/trenvx-service-smoke/20260927T113837Z-2101631/`，脱敏摘要见 [service-smoke evidence](evidence/trenvx-service-smoke-20260927.json)。本用例仅覆盖 host 复制接口抛错；磁盘实际 I/O 错误、冻结/解冻失败、guest 请求超时与跨服务重启仍未验证。

## 复制超时恢复与空间清理

第一次真实服务超时尝试 `20260927T115731Z-2104794` 为 **FAIL**：测试把 upper 的 `cp` 超时设为 20 ms，但稀疏镜像在该窗口内已复制完成，预期的 `TimeoutExpired` 没有发生。捕获审计显示 CH 已恢复、guest 已解冻；本次 SDK、服务、cgroup 与 loop 清理通过。不能把它计为超时恢复通过，详见该运行的 `result.json`、`injected-failure.json` 和 `controller/checkpoint-1-quiesce.json`。

脚本随后改为只在超时注入场景让复制子进程读取无限输入，避免稀疏 upper 过快完成。独立探针 `.artifacts/trenvx-timeout-probe/20260927T120459Z-2106740/result.json` 先确认 20 ms 限额触发超时且子进程已回收。清出空间后的完整服务运行 `20260927T123214Z-2113498` 为 **PASS**：真实 guest 冻结、CH 暂停后，替代复制命令的 `cp /dev/zero` 触发 `TimeoutExpired`，子进程已回收；checkpoint 仍为 `complete=false`，CH 恢复 `Running`、guest 解冻，父实例写入和后续工具继续，SDK/服务/cgroup/loop 清理均通过。该测试验证控制器在复制子进程超时后的恢复，不代表实际 upper 或磁盘 I/O 会自然超时。

经用户授权，仅将两个测试脚本自身失败的运行 `20260927T115731Z-2104794` 与 `20260927T112658Z-2098122` 的 `data/` 压缩为各自目录下的 `data.tar.gz`。每份都先通过 `gzip -t` 和 `tar --compare`，记录 `archive-manifest.json` 的 SHA-256，然后才移除原 `data/`；运行日志、结果和审计文件仍在原位。两份原目录共约 22.1 GiB，归档各约 2.9 GiB，实际空闲空间合计增加约 16.3 GiB。清理后完成上述超时运行，目前根文件系统空闲约 38 GiB。归档仅在本机，尚无异机备份。

## 新发现：复制成功返回但镜像被截短

第三次尝试中，2 MiB 对齐的源镜像经过 template-manager 复制后，CH 报 `PmemSizeNotAligned`。固定依赖 `github.com/KarpelesLab/reflink v1.0.2` 的 `Auto` 回退只调用一次 `copy_file_range`，忽略返回的实际字节数。本机 ext4 上的独立复现为：

| 方法 | 输入字节 | 输出字节 | 尾部标记 | 返回错误 |
| --- | ---: | ---: | --- | --- |
| 原 `reflink.Auto` | 2149580800 | 2147479552 | 丢失 | nil |
| `cp --reflink=auto --sparse=always` | 2149580800 | 2149580800 | 保留 | nil |

证据为 `.artifacts/trenvx-copy-probe-20260927/result.json`，复现源码为 `scripts/probe_trenvx_copy.go`。问题影响本机非 reflink 回退路径；本轮没有复核历史 Btrfs reflink 运行，不能将该发现泛化为所有历史结果无效。

`patches/trenvx-complete-template-copy.patch` 仅替换 template-manager 的两个镜像复制调用，使用 coreutils 并核对长度，保留支持时的 reflink 行为；没有升级依赖。补丁自带超过 2 GiB 的稀疏文件尾部回归测试，构建及测试通过，证据为 `.artifacts/trenvx-service-build/20260927T034735Z-2074196/`。新补丁在固定源码的干净文件快照上复现通过，见成功运行的 `patch-reproduction.json`。

## 失败记录

| run_id | 首个失败边界 | 处理 |
| --- | --- | --- |
| `20260927T034202Z-2072804` | 修改模板副本返回 EROFS | 副本先解除 ext4 read-only 特性 |
| `20260927T034250Z-2072926` | 紧凑镜像替换 envd 返回 ENOSPC | 副本增加 256 MiB 并 resize2fs |
| `20260927T034339Z-2073039` | template-manager 后 CH 报 PmemSizeNotAligned | 最小复现确认复制截短，增加上述修复 |
| `20260927T034748Z-2077790` | 无；全部 smoke 检查通过 | 保留模板、日志和证据 |

前三次失败都完成了运行所属 cgroup/loop 检查，没有把它们计为 PASS。

## 复现与下一步

已有固定源码、内核、seed 模板及本地客户端时，以有 root 权限的实验环境执行：

```bash
python3 scripts/40-apply-trenvx-task-cgroup.py
bash scripts/43-build-trenvx-service.sh
bash scripts/44-smoke-trenvx-service.sh
bash scripts/44-smoke-trenvx-service.sh --scenario fault-copy
bash scripts/44-smoke-trenvx-service.sh --scenario fault-copy-timeout
```

`43` 只构建本地服务并运行复制回归；`44` 创建一次隔离完整服务实验。当前空间门槛为普通重放与受控复制故障 36 GiB、完整服务 smoke 44 GiB、带 checkpoint 的 Exp1 48 GiB，以容纳运行镜像、归档暂存和主机余量。每次运行独立目录，失败重试不覆盖前次结果；后续运行结束后默认把本次私有 `data/` 校验归档再清理，可用 `--keep-data` 保留原镜像。此入口仍依赖本机已有 seed，不是全新机器部署器。

`incrementaldax-service-paths.patch` 为 controller 增加 `ASB_TRENVX_BUILD_CONFIG` 和 `ASB_TEMPLATE_MANAGER` 两个可选路径，默认保留上游布局。成功 smoke 使用这些路径调用真实 controller，而非重写 checkpoint 算法。

成功运行后仅补充了运行源码/补丁版本归档、外层失败不得被内层 PASS 覆盖的判断，以及私有 hosts 的 hostname 解析；已做语法检查，没有为这些收尾改动重复完整服务实验。成功运行的实际脚本副本在对应目录中。

后续 Exp1 的 26 步完整重放及第 013、022 步文件恢复续跑现已通过，见 [Exp1 记录](TRENVX_EXP1.md)。该结果独立于本页的小文件 smoke；真实服务的其他失败路径、自然超时及公平性能比较仍待验收。当前空间与归档策略以[项目状态](../STATUS.md)为准。
