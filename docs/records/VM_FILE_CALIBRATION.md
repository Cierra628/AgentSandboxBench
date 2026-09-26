# 真实 VM 已知文件页校准（2026-09-24）

在既有官方 AgentENV 镜像、2 vCPU/4096 MiB VM 中完成 1 MiB smoke，再完成 16 MiB 校准。两档均通过共享映射、半数页 CoW、独立文件三种状态检查，VM 删除后逐页离线复算通过。未运行 DAX 或跨 VM 共享实验。

## 方法与判据

一个 guest 进程创建两个 `MAP_PRIVATE` 文件映射。共享状态映射同一文件，只读触页；CoW 状态修改第二个映射的每个偶数页；独立状态映射两个内容相同的独立文件。文件内容使用确定的非零逐页数据，同时核对映射 SHA-256 和原文件未被修改。这里的“共享”是同一 VM 内的文件页共享，“CoW”是 guest 进程文件映射的写时复制，不是 VM checkpoint fork。

每个状态保持映射存活，复用学长 `physical-cache-probe.py` 导出全 guest 的文件 LRU PFN 与 marker。已知映射另外记录逐页 pagemap/kpageflags，host 利用实际捕获的 KVM slots 读取相应 host pagemap。检查以下集合关系：

- 共享：两个映射的 guest/host PFN 集合相同，去重后为 N 页。
- 半数页 CoW：交集 N/2、并集 3N/2；原 N 个文件页在学长枚举结果中，新增 N/2 个匿名页不在文件页集合中。
- 独立文件：两个集合交集为空、并集 2N，即使文件字节内容相同也不会直接视为共享页。

学长 host collector 对每个状态的全 guest 文件页关联也成功；该全局数值包含系统与探针自身文件页，不用它的前后差代替已知负载大小。逐页已知集合单独归档为 `fixture/<case>/known-page-map.tsv`。

## 结果

| 每个映射大小 | 状态 | 已知文件页 | 已知匿名页 | guest / host 去重页数 | 两映射交集 |
| --- | --- | ---: | ---: | ---: | ---: |
| 1 MiB | 共享 | 256 | 0 | 256 / 256 | 256 |
| 1 MiB | 半数 CoW | 256 | 128 | 384 / 384 | 128 |
| 1 MiB | 独立文件 | 512 | 0 | 512 / 512 | 0 |
| 16 MiB | 共享 | 4096 | 0 | 4096 / 4096 | 4096 |
| 16 MiB | 半数 CoW | 4096 | 2048 | 6144 / 6144 | 2048 |
| 16 MiB | 独立文件 | 8192 | 0 | 8192 / 8192 | 0 |

证据根目录 `.artifacts/vm-pfn-smoke/`：

- `20260924T135749Z-1789690`：1 MiB，运行和 `offline-audit.json` 均 PASS。
- `20260924T135801Z-1789906`：16 MiB，运行和 `offline-audit.json` 均 PASS。
- `20260924T135553Z-1788461`：首次失败在 guest 工作目录缺失；改为创建父目录。
- `20260924T135620Z-1788767`：子探针提前退出，原就绪日志没有包含具体原因。
- `20260924T135710Z-1789296`：补全诊断后确认 `/tmp/asb-physical-cache-probe.py` 不存在。改为将校准脚本与学长探针一同上传到 `/workspace/asb-tools` 后通过；没有验证旧路径消失的底层原因，不归因于 AgentENV 上传缺陷。

失败不覆盖，均单独保留运行结果。所有运行清理成功，最终项目 sandbox 列表为空。没有安装依赖、重启服务、全局清缓存或停止其他工作负载。临时 bpftrace 仍限定本项目服务 cgroup，并在 slot 捕获后退出。

## 复现与结论边界

```bash
bash scripts/29-calibrate-vm-files.sh 1
bash scripts/29-calibrate-vm-files.sh 16
python3 scripts/audit_vm_pfn.py .artifacts/vm-pfn-smoke/20260924T135801Z-1789906
```

依赖本机已有服务、私密 CLI 配置、固定镜像和 root pagemap/BPF 权限。脚本将生效配置、源码快照、页表、分类、日志及退出码归档在独立 run_id；这些原始地址与日志仅保留本地，不上传 Git。

现在有证据支持：已知普通文件页可被当前 guest 枚举器覆盖，guest CoW 匿名页不会误计为文件缓存，真实 guest→host 映射保留了本次负载的页集合关系。不能推广为所有 guest 文件页均无漏计，也不覆盖大页、非驻留页、页迁移、DAX 或跨 VM checkpoint 共享。扫描非原子且探针改变内存状态，本轮不作性能比较；每档一次确定性校验不估计方差。

后续已完成 AgentENV checkpoint 双实例的已知页关系及写入隔离，见 [双实例校准报告](SNAPSHOT_SIBLING_CALIBRATION.md)。DAX 后端仍需独立生命周期与驻留校准；轻量监控 5% 验收保持未判定。
