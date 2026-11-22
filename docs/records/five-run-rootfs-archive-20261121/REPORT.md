# 五批共用卷：最后一个 rootfs 增量归档与删卷清单

基线 handoff-20261118，`dd9ba4c9ebae59db68055da5e8ad566b87d6eb16`。基线离线检查退出码 0，末行 `status=PASS scope=platform-offline`。所有时间为北京时间。

## 提取与独立校验

对五批共用的 volume.ext4 使用只读 debugfs，无挂载、无 loop、无卷上写入。归档根目录是 `/home/yyxie/research-archives/AgentSandboxBench/20261121T034046Z-1477264/`。它在实验清理目录之外，**与原卷同盘**，能防误随本次清理删除，不能防磁盘损坏。

| 文件（卷内完整路径） | 卷内字节 | 卷内 mtime | SHA-256（归档与源一致） |
|---|---:|---|---|
| /env/snapshot-store/repository/managed-layers/sha256_1d07f4ff0d04df56f7c80deb32379792650e7bf1f21b87c68421f2d5721c6c47.overlaybd.commit | 16,818,176 | 2026-11-21 11:41:05.781809938 | 1d07f4ff0d04df56f7c80deb32379792650e7bf1f21b87c68421f2d5721c6c47 |
| /env/snapshot-local-cache/runtime/01a1fc31-83b2-7270-aada-2a36f7ef5db0/rootfs/image.json | 8,876 | 2026-11-21 11:41:19.809650425 | 9a1a40845829f25583889e0ed99be8df2ee11e136e590cb20ac150e44f4a5218 |

第 20 层在归档根目录，保留原文件名；配置在其下的 rootfs/image.json，未覆盖原来的内存层配置 image.json。重新从卷读出第 20 层独立求摘要，与预期、归档三方一致。重新从卷读出配置与归档执行 cmp，退出码 0。

归档 SHA256SUMS 覆盖上述两项以及原内存层（0235b027fcb67d0e321110aaded54da6a5425068e1016c7b1d09f8466f5a49b5）和原内存层配置 image.json（411ab1e8b48b75d6d8fa71a43355094ce50c7806622f20d1dd9064db2a40c5d4）。

## 种子依赖

种子是 `/home/yyxie/asb-seven-process/.artifacts/storage-isolated-service/20261008T102942Z-3642885/volume.ext4.gz`，2,245,185,148 字节。原批 result.json:immutable_cache_seed.source_backup 与 11 月 21 日的 .artifacts/prettier6604/asbvmmproc20261121t033927/seed.json 都引用它；上次核实其压缩包 SHA-256 为 502b8bb6ac5244223034e1f961f64986f9eb41ab9b420e2bca26dd407335fe29。本轮再次直接从这个文件读取。种子不在待删卷内，但在 .artifacts 下，无永久保留保证。旧种子准备流程的 resources.json:released=true 表示临时挂载已清理，不表示 gzip 被删除。若扩大清理范围，必须排除此文件；否则会失去已验证的前 19 层逐字节恢复路径。

## 实际拼装结果

归档用户 yyxie 执行 `sha256sum -c SHA256SUMS`：4/4 OK。使用临时目录解开 20 GiB 逻辑大小的种子镜像，不挂载；按归档 rootfs/image.json 的原始路径层序，以 debugfs 从种子逐个实际取出前 19 层，最后复制归档的第 20 层，形成完整配置和其全部引用文件。逐个重新读取核对 SHA-256、字节数、层 UUID 和父 UUID：20/20 通过，共 5,582,672,896 字节，解压、拼装和校验合计 105.947 秒。首层父 UUID 为零，2–19 层各指向前一层，第 20 层父字段为空，按配置最后叠加。验证到可重建完整配置和全部层文件；未挂载合成磁盘、未启动虚拟机。临时拼装已清理。

## 删卷前最后清单——待授权执行

- 仅删除候选文件 `/home/yyxie/asb-seven-process/.artifacts/storage-isolated-service/20261121T034046Z-1477264/volume.ext4`，绝不删除父目录。提取后 losetup -j 对卷无输出，findmnt 对原挂载点无输出（退出 1）。删除前仍要重新核对路径、占用和归档清单。
- 保留上述卷外归档整个目录（含 SHA256SUMS、内存层与两个配置、rootfs 第 20 层、reference 等）和上述种子 volume.ext4.gz。
- 保留卷同级 guest-cache/ 的 834 个、service-logs/ 的 139 个、service-provider-slots/ 的 352 个非目录条目，以及 result.json、commands.json、分批回执与已有报告（ea6f7eb、4bf984a、95f3715、8669453、2bcc2d0 和本报告）。不能递归清理本批目录。
- 会失去：原 ext4 文件系统布局、原 inode、服务状态库、未整卷归档的运行现场，以及对该原卷继续取证的能力。保留材料能还原原始内存层与完整 rootfs 层配置，不等于保留一台可原地恢复运行的虚拟机。
- 临时拼装收尾后可用 67,137,064,960 字节（62.53 GiB）；候选卷实分配 68,720,115,712 字节（64.00 GiB）。若其他用量不变且文件不被打开持有，删除后预计 126.53 GiB，高于 90 GiB 门槛 36.53 GiB。这是预计值，未删除。
