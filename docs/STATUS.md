# 当前状态

截至 2026-09-26。这里是项目状态的统一入口；`records/` 保存各阶段的详细证据。原始日志、镜像和样本保存在各次运行的本机 `.artifacts/`，不随 Git 上传。所有命令从仓库根目录执行。

**当前结论：后端代码补丁、客户端准备入口和隔离验证已交付；完整 TrEnv-X 服务与真实轨迹下的文件 checkpoint/restore 尚未验收。** 历史服务实验和本轮隔离验证按下述范围分别记录。

## 平台三部分的进展

| 部分 | 原有平台与历史验证 | 本轮新增交付 | 尚未完成 |
| --- | --- | --- | --- |
| 沙箱运行、轨迹重放 | AgentENV 官方 Python 示例；Exp1 26 步的输入哈希、动作顺序、预期失败、patch/oracle 及针对性 SVG 功能检查通过。 | 固定源码/补丁和 SDK/CLI 准备入口已交付；TrEnv-X 实际 simple HTTP / process RPC / PTY 启动代码通过隔离 guest 测试。 | TrEnv-X 完整服务部署、真实 trajectory；更多任务与官方完整评测。本轮未验收新 trajectory 的自动生成。 |
| checkpoint/restore 管理 | AgentENV 第 013 步后保存、重建并续跑 014–026，patch 一致和 guest 进程身份连续；受控 upper 捕获及新 guest 文件重建 fixture 通过。 | 工具及后台后代的任务 cgroup、冻结确认/解冻/定向清理；固定 fork/controller 的捕获与失败恢复补丁；契约和真实 guest 测试通过。 | 将新版 envd 装入新模板后，验收完整 controller 的 sync、CH pause/copy/resume、upper 封存和新 guest 文件恢复，以及服务异常路径；其他动作边界、打开文件、跨服务重启和远端持久化仍未验收。 |
| 指标采集、分析 | guest/host 内存样本、事件、正确性和时延采集；物理页/DAX 已知负载校准及 timing_schema=2 离线审计已有记录。 | 本轮短验证保存各阶段日志、版本/哈希和 PASS/FAIL，不新增性能样本。 | 真实任务的统一计时/物理内存口径、跨后端公平收益和监控开销统计；暂定 5% 目标未判定。 |

TrEnv-X 的恢复属于文件状态继承：新 guest 使用封存文件层和选定 runtime 文件；它不恢复任意父 shell、后台进程、打开 FD 或完整父 VM 内存。AgentENV 历史进程连续性证据不能移用为 TrEnv-X 的恢复保证。

## 后端实现与实际验收

本轮验证使用 Python 3.12.3、Go 1.22.2、QEMU 8.2.2 和 6.1.134 guest kernel。固定源码/补丁检查、两个 Python SDK 和 aenv 0.2.2 CLI 检查、新版 envd 编译及隔离测试通过。复现者按自己的实验环境准备依赖；新版 envd 尚未部署到经过验收的服务模板。

入口：

```bash
bash scripts/41-verify-trenvx-server.sh
```

| 检查 | 实际结果 | 证据范围 |
| --- | --- | --- |
| 固定版本/补丁与 SDK/CLI | PASS | 源码 HEAD、补丁一致性、SDK 导入/依赖检查、aenv 版本；没有连接完整服务。 |
| Go 契约 | 8 个 PASS，含 race | 模拟 cgroup I/O，检查所有权、启动门禁、异常解冻和清理。host 内核测试 SKIP 不计为 PASS。 |
| Python 契约 | 4 个 PASS | 模拟 guest/CH，检查捕获顺序、异常恢复、异步取消后等待 worker 和 inventory 定向清理。 |
| 固定 envd 包检查及编译 | PASS | 真实 fork 的 process/terminal/taskcgroup 接入可编译；terminal 包没有独立 host 测试。 |
| QEMU 真实内核/启动代码 | 两个 guest 用例 PASS | shell 退出后的 setsid 后代仍归属；冻结后停止写入、解冻后继续；只清理本 manager 任务；simple HTTP / RPC / PTY 路径。没有启动完整 envd/main、orchestrator 或 CH 服务。 |

首次完整短验收 `20260926T090846Z-1954195` 和复跑 `20260926T091827Z-1959097` 均为 PASS；后者的隔离 guest 为 `20260926T091830Z-1960776`。结果表明短验证可重复执行，不构成服务稳定性或性能统计。阶段解释、实际结果和接口见[本轮平台进展](records/PLATFORM_PROGRESS_20260926.md)。

完整服务集成需确认实验环境的 Docker、KVM/ublk 访问和受控 mount/tar/chown/umount 权限；现有文件层封存代码使用 `sudo -n`。当前隔离入口不访问宿主机 Docker/KVM，也没有验证完整服务所需的文件层操作。权限准备、新模板部署和 controller 服务联调仍是后续验收前提。

## 历史实验结论与边界

以下是既有实验记录中的结果，本轮没有重新运行这些完整服务实验：

- 官方 AgentENV Python 示例通过，使用固定预构建镜像与 `e2b 2.26.0`；不等于模型采样实验或学长 AgentENV fork 构建通过。
- Exp1 的完整重放与指定步骤恢复、独立 SVG 属性用例和上游测试子集通过；仍缺官方任务专属完整评测、更多任务和其他 checkpoint 边界。
- AgentENV snapshot 双实例的已知文件页共享/写入隔离，TrEnv-X DAX 只读层共享、OverlayFS 整文件 copy-up 和两代封存重建已有校准；两后端写入语义不同，尚无任务级公平内存收益结论。
- 在线复制 ext4 upper 后直接 `ro,noload` 封存曾遗漏 fsync 更新；任务 cgroup 冻结、guest sync、CH 暂停期间复制的受控 fixture 通过。新后端代码补丁已交付，完整服务中的同一协议仍未验收。
- 监控正确性/清理已做 10 对 A/B、10 对 A/A 和 10 个交错块、共 80 次完整重放；A/A 波动与 A/B 同量级，5% 目标未判定。schema=2 的 8 次调用审计通过，尚未按新口径开展统计验收。

历史“下一步”描述的是各记录完成时的状态。任务 cgroup 和 controller 代码补丁如今已交付，不能继续将其视为尚未实现；同样不能把代码或 fixture 的 PASS 写成完整服务通过。历史原始数据没有随 Git 同步，学长历史 raw 尚未取得；全新机器端到端复现仍未验收。

## 下一步顺序

1. 确认集成环境具备 Docker、KVM/ublk 及受控文件层操作权限；为本实验明确独立服务端口、数据目录和私有凭据，不沿用历史服务结果路径。
2. 将 patched envd 装入新的 TrEnv-X 模板，在 guest 配置 `ASB_TASK_CGROUP_PARENT`，对齐控制器的 `DATA_ROOT`、guest 地址与 CH socket；先验证一次真实工具调用及其后台后代归属。
3. 完整服务中验证冻结确认、runtime 归档/sync、CH pause/copy/resume、upper 封存、新 guest 文件恢复及失败/超时后的恢复和定向清理。
4. 再运行真实冻结 trajectory，对齐动作哈希、预期失败和独立任务评测；增加其他 checkpoint 边界、打开文件探针，并明确文件恢复与进程恢复的区别。
5. 正确性通过后，固定计时、内存与 A/A、A/B 协议，开展 AgentENV/TrEnv-X 同语义任务级对照；后续再推进 GRPO/BPO/TVCache 调度形状。不能因区间跨过 5% 而改分组；调度形状不代表 RL policy 已训练。

## 证据导航

| 主题 | 详细记录 |
| --- | --- |
| 本轮平台交付、用户复跑及完整服务集成缺口 | [平台进展](records/PLATFORM_PROGRESS_20260926.md) |
| TrEnv-X 后端任务组接口、固定补丁及隔离验证 | [后端交付记录](records/TRENVX_TASK_CGROUP_BACKEND.md) |
| 历史功能收尾、独立测试、计时及 cgroup fixture | [收尾记录](records/CLOSEOUT_STATUS.md) |
| 历史 Exp1、监控 A/B/A/A、时延及服务盘点 | [阶段交接记录](records/PLATFORM_HANDOFF.md) |
| 单 VM、snapshot 双实例内存校准 | [VM 文件页](records/VM_FILE_CALIBRATION.md)、[双实例](records/SNAPSHOT_SIBLING_CALIBRATION.md) |
| DAX 与 checkpoint 文件层 fixture | [DAX 底层](records/TRENVX_DAX_PRIMITIVES.md)、[两代封存](records/LAYER_FIDELITY.md)、[在线不一致](records/ONLINE_UPPER_CONSISTENCY.md)、[受控协议](records/QUIESCED_UPPER_PROTOCOL.md) |
| 历史环境迁移与官方示例 | [迁移](../MIGRATION.md)、[cpu-15](records/CPU15_RESULTS.md)、[官方示例](records/KIMI_AGENTENV_EXAMPLE.md) |

固定源码版本见 [`configs/source-revisions.json`](../configs/source-revisions.json)。上游 checkout、运行时依赖/凭据及 `.artifacts/` 不随 Git 上传；修改固定 fork 的可复现补丁保存在本仓库 `patches/`。
