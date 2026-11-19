# 暂停且禁止 guest 导出时，原四来源采集器停在哪里

基线：`handoff-20261118`，`dd9ba4c9ebae59db68055da5e8ad566b87d6eb16`。从标签检出独立分支；`bash scripts/check-platform-offline.sh` 退出 **0**，末行 `status=PASS scope=platform-offline`。

**现有四来源入口不能在“立即暂停、全程不做 guest 导出”条件下交付一个完整点。** 单恢复分支实际调用了原 `LiveTaskCache.start(restored, coordinated=True)`，启用 lower_cache 和 provider_cache。其第一个 guest 请求是 `live-cache-upload / upload`。驱动的禁止 guest 访问回调在上传执行前停止；没有在 guest 上运行导出器，没有查询其他对象。实验驱动退出 0 表示按约定记录了边界并完成清理；页级结果为 **UNAVAILABLE_REQUIRES_GUEST_EXPORT**。

| 实测事件（宿主单调时钟） | 时间 ns |
|---|---:|
| 恢复返回 | 854962086586306 |
| 发出 PATCH /vm | 854962090201531 |
| 暂停返回 HTTP 204 | 854962090558027 |

恢复返回至完成暂停 **3.972 ms**（请求前 3.615 ms，PATCH 0.356 ms）。读取状态为 Paused，并核对同一 VMM；原入口方法进入至被禁止的上传请求为 264.251 ms，含结束 KVM 注册跟踪，**不是四来源读窗**。使用既有 `seven_restored_pages.mapped_device` 识别本次 RAM 映射：`/dev/ublkb2`，st_dev=6、inode=30438、st_rdev=66306，2 段映射。仅核对身份，没有读设备的页。没有沿用旧 inode。

一次运行，无重试；恢复 Running 后删除分支及快照，私有服务、卷、挂载、loop 和专用 slice 均已清理。原始记录：`.artifacts/storage-isolated-service/20261119T060115Z-812249/guest-cache/result.json`；[本次入口回执](measurement.json)。

## 缺的是本实例当前缓存的页清单，而不只是设备名字

首次启动时，`LiveTaskCache.start` 先完成 KVM 注册记录采集，再调用 `_start_guest_exporter`。后者要求上传工具、在 guest 中执行导出器、取回 `bootstrap.json`；其中 `export_pfns` 指向发布缓存清单的通信页，**不是**待查询的缓存页（`scripts/live_task_cache.py:137–179`）。

即使提前装好导出器，每轮仍从 `guest_before` 开始：host 写入新的请求序号，guest 读自己的 `/proc/asb_pagecache`，发布该序号的完整记录。host 解码后从有效记录取 guest PFN（物理页号），再换算为宿主映射和文件偏移；`guest_after` 的第二次新发布用于核对页身份。已经暂停的 guest 无法处理新请求，旧清单也不满足请求序号。源码中的 5 秒是等待上限，**本次没有发起发布或实测超时**（`live_task_cache.py:235–252,299–320,327–365`；`guest_cache_exporter.py:69–95`，均在 scripts 下）。七分支入口使用继承同一逻辑的 Reader，同样要启动导出器（`seven_restored_pages.py:46–56,123–126,299–310`）。

## 四个来源读什么、哪些部分可脱离 guest

| 来源 | 原采集器如何绑定、读取（基线 scripts/ 下） | 不运行 guest 时可以得到什么 |
|---|---|---|
| VMM 页表 | KVM 注册记录绑定 RAM 槽（`task_physical_cache.py:122–150`），`proof['slots']` 的 host 和 size 决定从 `/proc/<VMM PID>/pagemap` 读取的范围。按 `vmm_first/middle/last` 三读，再取候选 PFN 对应的表项（`live_task_cache.py:254–272,307–309,323,333,343–346`）。 | 页表本身是宿主接口；有已验证的槽位即可读原始表项。判断哪几项属于当前 guest 文件缓存需要发布清单。`smaps_rollup:Pss` 是另一路轻量进程指标（`host_sandbox_monitor.py:90`），不是这里的页表。 |
| RAM 直接后备 | `SnapshotCache(proc, proof['slots'], ...)` 从 VMM 的 maps 中取与 RAM 槽相交的唯一 /dev/ublkb* 映射，验证设备、inode、私有映射，再把 guest PFN 换成设备页偏移。连续偏移按段 mincore，内核探针记录 PFN，再按偏移查 BPF 表。字段 `backend_query_first/last`、`requested_pages`、`confirmed_pfn_pages`（`live_task_cache.py:190–205`；`snapshot_cache_residency.py:227–246,279–323,352–399`）。 | 无需 guest 即可识别映射设备；已知有效偏移时宿主查询可执行。仅凭设备名无法选出原算法要查的偏移。 |
| 快照内存下层 | 恢复设备关联到 `snapshot-local-cache/runtime/*/memory/image.json`；核对唯一不可变的 `lowers[0].file` 和本批服务打开的 inode。用内存层区段表将直接后备页偏移换成下层文件偏移，再对普通文件按同样的 mincore＋PFN 路径查两遍（`snapshot_layer_metadata.py:64–134`；`live_task_cache.py:205–207,318–320,329–332`）。 | 文件及区段元数据可在宿主识别；原算法要查的子集依赖上一步的候选偏移。扫描整文件会改变分母。 |
| provider（设备级） | 从配套镜像配置、daemon 身份、`provider-slots/*.json` 核对 dev_id、queue、slot 及元数据文件。每帧读槽位元数据、daemon 的 pagemap、再读元数据；用两帧与三类行表以及前后 guest 清单核对归属和去重（`provider_slot_cache.py:30–113,127–171,318–345`）。 | 主机可独立读两帧原始槽位与页表；无清单不能把槽位页计入当前 guest 缓存的四来源并集。此阶段没有用 cachestat。 |

**没有“块设备就走 cachestat”的四来源分支。** 角色决定入口：RAM 直接后备按映射绑定块设备，快照下层通过显式 file_path 绑定普通文件，二者均使用 SnapshotCache。另一个端点工具 `host_schedule_cache_layers.py:54–76,291` 才按显式 method 选择块设备 mincore 或 cachestat；其缓存索引计数不提供四来源归属所需的物理页号。

## 可比较的耗时和页次

四个来源各自实际进入页查询的对象数、查询调用数、覆盖页次均为 **0**；四个接口和整轮墙钟均为**未测**，不是 0 ms。没有完整点，无法判断能否在 1 秒内完成。按实际发出的直接后备＋下层查询计，是 0 / 645,238 = 0%；这是入口停止，不是单分支工作量为七路的 0%，也不是改变了对象范围。负载规模没有测得。

此前的 208.030 ms 是另一次单分支实测，允许在暂停前及恢复运行后各导出一次，然后计宿主读窗；不适用于本次禁止导出的条件。没有改用历史清单、全设备扫描、PSS 或缓存容量补值。

下一步建议（未执行）：若目标仍是计量原四来源，只能在允许导出的条件下获得该清单；已有跨暂停窗口的单分支耗时可直接引用，无需为本次结论重复运行。
