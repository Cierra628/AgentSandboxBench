# 当前状态

截至 2026-09-26。这里是项目状态的唯一入口；`records/` 保存各阶段的详细证据。原始日志、镜像和样本保存在本机 `.artifacts/`，不随 Git 上传。所有命令从仓库根目录执行。

## 第一版验收

| 目标 | 已验证 | 仍需完成 |
| --- | --- | --- |
| 单条冻结轨迹完整重放 | AgentENV Exp1 的 26 个动作、顺序、输入哈希、预期失败及 patch 检查通过；独立 SVG 属性用例与上游 SVG 测试子集通过 | 官方完整任务评测、更多任务 |
| 指定步骤 checkpoint/restore | 第 013 步后保存，重建实例从 014 继续；patch 一致，guest 进程身份连续 | 其他步骤、打开文件、跨服务重启和远端持久化 |
| 内存曲线与阶段时延 | guest/host 分口径报告、已知负载 PFN 与 DAX 校准；计时 schema 2 将 CLI 与本地归档分开 | 任务级物理内存收益、完整生产调用计时 |
| 监控不改任务语义并量化开销 | 10 对 A/B、10 对 A/A、10 个交错块的 80 次完整重放均通过正确性与清理核对 | A/A 波动与 A/B 同量级，暂定 5% 目标未判定 |
| 结果可追溯、复算 | run_id、版本、配置、哈希、原始样本、失败和清理记录均在本机；离线审计通过 | 全新机器完整复现、学长历史 raw 数据 |

官方 AgentENV Python 示例也已通过，使用固定预构建镜像与 `e2b 2.26.0`。这不等于 Kimi 模型实验或学长 fork 构建通过。[官方示例记录](records/KIMI_AGENTENV_EXAMPLE.md)说明版本边界。

## 当前研究结论

- AgentENV snapshot 双实例的已知文件页共享、写入隔离已通过逐页校准；TrEnv-X 底层 DAX 只读层共享、OverlayFS 整文件 copy-up 和两代封存重建也已验证。两后端写入语义不同，尚无任务级公平内存收益结论。
- 运行中复制 ext4 upper 后直接 `ro,noload` 封存，已复现遗漏 fsync 更新；独立副本恢复日志能读到更新。已知写进程在任务 cgroup 冻结、guest sync、CH 暂停期间复制的受控试验通过，包括新 guest 重建。生产 TrEnv-X controller 尚未接入通用任务写入者管理。
- 同 VM 计时 smoke 与独立功能测试已通过；旧性能数据不能重新解释为 guest 执行、纯 RPC 与落盘的完整拆分。新 schema 2 的 8 次调用离线审计通过，尚未按新口径开展统计验收。

## 下一步顺序

1. 在隔离 TrEnv-X 服务中接入每次工具调用及后台后代的任务 cgroup，验证冻结超时后的解冻和资源归属，再复放真实 trajectory 的 checkpoint。
2. 取得并运行任务专属评测清单，增加另一个动作边界及打开文件状态探针。
3. 固定生产计时口径、A/A 与 A/B 测量协议后再扩展统计轮数；不能因区间跨过 5% 而改分组。
4. 前述正确性与口径通过后，开展 AgentENV/TrEnv-X 同语义任务级对照，再推进既有 GRPO/BPO/TVCache 调度形状。调度形状不代表 RL policy 已训练。

## 证据导航

| 主题 | 详细记录 |
| --- | --- |
| 功能收尾、独立测试、计时及 cgroup | [收尾记录](records/CLOSEOUT_STATUS.md) |
| Exp1、监控 A/B/A/A、时延及历史服务盘点 | [阶段交接记录](records/PLATFORM_HANDOFF.md) |
| 单 VM、snapshot 双实例内存校准 | [VM 文件页](records/VM_FILE_CALIBRATION.md)、[双实例](records/SNAPSHOT_SIBLING_CALIBRATION.md) |
| DAX 与 checkpoint 文件层 | [DAX 底层](records/TRENVX_DAX_PRIMITIVES.md)、[两代封存](records/LAYER_FIDELITY.md)、[在线不一致](records/ONLINE_UPPER_CONSISTENCY.md)、[受控协议](records/QUIESCED_UPPER_PROTOCOL.md) |
| 环境迁移与官方示例 | [迁移](../MIGRATION.md)、[cpu-15](records/CPU15_RESULTS.md)、[官方示例](records/KIMI_AGENTENV_EXAMPLE.md) |

代码版本固定于 [`configs/source-revisions.json`](../configs/source-revisions.json)。本仓库只管理平台脚本、配置、补丁和文档；两个上游 checkout、运行时凭据及 `.artifacts/` 均不随 Git 上传。
