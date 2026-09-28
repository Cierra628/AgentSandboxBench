# TrEnv-X Exp1 重放与第 013、022 步恢复（2026-09-27）

Exp1 的 26 个冻结工具动作已在实际 TrEnv-X 服务中完成一次完整重放，以及第 013、022 步后的文件 checkpoint/restore 续跑。三种模式各有一次正确性与清理通过。**这是单任务功能验收；没有性能统计或任意父进程恢复结论。**

## 已验证的结果

原始证据位于本机 `.artifacts/trenvx-service-smoke/<run_id>/`，脱敏摘要见 [Exp1 evidence](evidence/trenvx-exp1-20260927.json)。

| 模式 | run_id | 结果 |
| --- | --- | --- |
| 001–026 完整重放 | `20260927T110632Z-2094910` | PASS |
| 001–013 → 文件 checkpoint → 删除父实例 → 新 guest 续跑 014–026 | `20260927T110838Z-2095712` | PASS |
| 001–022 → 文件 checkpoint → 删除父实例 → 新 guest 续跑 023–026 | `20260927T112826Z-2098756` | PASS |

三次运行使用相同 seed 文件哈希、内核、CH/envd/服务二进制和固定源码版本，均为 2 vCPU / 4096 MiB。沿用[服务 smoke](TRENVX_SERVICE_SMOKE.md)的私有 namespace 和模板准备方式；没有重建 Docker digest 对应的全新基础镜像。

- 本地动作文件、manifest 和 guest 传输后的动作哈希一致；实际执行顺序严格为 001–026。
- 仅 `008` 与 `016` 返回预期的 127，保留原轨迹中的失败探索。
- 第 024 步 SVG 输出符合 oracle；独立 SVG、嵌套 SVG、普通 HTML 三个用例的格式化和幂等性通过。修复前 SVG 用例失败，说明测试确实覆盖了本次问题。
- 恢复运行的前 13 步记录（含退出码和逐步时长）及 baseline tree 与 checkpoint 前一致；续跑从 014 开始，没有重新执行前缀。
- 第 022 步把 `svg:script` 修复写入 `/testbed/src/language-html/utils/index.js` 后，保存前与新 guest 续跑前的源码 SHA-256 均为 `51d07fd7752ca36d8f059b7897ee6c8a77e9946c1ce22f129ad8f7dc6a1c1ea5`，Git diff 字节也一致。前 22 步记录及 baseline tree 保留，续跑从 023 开始。
- controller 捕获审计确认冻结、CH 暂停/复制/恢复、解冻全部完成；子 guest 的 checkpoint 层为 `ro,dax=always`。
- 三种模式的最终 patch 和第 024 步输出完全一致。patch SHA-256 为 `f1cc23ac4f5ac1cff87eb7f8c54f8176c1a47cec37fa4cb6907b9dd8ad199af2`。
- 另与已有 AgentENV 原始运行 `20260924T035910Z-1677178` 离线比较，manifest、初始 Git HEAD 和最终 patch 字节一致，见完整重放目录的 `agentenv-reference-audit.json`。这不是运行环境或性能等价证明。
- SDK 任务清理、实例删除、服务退出、所属 cgroup 删除及 loop 设备检查通过；运行后没有遗留本次 VMM 或 host 15005 监听。

主要文件：`result.json`、`correctness.json`、`replay-config.json`、`guest-environment.json`、`replay/steps.tsv`、每步 stdout/stderr 和 `patch.diff`。恢复运行另有 `prefix/`、`checkpoint.json`、`restore-environment.json`、`controller/checkpoint-1-quiesce.json` 和模板构建日志。第 022 步运行还保存 `source-before/after.json` 与 `source-diff-before/after.json`。原始脚本与冻结输入也保存在每次目录中。

## 实现与复现

`scripts/replay_trenvx_exp1.py` 复用学长仓库已适配的 AgentENV guest runner，使两后端执行相同冻结动作，保留每动作一个 bash、cwd=`/testbed` 的语义。通过实际 SDK 上传输入和取回结果，恢复调用已有 `Controller.capture_template/start/delete`，没有重新实现运行时或 checkpoint 算法。

动作和 runner 位于 guest `/opt/asb-exp1`，重放产物位于 `/root/asb-exp1/replay`，随文件层继承。配置显式关闭 guest 内存采样和 cache drop；保存的逐步时间只用于执行证据，不用于本轮性能结论。

已准备好服务依赖和 seed 的本机，以 root 执行：

```bash
bash scripts/45-replay-trenvx-exp1.sh
bash scripts/45-replay-trenvx-exp1.sh --checkpoint-after 013 \
  --reference .artifacts/trenvx-service-smoke/20260927T110632Z-2094910
bash scripts/45-replay-trenvx-exp1.sh --checkpoint-after 022 \
  --reference .artifacts/trenvx-service-smoke/20260927T110632Z-2094910
```

复跑时将 `--reference` 换成本次成功完整重放的 `result_dir`。每次新建运行目录，失败、重试和原始镜像均不覆盖已有结果。当前 CLI 仅支持已验收的 `013`、`022` 两个恢复边界。

## 尚未验证与下一步

第 013 步仍处于源码阅读阶段；第 022 步则在主要源码修改之后。首个第 022 步尝试 `20260927T112658Z-2098122` 因新增检查假设了错误的缩进而在 checkpoint 前断言失败；实际 diff 已含 `svg:script`，服务和资源清理均通过。修正断言后一次复跑通过。这不是服务故障证据。

复制抛错及子进程超时的真实服务受控恢复已另行通过，见[服务 smoke 记录](TRENVX_SERVICE_SMOKE.md)；自然超时和其他故障点仍待验收。首个第 022 步失败运行的镜像已在用户授权下归档为该运行目录的 `data.tar.gz`，日志和结果仍在原位。随后接入统一采集。当前磁盘仅余约 38 GiB，新增大镜像运行前须先规划原始产物保留空间。

TrEnv-X 继续只声明文件状态继承，不能移用 AgentENV 的进程连续性结果。官方完整任务测试、更多任务、打开 FD、跨服务重启、远端持久化、统一内存/计时报告与监控开销统计仍未验收。本轮未新增内存样本，不能据此比较两后端收益。
