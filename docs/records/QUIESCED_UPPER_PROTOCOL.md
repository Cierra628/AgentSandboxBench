# 受控写入停止与在线封存协议（2026-09-26）

后续[收尾实验](CLOSEOUT_STATUS.md)已用 task cgroup 替代单 PID SIGSTOP，并在 CH 源 OverlayFS 捕获和新 guest 重建中通过；AgentENV guest 另验证了父进程退出、setsid 后代的归属与冻结。生产 controller 尚未接入，以下保留原 SIGSTOP 协议证据。

固定 CH / guest 6.1.134 的受控协议验证通过：已知写进程停止后，归档 tmpfs、同步 guest、暂停 VM 并复制 upper；父 VM 恢复后继续写入，新 guest 则从封存层重建出捕获边界的旧状态。最终测试覆盖真实 OverlayFS upper 的 whiteout 和 opaque 目录。该结果验证了小型已知负载，不表示完整 TrEnv-X controller 已具备通用写入停止能力。

## 协议及验收

入口为 `bash scripts/36-check-quiesced-upper.sh`，复用 `check_online_ch_upper.py` 的生命周期与学长实际 `archive_upper/build_layer`，不修改学长 controller。

1. 在独立 mount namespace 和本次 Btrfs 镜像内建立基础层与 writable ext4。父 guest 使用只读 DAX 基础层和 OverlayFS upper；2 vCPU、4096 MiB、无网络。
2. 单个受控 worker 完成一组文件修改，并将 tmpfs generation 设为 1，然后对自身发送 SIGSTOP。控制脚本核对 `/proc/PID/status` 的停止状态。worker 的同步子命令都已返回；这不是对任意进程树的冻结实现。
3. worker 停止期间，把 `/tmp/generation` 打包进 upper 的 `runtime.tar`，随后 guest sync。host 收到 ready 标记后调用 CH `vm.pause`，通过 `vm.info` 确认 Paused，使用原 cp 命令复制 upper，再调用 `vm.resume`。归档与同步期间未暂停整个 VM，但已知应用写进程保持停止。
4. 原始副本走既有 `ro,noload` 封存；另一个独立副本以可写挂载执行日志恢复，两个视图都检查内容、元数据及 tmpfs 归档边界。日志恢复不作用于父镜像或原始捕获副本。
5. 父 guest 在预设等待结束后恢复 worker，完成第二个文件及 generation=2 的写入，再同步并关机。等待是 fixture 的控制窗口，不是测得的实际 checkpoint 延迟。
6. 新启动一个 CH guest，以封存层和基础层作为 DAX lower、全新空白镜像作为 upper，检查捕获状态及链接、删除和目录覆盖。新 guest 正常关机，封存层 SHA-256 不变。

文件边界为捕获时 `first/second=1/0`，父 guest 完成后 `1/1`；runtime.tar 保留 generation=1，父 guest 的 tmpfs 已变为 2。两文件之间的中途状态是特意选择的合法边界，不声称应用事务原子性。

## 实际结果

目录前缀 `.artifacts/online-ch-upper/`，本阶段三轮均 PASS：

| run_id | 新增验证 | 边界 |
| --- | --- | --- |
| `20260926T031500Z-1847499` | writer STOP、tmpfs 归档、guest sync、CH pause/copy/resume、host 内容和元数据、父 guest 续跑 | 直接 ext4 upper，未启动新 guest |
| `20260926T031633Z-1847915` | 加入从实际封存层启动新 guest，检查 OverlayFS 重建 | 源仍为直接 ext4 upper |
| `20260926T031745Z-1848130` | 源也采用 OverlayFS，验证真实 whiteout、opaque，再启动新 guest 重建 | 本阶段最终完整 smoke |

最终结果逐项通过：准备、guest ready、暂停、在线复制、恢复、封存、副本日志恢复诊断、元数据和 runtime 归档、父 guest 完成、内容保真、新 guest 重建及清理。删除和重命名后的原路径不可见，目录旧内容未复活；新内容、0640 权限、符号链接和硬链接关系保留。原 upper 封存层和日志恢复副本均检查了字符设备 whiteout 与 `trusted.overlay.opaque=y`；新 guest 检查它们产生的可见状态，未直接读取 ACL/xattr。

证据包括 `result.json`、`timings.json`、`paused-info.json`、命令 JSON/stdout/stderr、`serial.log`、`restore-serial.log`、`parent-init.sh`、`restore-init.sh`、两个 initrd、配置和源码快照，以及捕获/恢复镜像。每轮独立 run_id，未覆盖上轮失败数据。`host-cleanup-audit.json` 为运行后的独立资源审计。

## 单次耗时与限制

最终 smoke 的 host 单调时钟记录：

| 阶段 | 毫秒 |
| --- | ---: |
| 暂停 API + 状态查询 | 19.67 |
| cp + 复制窗口核对 | 3.58 |
| 恢复 API | 6.39 |
| 暂停请求至恢复 API 返回的总窗口 | 29.68 |
| archive_upper | 67.47 |
| build_layer | 54.40 |
| 独立副本日志恢复挂载 | 17.04 |
| 新 guest 启动、检查、关机完整窗口 | 314.16 |

总暂停窗口已包含暂停、复制和恢复，不能重复相加。新 guest 窗口不是“创建至工具通道可用”。日志恢复挂载耗时包含挂载开销，不是纯日志重放时间；同步组也不保证存在待重放事务。

guest `/proc/uptime` 在 tmpfs 归档前后均为 0.32 秒，sync 后为 0.33 秒，仅有约 10 ms 粒度，不能报告归档精确为零或 sync 精确为 10 ms。这些是微型 fixture 的单次阶段记录，不是任务性能对照或稳定性统计。没有扩大到 10 对性能实验。

## 接入边界与后续

本轮解决的是已知 worker、已知停止点下的捕获验证。当前 controller 没有能确认所有应用写入者已停止的接口；工具 shell 退出也不自动证明后台后代进程结束。因此暂未把这个 fixture 的 SIGSTOP 逻辑移植为通用 checkpoint，也没有用全 guest 杀停进程代替任务隔离。

下一步先为工具及其后台后代建立可归属的任务进程组或 cgroup，并验证停止/恢复与退出检测；在独立服务中接入后，再复用冻结轨迹检查动作、预期失败、patch 和清理。需要单独处理不在任务组内的写入者、超时后恢复、`/tmp` 与 rootfs 的真实归档流程及多写入者。必要时在捕获副本上恢复日志，但这仍不提供应用事务或任意进程状态恢复。

本轮是文件状态继承：父 VM 的 resume 属于本次暂停继续，新 guest 是另建进程和全新 writable 镜像，不是父进程 restore。未运行完整 TrEnv-X 服务、完整任务轨迹、ACL 专项或并发压力；未修改系统配置、共享服务、学长模板，未提交/推送 Git。
