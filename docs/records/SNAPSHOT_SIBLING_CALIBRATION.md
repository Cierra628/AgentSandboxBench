# AgentENV snapshot 双实例物理页校准（2026-09-24）

从一个持久 snapshot 启动两个 VM，检查同一已知文件映射的宿主机 PFN，以及只修改 A 分支时 B 分支的状态。1 MiB smoke 和 16 MiB 扩展均通过；这两次运行证明当前固定镜像与服务配置下存在跨 VM 物理页共享，并且本次已知负载的写入被隔离。

## 实验过程与判据

入口为 `bash scripts/30-calibrate-snapshot-siblings.sh 1`，通过后运行 `bash scripts/30-calibrate-snapshot-siblings.sh 16`。固定官方 Exp1 镜像 digest，原 VM 为 2 vCPU、4096 MiB。guest 常驻进程将确定性非零数据写入文件，以 `MAP_PRIVATE` 映射并逐页读取；记录映射和原文件哈希、PID、进程 start ticks、boot ID、逐页 guest PFN/kpageflags。

创建持久 snapshot 后删除原 VM，从同一 snapshot 启动 A、B。两个恢复实例的 PID、start ticks、boot ID 和映射内容必须与 snapshot 前一致。先采集两侧文件映射，再只让 A 修改每个偶数页，B 仅复查原内容。学长 guest 文件页探针分别导出各 VM 的文件 LRU PFN，host 采集器用真实 KVM slots 与 marker 识别两台 VMM；已知映射另逐页保存 host pagemap。B 的文件哈希必须不变，A 的原文件哈希必须不变，A 改写页必须归为匿名页并从文件 LRU 集合排除。

临时 ioctl tracepoint 仅筛选本项目服务 cgroup，捕获成功注册的 KVM slots；探针在映射捕获后退出。原 VM、A、B 和本次 snapshot 均由运行脚本按 ID 清理。没有修改共享服务配置、清全局缓存或停止其他 VM。

## 结果

| 每 VM 映射 | 写入前同位置共享 host PFN | A 改写页 | 写入后同位置共享 host PFN | A/B 前后变化的 host PFN | B 内容 |
| --- | ---: | ---: | ---: | ---: | --- |
| 1 MiB，256 页 | 253 页 | 128 页 | 128 页 | 128 / 0 页 | 原哈希不变 |
| 16 MiB，4096 页 | 4075 页 | 2048 页 | 2047 页 | 2048 / 0 页 | 原哈希不变 |

1 MiB 运行的写入前两 VM 已知页去重后为 259 页，写入后为 384 页；16 MiB 分别为 4117 和 6145 页。共享页小于全部页的原因尚未隔离；这不妨碍确认本次确实存在大量跨 VM 共享。写入后的 A 文件页数分别为 128 和 2048，匿名页数相同；B 的文件页数分别保持 256 和 4096。两次已知映射的逐页 host PFN 以及 guest 页类型检查通过。

证据根目录 `.artifacts/snapshot-sibling-pfn/`：

- `20260924T140355Z-1792529`：首次 1 MiB 运行在写入后采集失败。写入前已记录 252/256 个共享页；因上一阶段 probe 的 marker 文件仍存在，就绪检查误判，结果保持 FAIL，资源清理 PASS。
- `20260924T140429Z-1792980`：修正探针生命周期后，1 MiB 全流程与离线复算 PASS。
- `20260924T140529Z-1793447`：16 MiB 全流程与离线复算 PASS。

每个成功运行保留 `comparison.json`、两个阶段的 `known-pages.tsv`、各 VM guest 状态、文件 PFN 集合、KVM ioctl、采集器结果、命令 stdout/stderr、配置和代码快照。离线复算入口：

```bash
python3 scripts/audit_snapshot_siblings.py .artifacts/snapshot-sibling-pfn/20260924T140429Z-1792980
python3 scripts/audit_snapshot_siblings.py .artifacts/snapshot-sibling-pfn/20260924T140529Z-1793447
```

运行后项目 `aenv list`、`aenv template list`、`aenv snapshot list` 均为空。原始页表和日志仅保存在本地 `.artifacts/`，不随 Git 上传。

## 结论范围与下一步

结果区分了两层：进程身份、文件内容和本次写入隔离是功能检查；同位置 host PFN 交集是物理共享检查。跨 VM 的共享页比例是该固定配置下两个样本的观测，不代表所有 snapshot、冷热缓存状态或任务的稳态比例。前后 pagemap 扫描非原子，host 页可以迁移；两次样本均观察到 B 页 PFN 稳定，但不将其推广为不会迁移。学长全 guest 文件缓存数还包含系统与探针页，不能用总量差直接估算本次已知文件的收益。DAX 为 0 仅表示本次 AgentENV 样本没有符合采集器名称规则的 DAX 层，不是 DAX 后端校准结果。

下一步应在 TrEnv-X 的独立服务和文件层语义下做同样的已知负载校准，明确其仅文件状态继承与 AgentENV 完整 VM snapshot 的差别；随后再设计成对任务级比较。进入性能比较前还需解决调用计时混入日志归档的问题，并固定随机化与统计口径。当前监控 5% 目标仍未判定。
