# 2026-10-28磁盘清理

本次按用户要求清理两处可重新生成的Cargo构建输出，共2.514 GiB：

- `.artifacts/host-full-grpo-build-20261024/target`
- `.artifacts/release-prep-v0.1-20261025/backend-build/target`

删除前重新检查目录身份、挂载和打开文件，均无使用者。保留目录外的`uvm-ublk-daemon`、源码、构建日志、工具链和依赖缓存；实验备份及原始结果未删除。影响是以后构建时需要重新编译，不影响现有已复制二进制的使用。

清理时可用空间由约48.059变为50.573 GiB；共享盘随后继续变化，因此不把全部可用空间回升算作本次释放量。此前获准删除的两份旧8 GiB失败卷是前一段操作，日志和原结果仍保留，不重复计入上述2.514 GiB。

清理后已满足此前授权的一轮Prettier #6604启动条件，该轮完整完成并清理，结束时48.712 GiB可用，见[结果及曲线](HOST_CURRENT_PRETTIER6604.md)。本次没有清理其他用户、Docker全局数据、源码或研究结果。

本机收据：`.artifacts/host-current-prettier6604-launch-20261028/build-cache-cleanup.json`；前一段卷删除收据为同目录`deletion-receipt.json`。
