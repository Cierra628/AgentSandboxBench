# 文件层封存与 DAX 重建保真验证（2026-09-26）

学长的 `archive_upper`、`build_layer` 和 `link_layers` 已通过两代文件状态封存检查；实际输出的两层也在指定 Cloud Hypervisor、guest 6.1.134 上以只读 DAX lower 重建成功。验证对象是干净卸载后的 upper，不覆盖运行中 VM 镜像采集的一致性，也没有启动完整 TrEnv-X 服务。

## 本轮验证

入口 `bash scripts/32-check-layer-fidelity.sh` 在独立 mount namespace 中创建本项目 ext4 upper，实际通过 OverlayFS 操作产生 whiteout 和 opaque 目录，再调用原有封存函数。基础目录、loop 镜像、挂载点和临时目录均归本次 run_id 所有。

第一代修改文件、删除文件、删除并重建整个目录，同时创建指定 UID/GID、权限、access ACL、user xattr、符号链接和硬链接。封存后作为新 lower 重建，比较可见路径、内容 SHA-256、类型、权限、属主、xattr 及硬链接身份；同时逐项比较原 upper 与层中 `/delta` 的元数据。

第二代继续删除第一代文件、显式重建已删除文件，再次替换 opaque 目录，并加入可执行文件和目录 default ACL。第二代封存后按“第二层 → 第一层 → 基础层”重建，比较同样的内容和元数据。第一层哈希未改变，`link_layers` 生成的两个继承层与原层保持相同 inode。

入口 `bash scripts/33-check-sealed-guest.sh LAYER_FIDELITY_RESULT` 把实际生成的两层接入无网络 VM。guest 使用 2 vCPU、4096 MiB，基础层和两代 checkpoint 均挂载 `ro,dax=always`，upper 为独立 virtio-blk ext4。通过生成的逐项检查核对全部可见路径、内容、权限、UID/GID、符号链接和硬链接身份；guest 正常关机后再核对层镜像哈希不变。ACL/xattr 在 host 中校验，guest 未独立读取 ACL/xattr。

## 结果与证据

| 阶段 | 结果 |
| --- | --- |
| 第一代封存与重建 | 内容、whiteout、opaque、权限、access ACL、xattr、链接全部 PASS |
| 第二代覆盖与继承 | 删除不复活、显式重建可见、default ACL 与执行权限保留，PASS |
| 不可变层及 hard link | 第一层哈希不变，两层 inode 继承 PASS |
| guest 6.1.134 DAX 重建 | 可见路径、内容、权限和链接检查 PASS |
| 清理 | namespace 内无残留挂载、host 无关联 loop device，VMM 正常退出 |

最终代码证据：

- `.artifacts/layer-fidelity/20260925T160139Z-1829391/`：两代 host 验证及 `host-cleanup-audit.json` PASS。`before.json`、`after.json`、`second-before.json`、`second-after.json` 与空差异表保留；原始 upper、tar 和层镜像均保留。
- `.artifacts/sealed-guest/20260925T160149Z-1829552/`：使用上述实际层，guest 重建 PASS；串口日志、生成的 guest 检查脚本、期望 manifest、启动参数、VMM/内核哈希和镜像哈希均归档。

前期记录也保留：首代 host smoke `20260925T155650Z-1827393` 和首次两代 host 测试 `20260925T155754Z-1827829` 均 PASS；首次 guest 测试 `20260925T160030Z-1828989` 为 FAIL。原因是把基础目录转换为 ext4 时，mkfs 自动生成了原 fixture 没有的 `lost+found`，使路径集合多了一项；只在本次生成的基础镜像中移除该目录后，`20260925T160058Z-1829190` 通过。这个失败不是 checkpoint 内容丢失。

## 代码变化与结论边界

封存函数原来执行全机 `os.sync()`，本轮改为 `fsync` 本次层镜像及其父目录，保留只针对该镜像的 `POSIX_FADV_DONTNEED`。修改已收录到 `patches/incrementaldax-platform.patch`；源码检查和补丁反向应用检查通过。该验证未测试掉电或进程崩溃耐久性。

此前怀疑解包未显式给出 `--xattrs-include=*` 可能丢失 OverlayFS trusted 属性；实际 `trusted.overlay.opaque` 和 `trusted.overlay.origin` 均被保留，原解包逻辑没有修改。不能把这一未复现的猜测列为已发现缺陷。

本轮没有修改宿主机挂载配置、服务、网络或学长既有模板，没有清全局缓存。所有原始数据仅在本地；没有提交或推送 Git。

后续已完成[在线采集边界 smoke](ONLINE_UPPER_CONSISTENCY.md)：真实 CH guest 初始 sync 后的 fsync 写入，在 `ro,noload` 封存时读到旧值，同一捕获镜像的副本恢复 ext4 日志后读到新值。写入后再 guest sync 的对照通过。干净卸载 upper 的上述 PASS 保持有效，但不能推广至在线捕获；下一步先验证同步、阻止新写入及副本日志恢复协议，再推进完整 TrEnv-X 生命周期。
