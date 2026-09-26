# AgentSandboxBench

单机 Agent 沙箱实验平台：重放冻结的真实工具调用，在指定步骤 checkpoint/restore，并分别记录任务正确性、内存和阶段时延。AgentENV 是主要 baseline，学长的 TrEnv-X / 增量 DAX 实现是对照。

- [当前进度与验收缺口](docs/STATUS.md)：以后续实验应从这里开始。
- [协作分工与接口](docs/COLLABORATION.md)：适合两人或多人并行开发。
- [rashen 用户环境与一键隔离验证](docs/records/RASHEN_SERVER_SETUP.md)：当前服务器的准备结果、使用命令和权限缺口。
- [详细实验记录](docs/records/PLATFORM_HANDOFF.md)：按阶段保留结果、失败边界与本机证据路径。

## 已验证到哪里

官方 AgentENV Python 示例、Exp1 的 26 步冻结重放及第 013 步后恢复续跑已通过。独立 SVG 功能用例与上游测试子集也通过。已知负载的物理页、DAX 和文件层封存校准通过；任务 cgroup 下的受控在线捕获和新 guest 重建通过。固定 TrEnv-X fork 的任务 cgroup/controller 补丁及隔离启动代码验证已交付，见[后端记录](docs/records/TRENVX_TASK_CGROUP_BACKEND.md)。完整 TrEnv-X 服务、任务级跨后端收益及暂定 5% 监控开销目标尚未验收，见[状态表](docs/STATUS.md)。

## 源码与数据边界

本仓库同步 `scripts/`、`configs/`、`patches/` 和文档。官方 AgentENV 与学长的 IncrementalDAX_moti 是独立 checkout，固定提交见 [`configs/source-revisions.json`](configs/source-revisions.json)，不会随本仓库上传。获取固定版本：

```bash
bash scripts/14-fetch-research-sources.sh
```

该入口尚未在全新机器完成端到端复现验收。实验机的服务凭据、镜像和 `.artifacts/` 原始数据仅保留本地。`AGENTS.md` 是本地协作指令，已被 Git 忽略。脚本运行前先读对应实验记录，避免重复已完成的长测。

## 可复现入口

以下命令从仓库根目录运行，输出写到独立的 `.artifacts/` 目录：

```bash
# 普通用户隔离验收；需要已准备的固定源码、两个 SDK、CLI 和兼容 kernel。
bash scripts/41-verify-trenvx-server.sh

# 需要本机已有兼容 AgentENV 服务及私密凭据。
bash scripts/13-run-exp1.sh
bash scripts/16-run-exp1-checkpoint.sh

# 局部诊断：独立功能测试、计时和任务 cgroup。
bash scripts/37-closeout-exp1.sh
bash scripts/38-check-task-cgroup-upper.sh
```

官方示例、环境迁移和早期排障细节分别见[官方示例记录](docs/records/KIMI_AGENTENV_EXAMPLE.md)、[迁移记录](MIGRATION.md)及[历史记录](docs/records/CPU14_EARLY_HISTORY.md)。本仓库 GitHub 地址为 <https://github.com/Cierra628/AgentSandboxBench>。
