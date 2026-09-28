# AgentSandboxBench

单机 Agent 沙箱实验平台：对冻结的真实工具调用轨迹进行可复现重放，在指定步骤执行 checkpoint/restore，并记录任务正确性、内存和阶段时延。AgentENV 是主要 baseline，固定 TrEnv-X / Incremental DAX 实现作为对照。

目前 AgentENV 和 TrEnv-X 都已有 Exp1 单轨迹重放与第 013 步恢复续跑证据；TrEnv-X 还通过了第 022 步已修改源码的文件恢复，以及复制抛错和子进程超时的受控恢复。**其他服务异常路径及跨后端性能对照尚未验收。**

- [当前进度与验收缺口](docs/STATUS.md)：项目状态的统一入口。
- [TrEnv-X Exp1 验收](docs/records/TRENVX_EXP1.md)：26 步完整重放、第 013、022 步文件恢复续跑及正确性证据。
- [TrEnv-X 服务恢复验证](docs/records/TRENVX_SERVICE_SMOKE.md)：完整服务 smoke、复制抛错/子进程超时恢复、大文件复制缺陷及修复证据。
- [Exp1 指标与归档](docs/records/EXP1_METRICS_AUDIT.md)：19 次运行的统一缺口审计、五对采样实验、进程内存与阶段耗时图。
- [文件内容物理页](docs/records/FILE_PHYSICAL_MEMORY.md)：独立采集/复算入口、已知负载判据与本轮首个权限失败；真实 Exp1 文件物理量仍未测。
- [本轮平台进展](docs/records/PLATFORM_PROGRESS_20260926.md)：原有能力、本轮改动、实际验证和集成要求。
- [协作分工与接口](docs/COLLABORATION.md)：后端、控制器、任务和采集分析的交付边界。

## 平台目前完成了什么

| 部分 | 已有能力与本轮交付 | 尚未完成的验收 |
| --- | --- | --- |
| 沙箱运行与轨迹重放 | 两后端 Exp1 的 26 步冻结重放、预期失败和独立 SVG 检查通过；manifest、初始 HEAD 和最终 patch 一致。 | 更多任务及官方完整任务评测；新 trajectory 的自动生成。 |
| checkpoint/restore 管理 | 两后端第 013 步保存并重建续跑通过；TrEnv-X 还在第 022 步保存并继承已修改源码，复制抛错/子进程超时后父 guest 可继续。 | 其他服务异常路径及更多文件状态。TrEnv-X 不恢复任意父进程。 |
| 指标采集与分析 | Exp1 19 次运行审计通过；五对采集完成，差异存在顺序相关波动；两次诊断已核对虚拟机进程归属，记录 PSS 和创建/恢复时间并生成图表，历史物理页/DAX 校准和计时审计已有记录。 | 补齐 guest、文件物理量和完整阶段计时，统一两后端的 PSS 采集，完成任务级公平对照；暂定 5% 监控开销目标仍未判定。 |

历史 AgentENV、DAX 和 upper-layer fixture 结果见[阶段交接记录](docs/records/PLATFORM_HANDOFF.md)及[收尾记录](docs/records/CLOSEOUT_STATUS.md)。它们与本轮普通用户隔离验证分别记录，不能用 fixture 的成功证明完整 TrEnv-X controller 已通过。

## 当前可运行的短验证

从已准备好环境的仓库根目录执行：

```bash
bash scripts/41-verify-trenvx-server.sh
```

2026-09-26，隔离验证复跑 `20260926T091827Z-1959097` 为 PASS：固定补丁、两个 Python SDK/CLI、8 个 Go 契约（含 race）、4 个 Python 契约、实际 envd 包检查和编译，以及无网络 QEMU guest 中的真实 cgroup/启动路径测试全部通过。父 shell 退出后的 setsid 后代归属、冻结停止写入、解冻继续及定向清理已覆盖。

`status=PASS` 表示这套短检查全部通过；`stage=isolated-guest` 表示最后完成的阶段；`result_dir` 是本次日志位置。该入口不会部署完整服务或重放真实任务，也没有验证完整文件 checkpoint/restore。详细阶段解释见[本轮平台进展](docs/records/PLATFORM_PROGRESS_20260926.md)。

完整服务入口为 `bash scripts/43-build-trenvx-service.sh` 和 `bash scripts/44-smoke-trenvx-service.sh`，复用本机已有模板，要求 root 及文档列出的依赖。真实重放入口为 `bash scripts/45-replay-trenvx-exp1.sh`；完整运行及第 013、022 步恢复模式均已通过，命令与范围见 [Exp1 验收记录](docs/records/TRENVX_EXP1.md)。

TrEnv-X 运行结束后默认用本机 `pigz` 将本次私有镜像 `data/` 压缩、校验后移除，保留归档与轨迹、样本和日志；可用 `--archive-compressor gzip` 选择旧方式，或用 `--keep-data` 保留解包镜像。入口设有空间门槛、写入互斥和每 0.25 秒的空闲空间检查；达到 20 GiB 停止本次受保护进程组。这是轮询保护，不能替代共享文件系统的硬配额。归档仅在本机，不等于异机备份。当前磁盘余量与保留边界见[指标记录](docs/records/EXP1_METRICS_AUDIT.md)。批量前可运行 `python3 scripts/plan_trenvx_storage.py --pairs 5` 只读估算所需空间；空间不足返回退出码 2，配对入口也会在每次运行前检查剩余整批预算。

## 固定源码与复现边界

本仓库管理脚本、配置、后端实现、可复现补丁和文档。AgentENV 与 IncrementalDAX_moti 的独立源码目录、运行时依赖/凭据及 `.artifacts/` 原始数据被忽略，不随 Git 上传。版本固定于 [`configs/source-revisions.json`](configs/source-revisions.json)，上游改动保存在 `patches/`。

```bash
# 获取固定源码并应用平台基础补丁。
bash scripts/14-fetch-research-sources.sh
# 应用 TrEnv-X 后端及 controller 补丁。
python3 scripts/40-apply-trenvx-task-cgroup.py
# 准备两个独立 Python 客户端环境。
bash scripts/42-prepare-python-clients.sh all
```

运行 `41` 还需要兼容 guest kernel、静态 BusyBox、Go/gcc/QEMU 和固定 aenv CLI；复现前提和客户端准备命令见[平台进展记录](docs/records/PLATFORM_PROGRESS_20260926.md)。这些入口尚未完成全新机器的完整服务复现验收。

既有 AgentENV 入口为 `bash scripts/13-run-exp1.sh`、`bash scripts/16-run-exp1-checkpoint.sh` 和 `bash scripts/37-closeout-exp1.sh`。它们依赖已部署服务、私有凭据及配置中的服务结果目录，运行前需对齐当前实例；不能直接沿用历史 `.artifacts/` 路径。后端接口与恢复边界见[TrEnv-X 后端记录](docs/records/TRENVX_TASK_CGROUP_BACKEND.md)。

本仓库 GitHub 地址为 <https://github.com/Cierra628/AgentSandboxBench>。
