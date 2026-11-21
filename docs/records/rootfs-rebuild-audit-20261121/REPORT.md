# 20 个 rootfs 层中，有一个不能从原始镜像取回

基线 handoff-20261118，`dd9ba4c9ebae59db68055da5e8ad566b87d6eb16`；基线离线检查退出码 0，末行 `status=PASS scope=platform-offline`。目标是五批共用的 20261121T034046Z-1477264/volume.ext4。仅用 debugfs 只读读取、检查现有文件与日志，没有恢复、转换、复制层或删除数据。以下日期时间均为北京时间。

**决定：若需完整保留本次 rootfs，先留卷。** 前 19 层可从另一份已验证的种子卷逐字节恢复；第 20 层是本次保存源沙箱时生成的可写层提交，不在原始镜像中，也未找到卷外副本。要解除这个阻碍，优先保留第 20 层和完整的 rootfs/image.json，不需要为此另存 19 份基础层。这个结论要求继续保留 10 月种子卷；尚不构成删除授权。

## 逐层数据与父子关系

[20 行逐层表](layers.csv) 按挂盘顺序给出**完整卷内路径、字节数、实际 SHA-256、UUID 与父 UUID**，以及 OCI 源 blob、实际 Docker 层目录、转换索引、转换器、种子卷内的精确取回路径和判断。所有 20 个文件均完整流式读取并核对配置大小与摘要；总计 5,582,672,896 字节。表中的卷内路径以 /env 开头，服务容器里相应前缀为 /workspace/env。

- 快照配置在 /env/snapshot-local-cache/runtime/01a1fc31-83b2-7270-aada-2a36f7ef5db0/rootfs/image.json。它为恢复后的 user-rootfs 提供 20 个有序 lower，upper.data 为空；同目录的 memory/image.json 只指已另行归档的 RAM 层，不是这些磁盘层的根或父层。
- 读取每个 OverlayBD 层末 4 KiB 的 LSMT 尾部，按 AgentENV/storage/overlaybd/src/lsmt/format.rs 的 uuid、parent_uuid 字段解码。第 1 层父 UUID 为全零，第 2–19 层的父 UUID **逐个等于前一个层的 UUID**。第 20 层自身 UUID 为 89e15f9d-5cce-4ef5-9a7e-0d92b8e62de8，父字段为空；它排在第 19 层之后的证据是有序 lowers 清单，而非尾部硬编码了第 19 层。它只保存变化，不能独立代替基础文件系统。
- 删除一个层不会改变其他文件的字节或哈希，但完整磁盘不能继续按原配置组装。中间缺层会破坏依赖该层的上层视图；只缺第 20 层仍可得到基础镜像，却缺本次源沙箱的写入。

## 1–19：原镜像、转换记录与同哈希恢复不是一回事

原始镜像为 `ghcr.io/timesler/swe-polybench.eval.x86_64.prettier__prettier-6604@sha256:8159d38fcbd6d5f09402d93d3e15cf2a891058ddd835a224f1d6b3357a87cc94`。本机 Docker 仍登记这个 digest，标签 mixfs/prettier-6604:exp2，对应 config ID 为 sha256:973298a5771926f902291e2afbd3e88972c465a18e47b1b45743a4d1434863b2。Docker 报 3,767,347,474 字节；原 registry-manifest.json 中 19 个压缩 blob 合计 1,601,815,575 字节。两者不是独占磁盘空间。逐层从 Docker RootFS.Layers、/var/lib/docker/image/overlay2/layerdb 的 chain ID 和 cache-id 核到实际存在的 /var/lib/docker/overlay2/.../diff 目录。Docker 保有本地解包镜像；没有据此认定本地另有一份完整的原始压缩 OCI 包。

19 个原始 blob 与 19 个 commit 的对应关系由各自 image-cache/indexes/...json 的 sourceDigest、commitDigest、size、converter 与镜像清单共同确认。转换器为 overlaybd-oci:v1.0.18-aenv.1:agentenv-cache-v1；user 镜像 8159…-image.json 的 19 个 lower 逐项等于本次前 19 项。10 月 8 日批次 20261008T102942Z-3642885 的 service-logs/server.log（含 18:30:03–18:31:19）记录已有层复用及缺层时启动后台镜像复制；不能给所有层指定同一次首次转换的进程或时刻。11 月 21 日批次 result.json:immutable_cache_seed 与 seed.json:parts 则记录了**本批是从种子读取转换产物**，不是重新冷转换，也不是证明当日又使用过 Docker 的镜像标签。

卷外独立来源是 /home/yyxie/asb-seven-process/.artifacts/storage-isolated-service/20261008T102942Z-3642885/volume.ext4.gz，2,245,185,148 字节，本次复核整个压缩文件 SHA-256 为 502b8bb6ac5244223034e1f961f64986f9eb41ab9b420e2bca26dd407335fe29。11 月 21 日实际从该种子准备的 seed.json 对每个 commit 记录了字节数和内容散列，**19/19 与本次实读完全相同**。原始 Docker 镜像不在待删卷内，但受 Docker 清理策略影响；种子也在 .artifacts 下，不是永久归档。没有证据表明计划删除二者，也没有保证其永远保留。删当前卷不删它们；若种子随后也被清理，就失去本报告已验证的同哈希取回路径。

**恢复方法（仅说明，未执行）：** 从上述工作区执行以下命令，OUT 为新的准备目录，DEST 为要重建的目录；遍历 CSV 的第 1–19 行。脚本校验 gzip 和解压卷，以只读方式暴露 seed.json 的 source_env。下例中的 H 是该行完整的实读 SHA-256。

```bash
python3 scripts/prepare_prettier6604_cache.py "$OUT"  # OUT 位于 .artifacts/prettier6604/ 下
ENVROOT=$(jq -r .source_env "$OUT/seed.json")
# 每层执行（DEST 已存在）
cp "$ENVROOT/image-cache/commits/sha256-$H/overlaybd.commit" "$DEST/sha256_${H}.overlaybd.commit"
printf '%s  %s\n' "$H" "$DEST/sha256_${H}.overlaybd.commit" | sha256sum -c -
# 全部完成后
python3 scripts/prepare_prettier6604_cache.py "$OUT" --release
```

这是**从已转换产物精确恢复**；若坚持只从 OCI 文件系统重新运行转换器，本轮没有覆盖这个执行路径，不能承诺输出同 SHA-256。重建视图和重建同一串字节应分别验收。

## 20：本次快照新增的 16,818,176 字节

文件为 /env/snapshot-store/repository/managed-layers/sha256_1d07f4ff0d04df56f7c80deb32379792650e7bf1f21b87c68421f2d5721c6c47.overlaybd.commit。原镜像的 19 个 lower 及种子 commits 清单都无该哈希。另一个镜像配置 e625… 虽也有 20 层，其末层是 469479…，不是本层。驱动 ea6f7eb 在源沙箱中写 /workspace/cache-session-file、刷盘、读回核对，再保存快照。设备日志 ublk-daemon.log 第 12–13 行：11:41:05.782062 开始、11:41:05.835071 完成 rootfs/snapshot.commit。这个增量含运行时文件系统变化；重写相同的 16 MiB 用户数据，不等于重复生成相同的块元数据和层 UUID。

对 /home/yyxie 与 /mnt/data 的普通文件按确切大小搜索，没有独立的同大小文件；不能排除未登记的外部备份或其他封装中的副本。现有证据下，**没有从原镜像生成同哈希第 20 层的命令**。

## 恢复成本与删卷条件

- 19 层复制输出需要 5,565,854,720 字节（约 5.18 GiB）。原准备脚本另用 tmpfs 放解压后的逻辑 20 GiB 种子卷；此前实分配 7,604,273,152 字节，脚本要求启动时可用内存大于 64 GiB。这段不是额外的持久磁盘占用。
- 历史种子准备实测约 76 秒（计时不含开始前的压缩包摘要检查）。按复制和重读校验各以 100–300 MiB/s 估算，再需约 35–106 秒；合计约 2–3 分钟，另加启动前读取 gzip 做摘要的时间。这里只估算复制已转换层；从零冷转换与重建第 20 层没有可信的同哈希时间估计。
- **本轮不删、不归档。** 若目标是保住全部层，下一步最小操作是另行授权保存第 20 层及 rootfs/image.json，独立核对 16,818,176 字节、完整 SHA-256 和清单顺序，并明确将外部种子列为保留项。只有在这些条件满足后，这 20 层才不再构成删卷阻碍。
