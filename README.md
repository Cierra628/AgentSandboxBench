# AgentSandboxBench

用于 Agent 沙箱的轨迹重放、checkpoint/restore 和内存与时延测量，主要以 AgentENV 为 baseline，并保留 TrEnv-X 对照。项目进度与未完成项见 [平台交接记录](PLATFORM_HANDOFF.md)。

## 当前验证状态（2026-09-24）

- **AgentENV 官方示例已跑通。** 在 cpu-15 上使用固定的官方预构建服务镜像和 `e2b 2.26.0`，完成 Kimi K3 报告所链接的 AgentENV 官方 Python SDK 示例：Ubuntu 22.04 模板构建、沙箱创建与列举、命令执行、暂停和删除。逐项结果、镜像版本与证据见 [官方示例报告](KIMI_AGENTENV_EXAMPLE.md)。
- **底层功能已验证。** Alpine 模板、文件读写、单次暂停/恢复及来宾进程连续性通过，证据见 [cpu-15 验证报告](CPU15_RESULTS.md)。这不等于本地源码构建或 K3 模型实验通过。
- **Exp1 完整重放及第 13 步恢复均已通过一轮。** 固定 26 个工具动作按序执行，保留原轨迹中 008/016 的预期失败；恢复后的最终 patch 与完整重放一致，来宾进程在恢复前后连续运行。原始结果分别保存在本地 `.artifacts/platform-exp1-agentenv-default/` 和 `.artifacts/platform-exp1-checkpoint/`，见 [平台交接记录](PLATFORM_HANDOFF.md)。监控开销、远端 checkpoint 持久化及独立任务测试尚未验证。

## 源码与复现

官方 AgentENV 源码在 `AgentENV/`，固定提交为 `2f48c9c78ec58e4d73c49aa07cb81d984a4a4184`。学长的实验源码在 `IncrementalDAX_moti/`，具体版本见 `configs/source-revisions.json`；两个目录均是独立仓库，不随本仓库上传。在新机器获取固定源码并应用平台补丁：

```bash
bash scripts/14-fetch-research-sources.sh
```

已有兼容服务、私密凭据和虚拟环境时，可复现官方 Python 示例：

```bash
bash scripts/10-run-official-python-example.sh .artifacts/server-smoke-20260922T114006Z-1387374
```

该服务目录与凭据只存在于实验机器；详细前提和依赖版本见 [官方示例报告](KIMI_AGENTENV_EXAMPLE.md)。历史排障过程已移至 [早期探索记录](CPU14_EARLY_HISTORY.md)，不作为当前操作步骤。

## Git 同步

本目录是独立 Git 仓库，`main` 分支连接到 [GitHub AgentSandboxBench](https://github.com/Cierra628/AgentSandboxBench)。Git 只同步文档、脚本、配置和补丁；`.gitignore` 排除嵌套源码、凭据、运行时镜像及原始实验数据。
