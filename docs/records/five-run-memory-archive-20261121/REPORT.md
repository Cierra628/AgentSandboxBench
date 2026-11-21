# 五批共用卷：原始内存层已单独归档

基线 handoff-20261118（dd9ba4c9ebae59db68055da5e8ad566b87d6eb16），离线检查退出码 0，末行 `status=PASS scope=platform-offline`。对象是北京时间 2026-11-21 11:40:46 开始的五批共用卷。

**删卷所需的内存层归档已经完成。** 归档在实验目录以外，但和原卷在同一宿主磁盘；它能防止随实验卷被清理，不提供磁盘故障冗余。归档目录仅供本机用户 yyxie 访问，未将内存内容放入 Git 或交付下载链接。

## 提取：一个内存层，不是所有镜像层

原卷是独立 ext4 文件系统：blkid -p 返回 TYPE=ext4、UUID=11efaa1a-2510-47c8-9e75-f1a729de8ca5，块大小 4,096 字节。历史挂载记录为 /dev/loop5。本次用 debugfs（未传 -w）直接只读打开卷，用 cat 读取配置及源字节流、dump 提取普通文件；没有挂载，没有分配 loop。若需只读挂载此 ext4 格式，应先以只读 loop 关联，再用 ext4 的 ro,noload 选项挂载，避免日志回放；本轮未实际测试挂载。

- 卷内路径：`/env/snapshot-store/repository/managed-layers/sha256_0235b027fcb67d0e321110aaded54da6a5425068e1016c7b1d09f8466f5a49b5.overlaybd.commit`
- 原文件：301,404,160 字节；inode 1835269；mtime 北京时间 **2026-11-21 11:41:05.778809972**（卷内 inode stat，与原 metadata.json 的 mtime_ns 对照）。
- managed-layers 内有 21 个 commit。唯一的运行快照配置目录是 01a1fc31-83b2-7270-aada-2a36f7ef5db0；其 memory/image.json 只列一个 lower、无 upper 数据，rootfs/image.json 列 20 个 lower。本次只有一个内存候选；与保存的 provider 开文件归属记录相符。
- 归档目录：`/home/yyxie/research-archives/AgentSandboxBench/20261121T034046Z-1477264/`；层文件名与卷内相同。同步保存原 memory/image.json（归档名 image.json）、mem-overlaybd-global.json，以及 reference/ 下原测量时的归属记录、层元数据、头、尾、索引副本。

## 校验与还原

| 校验对象 | SHA-256 |
|---|---|
| 卷内原文件：直接读取全部字节计算 | 0235b027fcb67d0e321110aaded54da6a5425068e1016c7b1d09f8466f5a49b5 |
| 卷外文件：独立重新打开，sha256sum -c SHA256SUMS 返回 OK | 0235b027fcb67d0e321110aaded54da6a5425068e1016c7b1d09f8466f5a49b5 |

两者与原配置 lowers[0].digest 三方一致。用项目 RawMemoryLayer 单独解析归档文件成功：它是带索引的 OverlayBD 内存层，不是平铺的 RAM 转储。虚拟大小 4,294,967,296 字节，索引在偏移 301,395,968，有 256 个表项、74 个非空段。归档文件内的头、尾、索引与 reference 中原采集的对应字节逐项一致，不依赖卷上的另一份偏移表。

还原层文件时，先在归档目录执行 `sha256sum -c SHA256SUMS`。按原配置使用时，在目标工作区把 commit 放回 image.json 的 lowers[0].file 所指位置（服务内 /workspace/env/snapshot-store/repository/managed-layers/），把 image.json 放回 /workspace/env/snapshot-local-cache/runtime/01a1fc31-83b2-7270-aada-2a36f7ef5db0/memory/。也可直接把归档 commit 交给 RawMemoryLayer 读取。已验证的是原层字节和索引的可恢复性，不是完整虚拟机启动；配置中出现的服务路径不会因复制文件而自动重建。

## 删卷前最后清单（未删除）

1. **唯一目标**：`/home/yyxie/asb-seven-process/.artifacts/storage-isolated-service/20261121T034046Z-1477264/volume.ext4`，不是其父目录。文件长度 68,719,476,736 字节，stat 显示分配 134,218,976 × 512 字节，即约 64 GiB。
2. **保留**上述归档，以及与卷同级的 guest-cache/（834 个采集条目）、service-logs/（139 个日志条目）、service-provider-slots/（352 个槽位条目）、结果回执、已交付报告。数量依据上一轮 95f3715 的卷内外清单核对。不要删除这些目录。
3. **会失去**原 ext4 现场，包括其文件系统布局、原 inode、服务状态库、未整体归档的 rootfs 层和运行目录，以及从原卷继续取证的能力。没有整卷备份；其他来源可能有相同的基础镜像，但本轮未验证。此次备份范围是本批原始内存层与配套元数据，不是可直接重启的整机快照。
4. **当前占用**：提取结束后，`losetup -j "$V"` 退出 0、无输出；`findmnt -rn --mountpoint "$D/mount"` 退出 1、无输出（V 为上述目标，D 为父目录）。本轮未建立挂载，无需卸载。取得单独删除授权后，实际删除前仍须再次核对绝对路径、卷外归档校验、挂载和 loop 状态。
