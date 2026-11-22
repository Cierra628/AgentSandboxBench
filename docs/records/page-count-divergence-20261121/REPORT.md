# 为什么诊断批首份清单只有 13,605 条？

**已定位到 guest 的缓存工作集，而不是导出器截短清单。** 两次使用相同基础镜像、内核和 16 MiB 校验流程，但分别启动源沙箱、各保存一次快照。诊断批到首次导出时累计只加入过 17,712 页，基线为 34,957–36,723 页；诊断批累计移出的页也更少。差额主要是**加入得少**，不是诊断批多回收了一万多页。

还能把范围缩小一步：基线每支至少有 **20,931** 条首份记录是在恢复后新加入且留到首查的，已超过诊断批首份清单的**全部** 13,605 条。两批恢复后的 guest 活动确实不同，差异不可能全用“少继承了一批旧页”解释。最有证据的解释是系统服务启动、读文件的进度不同；没有逐文件读数，不能断言是某个服务或某次回收所致。

## 1. 哪些配置相同，哪些不同

| 项 | 基线五批与诊断一批的核对结果 | 依据（路径相对各自实验目录） |
|---|---|---|
| 源沙箱与恢复 | 同一固定 Prettier 镜像；2 vCPU、4096 MiB、SMT 关闭、跟踪脏页。源创建超时参数 1800，关闭 guest 互联网；七支分别以 `start SNAP --detach --timeout 1800` 恢复。 | 创建参数见 scripts/agentenv_cold_start.py:create、run_rollout_cache_calibration.py:create_source；实际规格见源实例 service-logs/serial/*/firecracker.log 的 /machine-config |
| 平台启动 | 同用 repaired-five：seven-restored、immediate-restores、guest-cache-unified-rounds、paused-query-processes=7、child-count=7、rollout-sessions、restored-lower、provider-get-data；14 个 PID 专属探针前置。isolated-config.toml 相同，含关闭服务启动预热；这不等于 guest 内部服务已停止启动。 | scripts/observe_paused_process.sh、result.json.config_overrides |
| 实验诊断开关 | **不同**：基线无 ASB_FIRST_FRAME_STUDY，诊断设为 diagnose，系列从五批限为一批，恢复驱动安装宿主 provider.snapshot 计时包装。安装发生在子驱动启动时；包装的分段计时和 GC 回调在调用 snapshot 时才执行，源快照在两者之前就已制成。 | scripts/seven_process_repeats.py、上轮 profile_build.py；批回执 guest-cache/result.json |
| 镜像、依赖 | 基础镜像 manifest 与 OCI 配置摘要均相同（见下）。两份 copied-input-hashes.json 完全一致，包括 19 个本地 lower 的输入；种子文件相同。 | child-cache-local-input/local-no-proxy-metadata/manifest.json、result.json.child_cache_offline / immutable_cache_seed |
| 内核和服务 | guest 内核均为 6.1.175-asb-cache-v3；内核、provider 摘要见 snapshots.csv，两份诊断 server 副本逐字节相同。源 VM 的 /boot-source 参数相同，均启用 damon_reclaim，不能从“已启用”推断它回收了多少。 | result.json、诊断 server 文件、源实例 firecracker.log、各支 marker.json |
| 内容校验和导出 | 源执行 true，再写 16 MiB 的 A、fsync、全量读回；恢复支先逐字节验证，再重写、fsync、读回，随后上传并启动导出器。没有等待 systemd 启动完成的条件。42 份导出器源码回执均为 410695460ad2ff6301905e24e36a57b77e636657cee8dfa3a566f5c6aeba7edf；42 份首读都是请求 1、发布 1。 | seven_process_repeats.py、run_seven_paused_serial.py:install、seven_restored_pages.py:CONTENT/WRITE；guest-cache/try-*/live-cache/*/source-sha256.json、result.json |

## 2. 基础镜像相同，运行时快照不是同一份

| 实测字段 | 基线 | 诊断 |
|---|---|---|
| 保存开始 → 完成（2026-11-21 北京时间） | 13:59:32.980201 → 13:59:33.199287 | 15:44:10.219144 → 15:44:10.504469 |
| 恢复实际加载的快照 UUID | 01a1fcb0-464f-7140-9f1f-b78e1c0023fa | 01a1fd10-0f08-7cc0-89bc-bc08666d29b2 |
| 内存层字节数 | 301,178,880 | 301,617,152 |
| 内存层 digest（sha256） | 265379fcd87cb7778b0620ec94dd04e22aef535bb0ef6db3a196c7aae99f1ece | 585aa9bacb81f029f0a459235c9499859fbc6f0f121f6ab8d6f1f36c1abcea6f |

保存时刻取 service-logs/server.log 的 capturing sandbox snapshot 与 snapshot captured，不是目录名时间；UUID 同见恢复实例 firecracker.log 的 /snapshot/load → snapshot_path。大小、摘要取 try-*/shared-device/lower-metadata/ownership.json 的 layer.size、layer.digest，五个基线批均指向同一层。[身份对照 CSV](snapshots.csv) 保留源实例 ID、快照名和原 UTC 时间。

**镜像配置 digest**：两批基础镜像 manifest 都是 sha256:8159d38fcbd6d5f09402d93d3e15cf2a891058ddd835a224f1d6b3357a87cc94，其 config.digest 都是 sha256:973298a5771926f902291e2afbd3e88972c465a18e47b1b45743a4d1434863b2。这不是运行时 memory/image.json 自身的字节摘要；ownership.json 记录的是该配置的路径和所引用层的摘要。不同内存层足以判定不是同一快照，但其大小不能换算成 guest 缓存条数。

## 3. 首次导出：同样等约 898 ms，页数仍相差一倍

下表每格为**实测记录数 / 恢复命令返回至首份读回的毫秒数**（取整）。[逐支时间线 CSV](first-exports.csv) 给出全部 42 支的恢复发出、返回、内容核对完成、可发请求、首份读回宿主单调时钟 ns，以及 guest 内部导出起止 ns。ready 是可发请求，不冒充实际写入请求的时刻；guest 时钟不与宿主时钟相减。

| 支 | 基线1 | 基线2 | 基线3 | 基线4 | 基线5 | 诊断1 |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 29473 / 906 | 30495 / 2023 | 29101 / 939 | 30335 / 1788 | 30216 / 1955 | 13605 / 876 |
| 2 | 29164 / 859 | 29148 / 797 | 30019 / 1865 | 30492 / 1807 | 30278 / 1650 | 13605 / 896 |
| 3 | 29249 / 944 | 30345 / 2089 | 30193 / 1903 | 28739 / 814 | 29433 / 1014 | 13605 / 907 |
| 4 | 30264 / 1915 | 30438 / 2067 | 30397 / 1781 | 30309 / 1430 | 29421 / 926 | 13605 / 835 |
| 5 | 30280 / 1870 | 30256 / 1968 | 30143 / 1839 | 29292 / 884 | 29187 / 1020 | 13605 / 898 |
| 6 | 30223 / 1883 | 30311 / 1840 | 29315 / 898 | 29326 / 921 | 30188 / 1965 | 13605 / 737 |
| 7 | 29181 / 838 | 30242 / 2082 | 29187 / 789 | 29139 / 845 | 29340 / 837 | 13605 / 817 |

基线第 3 批第 6 支是 898.385 ms、29,315 条；诊断第 5 支是 898.049 ms、13,605 条。相近的导出间隔可以对应很不同的工作集。独立读取的 guest /proc/meminfo 的 Cached 也从基线 118,042,624–124,841,984 B 变为诊断 55,726,080 B。两批 before.bin 的长度与头部 records 匹配，均无重复加入、移除缺失、越界或 stale 记录。没有导出容量截断的证据。

## 4. 从缓存事件区分“加入少”与“移出多”

**由原始计数推导。** 诊断内核补丁 patches/agentenv-cache-metadata/linux-6.1.175.patch 的 add_locked 和 remove_locked 都让 generation 增 1，records 分别加 1 和减 1；初值为零，42 支的 moved_pages 均为零。故从该 guest 启动（包括快照继承）至首份清单，累计加入 = (generation + records)/2，累计移出 = (generation − records)/2。诊断为 **17,712 − 4,107 = 13,605**；基线累计加入 **34,957–36,723**，移出 **6,218–6,228**。这是累计事件，不是不同文件数。扣除 shmem 后，诊断仍有 13,392 条，基线为 28,454–30,209 条，差额主要在非共享内存的文件缓存。

**由不同分支间的记录给出恢复后加入量的下界。** 每次加入时，内核把唯一的 generation 写入该页的 tag（最低位另表 shmem）。同一快照中某代若已分配，不可能在其两个恢复支里指向不同 guest 页号。逐份解码 before.bin，把 tag 右移一位，用同一快照下各分支第一次出现分歧的代号作界：

- 基线第 11,925 代在第 1 批第 1 支对应 guest PFN 1,063,051，在第 4 批第 5 支对应 1,064,448。因此这代及以后的所有加入都在该快照分叉之后。各支首份中仍存活的此类记录为 **20,931–22,687**。
- 诊断第 16,252 代在第 4、5 支对应 PFN 1,073,036、1,073,022；每支至少 **5,568** 条在恢复后加入。此数是下界，不能用两个下界相减来估计工作集差。即使用诊断的**总数**作其恢复后新增量上界，基线每支仍至少多 **7,326** 条恢复后加入且留到首查的记录。

**最有证据的解释（推断）：** 恢复后的系统启动活动不同。恢复实例各自的 service-logs/serial/<sid>/firecracker-stdout.log 中，基线 35/35 有 systemd 创建 getty slice 和启动 Snap Daemon 的输出；诊断 0/7 有这两条。导出器在基线的 guest PID 为 661–777，在诊断七支均为 356。日志、PID 和缓存加入量指向同一方向，但串口无输出不证明服务没有运行；也没有服务级读文件记录，不能点名 snapd 贡献了差额。两个源都按“16 MiB 核对后保存”，不是按同一个系统启动里程碑保存。现有证据能确定恢复后的新增量不同，不能确定是保存时已处于不同启动阶段，还是恢复后进度才分开，或二者兼有。

## 5. 还缺哪一个观测，怎样最小补采

真正缺的是**同一条时间线上的中间读数**：两份源快照保存前的 records/generation、恢复后第一次可执行 guest 命令而尚未校验时的读数、16 MiB 核对后但尚未安装导出器的读数，以及对应的 guest 单调时钟和系统启动状态。现有首份读数在最后一步之后；内容正确不说明两支读文件前的缓存相同。

建议的最小补采（未执行）：独立创建 **2 个源，各保存一次，各恢复 1 支**；保留同一写入、恢复校验和导出路径。不挂页查询探针、不跑四来源。源保存前、恢复首次可执行时、16 MiB 核对后、首份发布时分别记上述计数和 Cached/Shmem，连同 systemd 正在启动的服务、主要服务累计读量及快照 UUID。采集动作和顺序两组相同，并记录其自身扰动。若两份源保存前已分开，判定起点不同；若此前接近，找出首次出现净加入量差的区间，再用同窗服务读量区分背景启动和显式校验/导出。若两次未复现高低工作集，仅报告未复现；不据此追认历史原因。若单支没有复现而需检验七支争用，再另行决定，不自动扩批。
