# 两代槽位文件的写入端回归
基线：handoff-20261118，`dd9ba4c9ebae59db68055da5e8ad566b87d6eb16`。基线离线检查 `bash scripts/check-platform-offline.sh` 退出码 0，末行 `status=PASS scope=platform-offline`。修正在草稿 `6bca996` 上叠加。

## 修改与实测
在 `patches/agentenv-provider-slot-lifetime.patch` 中，仅给两个使用栈内存的测试构造器补上 `publication: None` 和 `_data: None`：`negotiated_get_data_retains_only_until_next_overwrite` 与 `reuse_failure_and_kernel_overwrite_invalidate`。它们不发布文件，也不持有临时文件路径。原有 ManuallyDrop 与断言均未改。相对草稿，生产代码、读取端和测量规则没有变化。

在上次的 AgentENV 构建目录执行同一命令：
```sh
cargo test --offline --locked -p uvm-ublk --lib slot_observation --jobs 4
```
退出码 0；9 项通过，0 项失败，0 项忽略。其余 18 项不属于这次的名称过滤范围。[测试输出](rust-tests.txt)

## 关键断言能证明什么
`retiring_one_publication_does_not_remove_its_successor` 在同一进程、同一临时目录，用相同镜像、设备号、队列号和槽位号调用真正的 SlotObservation::create 两次；两个对象同时存活。测试断言两个发布路径不同；释放旧对象后，它的 JSON 和 BIN 都不存在，新对象的两个文件都仍存在；再释放新对象，目录为空。它确实覆盖了“旧代正常退出不误删新代”，而不是用构造空字段的两个测试来代替这一证明。

测试检查的是文件存在与否，没有核读新文件的内容，也没有启动真实设备服务、模拟异常退出或执行快照恢复。要确认同一快照可连续恢复，仍需在磁盘预算满足后，用修正版 provider 执行一次三批、每批七支的连续恢复与暂停，逐批核对实际绑定设备、完整槽位、释放结果以及是否出现 File exists。本轮未执行。

## 磁盘（只列候选，不清理）
检查时宿主可用 67,514,089,472 字节，约 62.88 GiB。原预算（scripts/isolated_storage.py:19，七支驱动传入 64 GiB）要求严格大于 90 GiB：实验卷 64 GiB，宿主保留 20 GiB，额外余量 6 GiB。因此还需释放超过约 27.12 GiB，并在实际运行前重查。

已核实的一项候选就足量：asb-seven-process/.artifacts/storage-isolated-service/20261121T034046Z-1477264/volume.ext4，实际分配约 64 GiB。这是此前五批失败现场的卷，不是可随意删除的缓存；只有确认不再需要现场、完成必要归档并获得删除授权后才考虑回收。未删除卷或结果。
