# 交接说明（检查点 handoff-20261118）

## 一句话

这个仓库是单机 Agent 沙箱实验平台：跑冻结的工具轨迹、在 checkpoint 处保存恢复、同时测 host 内存与 guest 文件缓存的物理驻留。**轻量内存曲线已经可用；逐沙箱缓存的物理驻留卡在测量速度上，尚未解决。**

这份文档给从本检查点另起一条路线的同学。页级测量这条线仍由原开发者继续，见文末"分工边界"。

## 现在能复用什么

| 能力 | 状态 | 证据 |
|---|---|---|
| 逐沙箱 VMM PSS＋父组 current/file 曲线，100 ms 周期 | 已验证 | 七角色 Prettier BPO，726 轮，421 轮七份完整，联合读取窗口中位 40.8 ms |
| 批级四来源物理并集（VMM／直接后备／下层／provider），按物理页号去重 | 已验证 | 双实例校准，三段会话共 22 点原始重算一致；其中含四来源读数的是恢复实例的 5 个点 |
| 逐沙箱 guest 缓存容量（逻辑页数） | 已验证 | 首查即可得约 30,000 条记录 |
| 快照保存／恢复、七分支并行恢复 | 已验证 | 并行恢复总耗时 0.365 秒 |
| 七实例逐沙箱缓存的 host 物理曲线 | **未交付** | 见下面"撞墙结论" |

## 撞墙结论（重要，不要重复走）

七实例在缓存峰值处**无法取得时间对齐的页级数据**，原因是测量比现象慢：

- 恢复后 guest 缓存约 30,044–30,228 条记录，**约 5.07 秒内**降到 2,519–6,502 条。
- 而一轮七路四来源查询需要 **10.265 秒**、16.116 CPU 秒。
- 单实例恢复首点 37,352 页用时约 0.61 秒；后续点最高 37,514 页，用时 0.38–0.50 秒。七个实例串行 2.3–3.9 秒，并发实测 1.27 秒（低负载）与 10.265 秒（峰值负载）。
- 因此 1 秒周期在峰值负载下不成立：26 万页 × 约 9 µs ≈ 2.4 秒是下限。

已经试过并排除的方向（不要再花时间）：

- **增量计数**：只能省 provider 槽位元数据读取（111 次、1.4 MB），省不掉物理查询。身份未变的页里仍有 680 张的物理状态变了。
- **跳读物理层**：VMM 和直接后备都有实测反例，39 张页在偏移不变时驻留状态改变。
- **合并 tracer**：分派次数从 166 万降到 23.7 万，墙钟没有改善——瓶颈转到后端与下层查询本身。
- **提前采样起点**：并行恢复加立即开始，首轮仍只有 2,299–6,003 页，因为一轮要 10 秒而缓存 5 秒就衰减。

## 如果要走替代路线，这三个方向还没被证伪

1. **内核侧聚合计数**：在 guest 或 host 内核里维护"当前缓存页对应的物理驻留量"累加器，用户态一次读回，避免逐页 syscall。
2. **抽样估计**：每轮只查 N% 的页，报告估计值与置信区间。精度换速度，但必须明确它不是逐页确认。
3. **批量接口**：把后端与下层查询合并成一次返回位图，减少往返。

这三条都需要独立立项，且都要先回答同一个问题：**能不能在一秒内给出七份同一时点的物理量。**

## 代码结构（入口在哪）

| 看什么 | 去哪 |
|---|---|
| 平台任务配置与调度 | `scripts/platform_tasks.py` |
| 七角色 BPO／GRPO 运行入口 | `scripts/run_prettier6604_grpo.py`、`scripts/run_prettier_light_bpo.py` |
| 轻量曲线（PSS＋父组） | `scripts/light_prettier_rollout.py`、`scripts/light_memory_round.py` |
| 页级四来源查询 | `scripts/live_task_cache.py`、`scripts/snapshot_cache_residency.py` |
| provider 槽位 | `scripts/provider_slot_cache.py` |
| 会话生命周期与窗口 | `scripts/rollout_cache_sessions.py` |
| 实验组内存范围 | `scripts/experiment_memory.py` |
| 闭合判定（segments_v3） | `scripts/report_experiment_schedule.py` |

## 上手先做这一步

```bash
git clone <repo>
cd agentenv_experiment
git checkout handoff-20261118      # 从本检查点起分支
bash scripts/check-platform-offline.sh
```

正常结束输出 `status=PASS scope=platform-offline`。这条不启动服务、不建沙箱、不需要 root。**不要用全量 unittest discover 代替它**——部分历史测试依赖本机保存的实验输入。

## 分工边界（重要）

页级测量这条线由原开发者继续，从 `handoff-20261118` 之后在 main 上推进。为避免互相覆盖，请把这些文件视为**活跃开发区**，改动前先沟通：

- `scripts/snapshot_cache_residency.py`
- `scripts/live_task_cache.py`
- `scripts/provider_slot_cache.py`
- `scripts/rollout_cache_sessions.py`
- `scripts/snapshot_layer_metadata.py`、`scripts/snapshot_lower_owner.py`
- `scripts/cache_round_clock.py`
- `scripts/run_live_child_calibration.py`、`scripts/run_rollout_cache_calibration.py`
- 对应的 `tests/test_snapshot_*.py`、`test_rollout_cache_sessions.py`、`test_live_task_cache.py`

相对稳定的部分，可以放心作为基础：

- `scripts/experiment_memory.py`、`scripts/light_memory_round.py`、`scripts/run_guest_light_memory.py`、`scripts/host_sandbox_monitor.py`
- `scripts/report_experiment_schedule.py`（判定规则 segments_v3）
- `scripts/platform_tasks.py`、`scripts/validate_isolated_agentenv.py` —— 属于平台入口，改动影响面大，**两边都要动时先商量**

分支约定：从 tag 拉出自己的分支，不要直接推 main；需要合并时走 PR，由原开发者先看。

## 几条必须遵守的边界

- **`results/` 不入库**，只是本机证据。文档里的 `../results/...` 链接在克隆后是死链，属正常。
- **不使用 17891 代理**做实验流量；需要联网时有界直连，失败就停。
- **不改判定阈值**：file 闭合 1 MiB、偏离占比 5%、单段 1 秒，都是历史结论的依据。
- **provider 不按分支分摊**：槽位按设备分配，记录里没有分支 ID，逐分支数字是编的。
- **PSS 不等于沙箱总成本**，父组 file 不能除以沙箱数当逐沙箱缓存。

## 当前状态一句话

代码可跑、测试可过、轻量路径可交付；页级四来源这条已被测到墙，替代路线尚未验证。
