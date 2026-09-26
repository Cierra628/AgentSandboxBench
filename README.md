# AgentSandboxBench

单机 Agent 沙箱实验平台：对冻结的真实工具调用轨迹进行可复现重放，在指定步骤执行 checkpoint/restore，并记录任务正确性、内存和阶段时延。AgentENV 是主要 baseline，固定 TrEnv-X / Incremental DAX 实现作为对照。

目前已有 AgentENV 历史单机实验闭环，以及 TrEnv-X 后端补丁和普通用户隔离验证入口。**TrEnv-X 完整服务、真实轨迹下的文件恢复及跨后端性能对照尚未验收。**

- [当前进度与验收缺口](docs/STATUS.md)：项目状态的统一入口。
- [本轮平台进展](docs/records/PLATFORM_PROGRESS_20260926.md)：原有能力、本轮改动、实际验证和集成要求。
- [协作分工与接口](docs/COLLABORATION.md)：后端、控制器、任务和采集分析的交付边界。

## 平台目前完成了什么

| 部分 | 已有能力与本轮交付 | 尚未完成的验收 |
| --- | --- | --- |
| 沙箱运行与轨迹重放 | 历史 AgentENV Exp1 的 26 步冻结重放、预期失败和针对性功能检查通过。本轮提供固定源码与 SDK/CLI 准备入口；TrEnv-X 的 simple HTTP、process RPC、PTY 实际启动代码通过隔离 guest 测试。 | TrEnv-X 完整服务与真实轨迹；更多任务及官方完整任务评测。本轮未验收新 trajectory 的自动生成。 |
| checkpoint/restore 管理 | 历史 AgentENV 在第 013 步保存并重建续跑通过。本轮为 TrEnv-X 工具及后台后代增加任务 cgroup，提供归属、冻结确认、解冻和定向清理；controller 捕获/失败恢复补丁已交付。 | 将新版 envd 装入独立模板，验收完整 controller 的捕获、封存、新 guest 恢复和异常路径。TrEnv-X 当前恢复的是文件状态。 |
| 指标采集与分析 | 既有脚本能保存 guest/host 内存样本、事件、正确性及阶段时延；历史物理页/DAX 校准和计时审计已有记录。 | 按统一口径在真实 TrEnv-X 任务中采集，完成任务级公平对照；暂定 5% 监控开销目标仍未判定。 |

历史 AgentENV、DAX 和 upper-layer fixture 结果见[阶段交接记录](docs/records/PLATFORM_HANDOFF.md)及[收尾记录](docs/records/CLOSEOUT_STATUS.md)。它们与本轮普通用户隔离验证分别记录，不能用 fixture 的成功证明完整 TrEnv-X controller 已通过。

## 当前可运行的短验证

从已准备好环境的仓库根目录执行：

```bash
bash scripts/41-verify-trenvx-server.sh
```

2026-09-26，隔离验证复跑 `20260926T091827Z-1959097` 为 PASS：固定补丁、两个 Python SDK/CLI、8 个 Go 契约（含 race）、4 个 Python 契约、实际 envd 包检查和编译，以及无网络 QEMU guest 中的真实 cgroup/启动路径测试全部通过。父 shell 退出后的 setsid 后代归属、冻结停止写入、解冻继续及定向清理已覆盖。

`status=PASS` 表示这套短检查全部通过；`stage=isolated-guest` 表示最后完成的阶段；`result_dir` 是本次日志位置。该入口不会部署完整服务或重放真实任务，也没有验证完整文件 checkpoint/restore。详细阶段解释见[本轮平台进展](docs/records/PLATFORM_PROGRESS_20260926.md)。

完整服务的下一步是配置实验所需 Docker、KVM/ublk 和受控镜像挂载权限，部署独立 TrEnv-X 模板，再验证“执行命令 → checkpoint → 新 guest 恢复文件 → 继续执行”。本次没有修改共享服务或全局清缓存。

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
