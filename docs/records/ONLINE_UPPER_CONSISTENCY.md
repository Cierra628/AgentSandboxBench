# 在线 upper 捕获的一致性边界（2026-09-26）

后续进展：[受控停止与捕获协议](QUIESCED_UPPER_PROTOCOL.md)已通过真实 OverlayFS 源和新 guest 重建验证；适用范围为可确认停止的单个已知 worker。下述失败证据保留有效，通用 controller 尚未接入停止协议。

已在固定 Cloud Hypervisor / guest 6.1.134 上复现一个文件状态保真失败：guest 初始 `sync` 后，再覆盖文件并等待 `fsync` 返回；host 复制在线 writable ext4 镜像，现有 `archive_upper` 的 `ro,noload` 挂载读到旧值。同一捕获镜像的独立副本经过 ext4 日志恢复后读到新值。更新存在于捕获镜像中，但跳过日志恢复的读取路径没有呈现它。

这个结果限定于“初始同步后又出现写入”的已知负载，不能据此宣布历史冻结轨迹已经损坏。写入后补一次 guest `sync` 的小规模对照通过。完整 TrEnv-X 服务和任务级在线 checkpoint 尚未验收；本轮没有修改 controller 或封存函数。

## 实际路径与实验方法

学长 Exp3 `controller.py:capture_template` 先在 guest 执行 `sync; echo 3 > /proc/sys/vm/drop_caches`，之后归档、下载 `/tmp`，最后 `promote_snapshot` 用 `cp --reflink=auto --sparse=always` 复制仍在线的 writable 镜像。该路径没有暂停 VM 或冻结文件系统。`checkpoint_dax.py:archive_upper` 将副本以 `loop,ro,noload` 挂载，再打包 `/root`；`build_layer` 解包至新层 `/delta`。

本次入口 `bash scripts/35-check-online-ch-upper.sh` 使用同一 cp 命令及实际 `archive_upper/build_layer`。为避免碰触既有存储，每轮在私有 mount namespace 中创建本项目的 768 MiB 稀疏 Btrfs 镜像，内部放置 128 MiB ext4 writable 镜像。CH 使用既有固定二进制、guest 6.1.134、2 vCPU/4096 MiB、virtio-blk 默认 I/O 参数，无网络。二进制、内核、busybox、执行源码的 SHA-256 与完整启动参数均归档。

guest 创建两个文件，初始为 `0/0` 并执行 `sync`；用 `dd conv=fsync` 覆盖第一个文件，确认 guest 读到 `1/0`，输出串口标记后等待 15 秒。host 见到标记才复制，并确认复制结束时 guest 尚未完成等待、VMM 仍在线。之后 guest 将第二个文件写为 `1`，同步并关机；host 验证父镜像为 `1/1`。

复制期间没有主动写入；测试没有运行 OverlayFS、完整 `/tmp` 归档或 guest drop_caches，也不代表两个文件构成应用事务。它专门检查已确认文件写入在在线磁盘捕获后的可见性。host 没有全局 sync/drop_caches、服务重启或共享模板修改。

## 证据

原始目录前缀为 `.artifacts/online-ch-upper/`。

| run_id | 配置与观察 | 判定 |
| --- | --- | --- |
| `20260926T025613Z-1844207` | 初始 sync 后写入并 fsync；guest `1/0`，封存 `0/0`，父镜像最终 `1/1` | `copy_fidelity` FAIL；其他执行阶段、关机和清理 PASS |
| `20260926T025707Z-1844746` | 增加日志恢复诊断，但 `loop,ro` 将 loop 设备也设为只读，恢复挂载失败；内核提示 recovery required / write access unavailable | 诊断实现 FAIL；父 VM 提前终止，不能计正常完成；清理 PASS |
| `20260926T025739Z-1845068` | 对独立副本采用可写挂载恢复日志；原封存 `0/0`，恢复日志后 `1/0`，父镜像 `1/1` | 保真 FAIL；日志诊断、父 VM 完成和清理 PASS |
| `20260926T025809Z-1845435` | 第一个文件 fsync 后、发标记前再执行一次 guest sync；封存与日志恢复副本均为 `1/0`，父镜像 `1/1` | 全部检查 PASS |

最后两轮使用同一脚本版本，仅 `ONLINE_CH_SYNC_BEFORE_CLONE` 不同。每轮保存 `result.json`、`captured-state.json`、`parent-state.json`、串口、命令 stdout/stderr/退出码、启动参数、配置、源码及原始镜像；日志恢复轮另有 `replayed-state.json`。原始捕获镜像保存在各轮 Btrfs 镜像内；日志恢复仅作用于它的另一个副本。`host-cleanup-audit.json` 是运行后独立检查，无关联 loop、当前 namespace 挂载或 VMM 残留，所有轮次均 PASS。

前置 host loop 模拟保留在 `.artifacts/online-upper-consistency/`：最终未追加 syncfs 的 `20260925T161349Z-1832026` 为保真 FAIL（封存 `0/0`），追加 syncfs 的 `20260925T161352Z-1832139` 为 PASS（`1/0`），父状态均完成为 `1/1`。首轮 `20260925T161143Z-1830974` 为校验器读取了不存在的 `delta/root` 路径，后已改为实际 `delta`；不能归为封存缺陷。其他中间失败和成功记录均保留，未覆盖。CH 结果提供了真实 guest 路径的独立证据。

复现命令：

```bash
# 预期能暴露保真问题；FAIL 是实际验收结果，不能改标 PASS。
bash scripts/35-check-online-ch-upper.sh
# 写入后追加 guest sync 的对照。
ONLINE_CH_SYNC_BEFORE_CLONE=1 bash scripts/35-check-online-ch-upper.sh
```

这是确定边界的 smoke，不是失败概率或性能统计实验；尚不能把单次同步对照通过当作并发写入下的一致性保证。

## 结论与下一步

现有同步与复制之间允许新写入，而 `ro,noload` 可能遗漏已经 fsync 的更新。受控实验证明日志恢复能找回本例更新，因此不能把在线复制成功、tar 成功或新 VM 启动成功单独作为文件保真 PASS。

下一阶段先确定并验证采集协议，再继续完整 TrEnv-X 生命周期：停止任务产生的新写入、同步并在受控边界捕获；同时在独立捕获副本上诊断/恢复日志，保留原始副本及错误。需要分别验证后台写入、重命名/删除、OverlayFS 元数据、`/tmp` 与 rootfs 的跨阶段一致性，并记录暂停、同步、复制、日志恢复各自耗时。追加 sync 本身不消除随后写入的竞态；日志恢复也不提供跨文件应用事务或进程连续性。

本轮完成脚本 Python AST 和 bash 语法检查。未运行完整轨迹、并发压力或独立任务测试；未安装依赖、修改系统配置或提交/推送 Git。
