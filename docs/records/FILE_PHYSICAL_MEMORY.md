# TrEnv-X 文件内容物理页：采集实现与权限边界（2026-09-28）

**本轮实现了独立采集、复算与校准入口，但没有取得新的 VM/DAX 或真实 Exp1 文件物理量。** 当前普通账号的驻留页面 PFN 被内核隐藏，KVM 打开权限和免密 sudo 也不可用；首次失败后停止实测扩展，不将缺失值写成零或 PASS。新增 DAX 校准接入尚未在 VM 中执行。

基线为 GitHub 最新 main `3232be5`，分支 `feature/trenvx-file-physical-memory`，工作目录 `/home/rashen/work/AgentSandboxBench`。没有使用旧工作树的未提交文件，也没有复制其运行时目录。新获取的 IncrementalDAX 源码固定为 `a9bc7deadb68a6e24b7242eab343dfedf953ea38`；其 gitlink 与配置一致，但两个子模块尚未初始化。宿主机 CH HEAD 核验为 `9e0056eb750096f3bf8ed491c345ef9241fd527e`；没有升级源码或构建新的 CH/guest kernel。旧独立工作树不属于当前 chat，归档工具拒绝操作，仍保留原位。

## 四种口径与页面归属

| 口径 | 读取方法与范围 | 本轮实际状态 |
| --- | --- | --- |
| 虚拟机内部内存 | guest `/proc/meminfo`、页大小、`/proc/iomem`；不等于 host 物理量 | 没有启动 VM，NOT_MEASURED |
| 宿主机资源组 | 指定 cgroup 的 `memory.current/stat`；包含该组所有成员 | 小负载读到的是已有 session 组，含其他进程；不能作为沙箱占用 |
| VM 进程 PSS | 核对 PID/start ticks/exe/socket/cgroup 后读取 `smaps_rollup` | 无 VMM，NOT_MEASURED；host 探针进程 PSS 单独标为 host_fixture |
| 文件内容物理页 | 文件 inode/device 和数据偏移 → VMM 映射 → resident host PFN；所有 VM 合并去重 | 首个驻留页 PFN 隐藏，physical bytes 为 null，FAIL |

这四种量分别保存，**不相加**。ext4 文件大小、磁盘分配块、DAX 映射容量和映射 PSS 均不代替文件内容的实际驻留物理量。文件尾部覆盖一页时计完整物理页。

独立脚本 `scripts/measure_file_physical.py` 接受 run-owned manifest。它检查进程身份和 cgroup 成员，按 backing 文件的 inode/device 匹配映射；不可变 ext4 镜像可用 `guest_path` 自动解析已初始化的叶子 data extent，排除镜像容量和文件系统元数据。不支持的稀疏、unwritten、非 extent 文件和多重映射歧义明确失败。普通已知文件可以直接提供数据页偏移。

普通 guest 文件页可复用固定版本 `physical-cache-probe.py`。其 PFN 必须通过实际成功的 KVM ioctl 记录、当前 start ticks 和 guest marker 核对；RAM slot 由 guest iomem 分类，不按固定 GPA 截断。旧探针仅扫描至 5 GiB，实际 RAM 超出覆盖范围立即失败。文件 LRU 没有 inode 归属且可能包括元数据，作为 `file_lru_unattributed` 单列，不能全部称为纯文件内容；存在未归因驻留页时返回 PARTIAL。未扫描页面的数量未知，失败记录中的零计数仅表示已观察集合为空。

非驻留和 swapped 页不计入驻留物理量；present 页 PFN 为零表示权限缺失，计数为 null。`MAP_PRIVATE` 写入产生的匿名 CoW 页单列。每个 host PFN 在跨 VM/跨文件的文件内容集合中只计一次。读取后复查页表、映射、进程及 backing 文件，检测到迁移或状态变化时失败；扫描仍非原子，不保证整个区间中没有变化。

## 已知负载、测试和实际失败

| 证据层次 | 已核对内容 | 结论 |
| --- | --- | --- |
| 历史已知文件校准 | [VM 文件页](VM_FILE_CALIBRATION.md)：1/16 MiB 共享、私有及半数 CoW | 来自 main 中既有记录；本轮没有复跑成功或读取历史 raw |
| 历史 DAX 校准 | [DAX 路径](TRENVX_DAX_PRIMITIVES.md)：两 VM 已知 lower 内容页共享；OverlayFS 整文件 copy-up | 不能将 upper 的磁盘分配量当作内存；不是 Exp1 内存收益 |
| 本轮逻辑验证 | 14 项新测试、9 项既有内存/磁盘测试、1 项串口测试 | PASS；使用合成 PFN，另以真实小型 ext4 镜像验证 data extent 解析；不证明真实共享 |
| 既有 host 校准复用 | `27-calibrate-host-pfn.sh 1`，运行 `20260928T094407Z-2272688` | 首个 host PFN 隐藏，FAIL；子进程清理 PASS |
| 本轮小负载 | 新入口复用 `calibrate_host_pfn.worker`，共享、独立文件、半数 CoW、写入后文件复制四种判据 | 共享状态首个页表项失败，后续三种驻留计数 NOT_RUN；没有用预期页数替代实测 |
| 本轮 DAX/真实 Exp1 | 新 DAX 入口接入了独立计数器、guest 内存与布局读取；Exp1 runner 尚未接入本采集器 | VM 验证 NOT_RUN；本轮没有任务级内存收益实测 |

最新本机 run_id、源码哈希、失败位置和扫描耗时见[脱敏证据](evidence/trenvx-file-physical-20260928.json)。host 页大小为 4096 B，kernel 为 6.19.9+；只读预检约 17.82 ms，检查时余量约 65.95 GiB。小负载的失败扫描耗时不是完成扫描的成本，也不是监控开销。首个失败为 `host-worker-0:pagemap:0`，驻留标志可见但 PFN 不可见。probe/source 快照、原始页表、进程/cgroup/文件归属及日志只留在 `.artifacts/`；Git 仅提交摘要。

## 复算与下一次运行

只读检查与小负载（普通账号缺权限会返回 BLOCKED/FAIL）：

```bash
python3 scripts/check_file_physical_environment.py --output .artifacts/file-physical-preflight/NEW_ID
python3 scripts/calibrate_file_physical.py --mib 1
python3 scripts/measure_file_physical.py --audit-run .artifacts/file-physical-calibration/RUN_ID/shared
python3 -m unittest tests/test_file_physical.py tests/test_trenvx_diagnostic_metrics.py tests/test_experiment_disk_guard.py
python3 scripts/test_trenvx_serial_marker.py
```

离线 `audit_status=PASS` 仅表示原始观测与计数/manifest 哈希一致；原 `measurement_status=FAIL` 保留。修改计数或把 FAIL 改成 PASS 会被拒绝。

权限和依赖准备完成后，先用**有权限的实验账号**运行 `python3 scripts/calibrate_file_physical.py --mib 1`，再运行既有 `bash scripts/31-smoke-trenvx-dax.sh 1`；1 MiB 成功后才考虑 16 MiB。DAX 入口记录 before/after 的 `*-file-physical.json`、guest meminfo/iomem、PSS、文件归属和原始页表。它仍只测已知 lower 内容；upper cache 的物理量明确 NOT_MEASURED。入口复用项目写入锁与 0.25 s 磁盘轮询，22 GiB 启动门槛和 20 GiB 停止线；只终止本次进程组。低空间中止后 VM/临时 socket 的完整清理尚未实测，不能把既有注入读数测试当作真实 VM 回收验收。

通用 manifest 的 DAX 文件项示例（这些是结构示意，不能直接作为真实观测）：

```json
{
  "page_size": 4096,
  "owned_root": "/ABSOLUTE/RUN_DIRECTORY",
  "vms": [{
    "id": "OWNED_SANDBOX", "pid": 123, "start_time_ticks": 456,
    "executable": "/ABSOLUTE/cloud-hypervisor",
    "cgroup": "/OWNED_GROUP/OWNED_SANDBOX",
    "api_socket": "/tmp/vmm-OWNED_SANDBOX.socket",
    "files": [{"owner": "/delta/known.bin",
      "backing": "/ABSOLUTE/RUN_DIRECTORY/checkpoint.ext4",
      "guest_path": "/delta/known.bin", "immutable_image": true}]
  }]
}
```

文件页从 guest cache 输入时，还需 `ram_slots`、`ram_slot_ids`、六列 `slot_capture`、`slot_capture_start_ticks`、`layout_source=successful-kvm-slot-registration`，以及 `file_lru_probe` 的 `marker/pfns/page_size/byteorder/scan_limit_bytes`。这些输入来自本次探针和捕获，不能手工猜地址或复用历史 PID。通用读取命令为 `python3 scripts/measure_file_physical.py MANIFEST.json --output NEW_RESULT_DIR`。

本次没有启动真实 Exp1，因此输入哈希、动作顺序、008/016 预期失败、最终 patch、独立 SVG 检查均为 NOT_RUN，VM/服务/cgroup/loop 清理为 NOT_APPLICABLE。host 小负载的两个自有进程已退出，文件和证据留在本次目录；未清缓存、共享服务或他人数据。

下一阶段应先完成新计数器的真实已知负载验收，再准备固定 fork/SDK/二进制，选择第 022 步之后的单次 Exp1 诊断读取。必须保留冻结 manifest、初始 HEAD、完整动作序列、预期失败、最终 patch、独立任务检查及本次资源清理证据；沿用 36/48 GiB 启动门槛和 20 GiB 停止线。不将探针额外读入的页面当作原任务驻留，也不把一次诊断计入性能对照。未完成之前，任务中的文件物理量及跨后端内存收益保持未判定。
