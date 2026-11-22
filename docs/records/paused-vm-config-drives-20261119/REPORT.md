# 暂停后配置中的六个 drive：磁盘驻留已读，四来源计时在 PFN 入口停止

基线 `dd9ba4c9ebae59db68055da5e8ad566b87d6eb16`（handoff-20261118）；`bash scripts/check-platform-offline.sh` 退出 0，末行 `status=PASS scope=platform-offline`。单恢复分支、一次运行，未执行 guest 导出。**实测退出 1，没有四来源墙钟。**

恢复返回 854559274199331 ns，暂停请求 854559278663205 ns，HTTP 204 完成 854559279117170 ns（宿主单调时钟）。返回至请求 4.464 ms，PATCH 0.454 ms，返回至暂停完成 4.918 ms。绑定原 VMM 并确认 Paused 后，只从该套接字的 GET /vm/config 响应读取 drives；配置读取 0.397 ms。完成快不等于观测到 guest 缓存峰值。

## 实际清单与可完成的逐对象查询

所有 path_on_host 均相对于该 VMM 的 cwd。下表长度：普通文件用 stat；块设备的 st_size 为 0，实际容量以 BLKGETSIZE64 为准。设备一栏为 st_dev；主:次为块设备 st_rdev。段为单次 mincore 覆盖的连续 4 KiB 页；本次每个对象的各段等长，故最短、中位、最长均等于表中段长。

| drives[].drive_id | path_on_host | 设备 / 主:次 | inode | 长度 B | 页次 | 驻留页 | 调用数 × 段长 | mincore ms | 文件窗口 ms |
|---|---|---|---:|---:|---:|---:|---|---:|---:|
| agentenv_volume_slot_0 | `agentenv_volume_slot_0.img` | 1797 / 普通文件 | 655375 | 4,096 | 1 | 0 | 1 × 1 | 0.014 | 0.123 |
| rootfs | `rootfs.ext4` | 6 / 259:0 | 30387 | 68,719,476,736 | 16,777,216 | 3,196 | 256 × 65,536 | 193.550 | 226.201 |
| agentenv_volume_slot_3 | `agentenv_volume_slot_3.img` | 1797 / 普通文件 | 655378 | 4,096 | 1 | 0 | 1 × 1 | 0.004 | 0.042 |
| user_rootfs | `user-rootfs` | 6 / 259:1 | 30397 | 68,719,476,736 | 16,777,216 | 0 | 256 × 65,536 | 111.824 | 136.440 |
| agentenv_volume_slot_2 | `agentenv_volume_slot_2.img` | 1797 / 普通文件 | 655377 | 4,096 | 1 | 0 | 1 × 1 | 0.002 | 0.016 |
| agentenv_volume_slot_1 | `agentenv_volume_slot_1.img` | 1797 / 普通文件 | 655376 | 4,096 | 1 | 0 | 1 × 1 | 0.002 | 0.013 |

六个 drive 共 33,554,436 页次，其中 3,196 页返回驻留位。516 次 mincore 合计 305.397 ms（0.0091 µs/页）；第一次调用进入至末次返回 363.493 ms；含映射、计数与解除映射的整个文件查询窗口 **363.588 ms**（0.0108 µs/页）。以上均为磁盘对象的先后读取，不是页号并集。

## drive、转换后的文件和 RAM 不是同一层

- `rootfs.ext4` 是只读启动盘（is_root_device=true），实际为 259:0 块设备；本批服务日志将 /dev/ublkb0 关联到 `/workspace/env/deps/tools/0.1.1/image.json`。不是按后缀推断的 ext4 普通文件。
- `user-rootfs` 是可写用户磁盘（is_root_device=false），259:1。它是逻辑设备，若要知道背后的文件，还须读本实例 `cwd/overlaybd/image.json`。现场配置有 20 项 lowers，含 `../../../snapshot-store/repository/managed-layers/sha256_….overlaybd.commit`，并有 upper.data/index 等字段。其余四个 drive 是各 4 KiB 的卷槽位文件。未把下层普通文件加进 drive 扫描。上轮 19 个文件本来就是转换后的 overlaybd.commit，并非未转换的镜像包。
- 从同一 VMM 的实际 RAM 映射取得 **/dev/ublkb2**（st_dev=6、inode=30401、st_rdev=66306，即 259:2），与六个 drive 的身份匹配为 **0/6**。本次 `snapshot-local-cache/runtime/01a1f25f-4779-76f1-aae0-3aa845122576/memory/image.json` 的 lowers 指向 `/workspace/env/snapshot-store/repository/managed-layers/sha256_b03f1fc2d7a79fa0d6a1963f7efdcde640f7d464d6fc96eddfe3a7d0a1473180.overlaybd.commit`（st_dev=1797，inode=524549，301,535,232 B）；已核实本批 provider 打开此 inode。配置路径和 inode 已找到，并非“快照不存在”。

## 停止点与比较边界

按配置读取磁盘驻留后，在 PFN（物理页号）步骤，现有 HostFilePfnQuery 打开 `rootfs.ext4` 时返回 **ELOOP / errno 40**：该入口使用 O_NOFOLLOW，配置路径末端是符号链接。此前允许跟随链接的 stat、容量查询及 mincore 已成功。没有绕过或重试；即使只讨论目标类型，该入口也要求普通文件，而目标是块设备（`scripts/host_file_pfn_query.py:131–134`）。原 RAM 直接后备 PFN 查询另以 VMM 的 RAM 映射绑定对象，不是任一 virtio 磁盘（`scripts/snapshot_cache_residency.py:227–245`）。首个实际错误是打开链接，不是内核拒绝暂停状态下的 PFN 系统调用。

| 来源 | 调用与耗时结论 |
|---|---|
| 配置 drive | mincore 如上；PFN 打开失败，未发出 PFN 查询，无每页 PFN 耗时 |
| 快照内存下层 | 已绑定文件，停止后未做 mincore/PFN 查询 |
| provider | 已核对文件持有者；未读 cachestat 或槽位记录 |
| VMM 页表 | maps 仅用于核对设备；未查询 smaps_rollup 或 pagemap |

**是否在 1 秒内完成四来源：没有完成，无法判定。** 只有磁盘 mincore 可以与上轮全文件窗口的 0.0755 µs/页并列；没有 PFN 实测，不能将两轮之差分解为 PFN 开销。已执行的磁盘 mincore 页次是 645,238 的 **52.00 倍**。这是两块 64 GiB 逻辑设备各扫描一次的页次；后者是历史七分支 RAM 候选页在后备与下层查询两遍的页次。差距主要是对象、范围和遍数不同，不是分支数换算。完整四来源的页次比无值。

原始证据：`.artifacts/storage-isolated-service/20261119T055434Z-806663/guest-cache/result.json`、同批 `service-logs/server.log`、平台 `result.json`；[可复核数据](measurement.json)。恢复分支已恢复 Running 后删除，快照、私有服务、挂载和 loop 已清理，专用 slice 已释放。24 GiB 失败卷按原策略保留在 `.artifacts/storage-isolated-service/20261119T055434Z-806663/volume.ext4`，没有删除。未改页级采集器，未追加轮次。

下一步建议（未执行）：若目标仍是原 RAM 四来源，应以本次已识别的 RAM 后备设备和配套内存快照为对象，而不是以 drives 作为唯一清单。若目标改为虚拟磁盘自身的 PFN，则需单独验证块设备 PFN 入口，两类测量不能混称。
