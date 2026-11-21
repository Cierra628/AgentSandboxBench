# 五批共用实验卷：保留或删除

**20261121T034046Z = 北京时间 2026-11-21 11:40:46。以下时间均为北京时间。** 核对分支基线 handoff-20261118（dd9ba4c9ebae59db68055da5e8ad566b87d6eb16）；指定离线检查退出码 0，末行 `status=PASS scope=platform-offline`。

**当前决定：留。** 目标仅为 `/home/yyxie/asb-seven-process/.artifacts/storage-isolated-service/20261121T034046Z-1477264/volume.ext4`（下称卷，同目录下其他文件不在候选范围）。卷没有活动使用者，报告所用的测量与日志已经完整导出；保留理由是卷内还有尚未证实另有副本的原快照内存层。保留既有报告与保留原失败现场是两个不同的要求。

## 它是五次尝试的共同现场，不是其中一次

外层启动日志 `.artifacts/asbvmmproc20261121t033927/launch.log` 的 result_dir 指向本目录，窗口起点 11:40:46.399558；外层 result.json 记结束于 11:48:55.861721。卷外 guest-cache/result.json 在**同一份**快照 seven-paused-20261121T034046Z-1477264 下列出 try-1 至 try-5。一次保存、删除源沙箱；每批恢复七个新沙箱，恢复前新挂 14 个探针，并行导出、暂停、七进程双遍四来源查询、父进程用 NumPy 合并。不是五次成功，也不是后来拟做的修复后三批验证。

查询实现为 8948bff；本次固定快照五批驱动和结果随后记录在其直接子提交 ea6f7eb（提交时间 11:50:54，晚于收尾）。准确说是 8948bff 加上后收入 ea6f7eb 的驱动，不把事后提交当作启动时已存在的 HEAD。

| 尝试 | 结果及现存证据 |
|---|---|
| 1 | 完成：七份就绪 611.450458 ms，合并 6.655239 ms，派发至结果 618.364155 ms；七支各确认 18,542–18,691 页，四来源去重 48,038 页，provider 单列 0 页。 |
| 2、3 | provider 槽位的 dev_id 与当前设备不符，未派发查询。 |
| 4 | 七支创建设备均超时，未派发查询。 |
| 5 | 第 7 支恢复超时；其余分支进入准备后设备号检查失败，未形成批级读数。 |

外层 FAIL、系列 INCOMPLETE 指整组未完成，不否定第 1 批读数。

## 哪些已保存，哪些仍只在现场找到

用 debugfs 只读查看镜像目录，逐个比对卷外文件的相对路径、类型、字节数。不是只看报告是否存在。

| 卷内目录 | 同批目录内的卷外副本 | 非目录条目 | 缺失 / 类型或大小不符 |
|---|---|---:|---:|
| /controller/guest-cache | guest-cache/ | 834 | 0 / 0 |
| /env/logs | service-logs/ | 139 | 0 / 0 |
| /env/provider-slots | service-provider-slots/ | 352 | 0 / 0 |

保存逻辑见 scripts/validate_isolated_agentenv.py:2329–2336。成功批逐支 frames.json、sets.npz、原始读表和失败批 result.json 都在卷外。ea6f7eb 的 docs/records/seven-process-five-20261121/ 含 REPORT.md、rounds.csv、workers.csv、summary.json：正文和表覆盖任务指标，**不是逐帧逐页原始值的全文副本**。独立工作区没有 results/ 目录。4bf984a 的 docs/records/fixed-snapshot-device-20261121/ 引用了这些日志与槽位记录，解释设备号变化和超时。两份报告及其表也已交付到 /mnt/data；未核实远端发布。只删卷不会改变这些数字、文件或已交付结论；不能连同外层目录一起删除。

卷内还有 /env/snapshot-store/repository/managed-layers/ 下的本次 RAM 层（文件名以 sha256_0235b027 开头，301,404,160 字节），与 try-2/shared-device/lower-metadata/ownership.json 所记 layer.file 对应；另有该快照的运行配置 /env/snapshot-local-cache/runtime/01a1fc31-83b2-7270-aada-2a36f7ef5db0/memory/image.json。只读搜索 /home/yyxie 与 /mnt/data 未找到前者的同名外部文件，也没有核实到可还原这个现场的归档。镜像还含现场状态库；本次没有读取其敏感内容。删除会失去目前可定位的原内存层副本及对原卷进一步取证的能力。事后另造一个快照不能代替原件。

## 是否还在使用（约 13:18）

| 对象 | 核对结果 |
|---|---|
| 挂载 | findmnt 未发现原 mount 挂载点；检查可读的 11 个进程挂载命名空间，无该批路径。 |
| loop | losetup -j 对卷无输出；原 /dev/loop5 也未列为活动关联。外层 cleanup.mount_and_loop_removed=true。 |
| 容器 | Docker 全部两个容器（含停止的）均无指向该目录、其子项或覆盖其祖先的挂载；原私有容器 ID 259449d3e7ed… 已不存在。原源容器已停止，使用不同目录。 |
| slice 与服务 | 原 asbvmmproc20261121t033927.slice 为 inactive，对应 cgroup 不存在；同名前缀的 control.service 为 not-found/inactive。 |
| 文件直接占用 | fuser 未报告持有卷文件的进程。外层 cleanup 另明确标记 failed_fixture_retained=true：卷是主动留存，不是漏卸载。 |

## 决策条件

若仍需原现场，先在**另一存储**保存并验证可还原的必要材料，或保留原卷。若负责人明确仅需卷外测量、不再保留原现场，则可释放**单个 volume.ext4**；它实际分配约 64 GiB，此次核查时可用约 62.84 GiB，单项释放足以跨过严格大于 90 GiB 的门槛。届时最后核对：重读卷外原始记录和报告、核对要删除的绝对路径与文件身份（本次 inode 46181732）、重新检查挂载、loop、容器、服务及文件持有者，再单独取得删除授权。本轮未归档、移动或删除任何实验数据，也未启动沙箱。
