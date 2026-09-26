# TrEnv-X 底层 DAX 路径校准（2026-09-25）

复用学长指定 Cloud Hypervisor 与 guest 内核完成两台无网络 VM 的已知负载验证。1 MiB 和 16 MiB 两档均确认只读 DAX 文件层的 host 物理页完全共享，并且 A 的文件写入不会改变 B 或只读层。另确认：本配置下修改一个字节会将完整文件复制到 A 的 OverlayFS upper，16 MiB 文件的 upper 实际分配量也是 16 MiB。

这是 Cloud Hypervisor、virtio-pmem、ext4 DAX 和 OverlayFS 的底层校准；没有启动 TrEnv-X orchestrator，没有运行 `checkpoint_dax.py` 的封存流程，也不代表完整 TrEnv-X 生命周期已验收。

## 部署盘点与范围

- 本地 TrEnv-X checkout 保持指定 `53041d8c924514fca27e363cce7f5af8079267ce`，无源码修改。
- `/var/lib/trenvx/deps/cloud-hypervisor` 的 checkout 为指定 `9e0056eb750096f3bf8ed491c345ef9241fd527e`，工作树干净；既有二进制报告 v45.0.0。本次归档二进制哈希，不以 checkout 版本代替可复现构建证明。
- 复用 `/var/lib/trenvx/kernels/ch-6.1.134/vmlinux`，guest 实际启动输出确认 6.1.134；host 为 6.19.9+。
- 已存在学长的 `/var/lib/trenvx`、Btrfs `/var/lib/trenvx-exp5-cow` 与多个历史模板，属于已有资产，本轮只读检查。Exp1 历史模板未找到 `provenance.env`。
- 当前 PATH 中 Go 为 1.22.2，未找到 rustup；本项目固定 checkout 未构建 orchestrator/template-manager。历史 setup 要求 Go 1.23，且包含旧路径、网络、cgroup、Btrfs 卷及模板操作，未直接执行。

本轮无需新安装工具。没有使用历史 rootfs、upper 或内存 snapshot，避免把缺乏完整构建记录的模板作为验收输入。VMM 与内核只读使用，所有输入和镜像均在本项目新 run_id 中生成；未更改宿主机网络、cgroup、挂载、缓存策略或共享服务。

## 方法

脚本入口 `bash scripts/31-smoke-trenvx-dax.sh 1`，smoke 通过后执行参数 `16`。每个 VM 使用 2 vCPU、4096 MiB；用本机静态 BusyBox 制作最小 initramfs，不运行任务服务。

两台 VM 分别挂接同 inode 的 `rootfs.ext4` 和 `checkpoint-0001.ext4`，每个镜像容量 256 MiB。pmem 使用 `discard_writes=on`，guest 以 `ro,dax=always` 挂载，确认 `/sys/block/pmem1/queue/dax=1`。checkpoint 层中的 `/delta/known.bin` 是确定性非零逐页数据。OverlayFS lower 顺序为 checkpoint 的 `/delta`、基础层；两台 VM 各有独立 virtio-blk ext4 upper。

读取阶段，guest 经合并目录读取文件并输出 SHA-256。host 根据 ext4 中已知文件的实际块偏移、VMM 文件映射、host pagemap 定位物理页，逐页比较两 VM 是否引用同一 host PFN。它不使用写死的 guest RAM PFN 地址范围处理 pmem。

写入阶段，A 使用 `dd conv=notrunc` 修改文件第一个字节，B 只读；再核对两边合并目录和原始 lower 的哈希、host PFN 共享及 lower 镜像哈希。最后两台 VM 正常关机，离线读取私有 upper 验证复制文件的大小、分配量和内容。

## 结果

| 已知文件 | 写入前/后共享的 lower host 页 | 已知内容去重物理量 | 两 VM DAX 映射 PSS 合计 | A upper 文件大小 / 分配量 |
| --- | ---: | ---: | ---: | ---: |
| 1 MiB | 256 / 256 | 1 MiB | 1472 KiB | 1 MiB / 1 MiB |
| 16 MiB | 4096 / 4096 | 16 MiB | 16832 KiB | 16 MiB / 16 MiB |

两档均通过 A 改写哈希、B 原哈希、只读层原哈希检查；B upper 中没有 `known.bin`。16 MiB 运行中，A VMM PSS 从 219911 KiB 增至 238403 KiB，B 保持 219971 KiB。这个总 PSS 差包含其他 VM 状态变化，不能全部归因为文件内容；整文件复制的直接证据是 upper 文件大小、完整分配块和导出文件哈希。

DAX 映射 PSS 覆盖两个镜像，包含已知文件外的驻留页；不能把 1472 KiB 当成 1 MiB 文件的纯内容物理量。每个 VM 映射的镜像总容量为 512 MiB，也不能把容量计为驻留量。

证据在 `.artifacts/trenvx-dax-primitives/`：

- `20260925T154353Z-1824317`：首次失败。VM 已启动并完成 DAX 挂载，但串口接收器只等到 `ASB_READY` 前缀就校验完整行；A 的串口分段到达导致误判。结果保留 FAIL，两个 VMM 已退出。
- `20260925T154429Z-1824608`：修正为等待完整行后的 1 MiB smoke PASS。
- `20260925T154551Z-1825196`：16 MiB PASS。

成功运行的 `offline-audit.json` 均 PASS，包含 upper 检查。原始文件包括逐页表、maps/smaps、串口日志、命令与退出码、二进制/内核/脚本哈希、生效参数、镜像、导出的 upper 文件。所有原始地址、日志和镜像保留本地，Git 仅同步脚本与摘要。

复算及不接触服务的回归检查：

```bash
python3 scripts/audit_trenvx_dax.py .artifacts/trenvx-dax-primitives/20260925T154551Z-1825196
python3 scripts/test_trenvx_serial_marker.py
```

## 重要结论和下一阶段

1. 固定 VMM/guest 内核的只读 DAX 下层路径可用，本次两个已知文件规模中完整共享了内容页。
2. A 的改写保留了只读层共享，但产生完整私有 upper 文件。这与此前 AgentENV `MAP_PRIVATE` 修改半数页的测试不是相同的写入语义，不能直接比较两者的内存增长或声称 DAX 的任务级收益。
3. 页表采集是非原子扫描，每档只有一次成功校准；未估计方差。当前 fixture 使用新建 ext4 层、普通独立 upper，不覆盖 Btrfs 模板布局或真实封存过程。

下一阶段先对齐文件写入负载，再验证 `archive_upper/build_layer` 对内容、whiteout、目录属性与 xattr 的保留；随后准备本项目独立 TrEnv-X 服务和固定版本构建。完整服务启动前须检查其网络规则、`/etc/hosts`、cgroup、数据目录与共享实验是否冲突。任务级对照和监控 5% 验收仍未完成。
