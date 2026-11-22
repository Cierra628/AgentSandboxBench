# Prettier GRPO：逐动作采集接线与单段验证

**第一阶段部分通过，完整 rollout 未启动。** 已放开 GRPO 轻量 PSS 的入口，并在固定镜像的原始多动作段上验证了逐动作放行。停在缓存指标选择：七支全部冷启动，而已有恢复校准使用的四来源算法有三项依赖恢复时绑定的内存后备。没有把冷启动的单来源数当作四来源结果。

## 第一阶段：通过项与证据

- **轻量 PSS 参数通过。** 启动器 `scripts/run_prettier_light_bpo.py` 新增显式选择 GRPO，未指定仍为 BPO；`run_schedule_smoke.py` 及 `light_prettier_rollout.py` 允许两种调度，私有服务、VMM 身份绑定和 BPO 专属检查点仍有原限制。业务入口 `--schedule grpo --schedule-smoke-metrics --light-rollout --check-only` 返回 `status=VALIDATED` 并列出七个角色。`tests/test_light_prettier_rollout.py` 4 项通过。这是离线入口验证，不是实测 PSS 曲线。
- **逐动作放行通过单段验证。** 使用固定镜像及原清单，在隔离的本地文件视图里运行 early_1 路径的最后一段（6 个动作）。只改测量用 runner 副本：每步结束写入角色、段、步号，收到匹配放行标记才继续。宿主在每步故意延迟放行，其间验证下一步没有启动；五个相邻动作均为“宿主已发出放行 < 下一步实际开始”。例如第一步放行为 1058691591834999 ns，第二步开始为 1058691618703698 ns，同一宿主单调时钟。这里的等待是离线测试，不是虚拟机暂停。
- **业务对照通过单段验证。** 动作清单与原实跑逐字节相同；6 个动作的角色、顺序和退出码逐一相同；生成的最终补丁 1,612 字节，与该支在历史完整 GRPO 最后一段的补丁逐字节相同。最初核验脚本曾将前一段 inspect 的补丁错作终点；按冻结的 `rollouts.json` 改为实际最后一段后通过。没有修改冻结期望。[原始逐步时间戳与历史回执位置](segment-proof.json) · [复核脚本](check-segment.py) · 动作放行实现：`scripts/grpo_action_gate.py`。
- **缓存会话没有接成可验收的 GRPO 业务路径。** 历史原始 GRPO 回执（同上批次 `prettier6604/sandbox-ids.tsv`）七支均为 `created_from=root`，没有恢复实例。现有 `RolloutCacheSessions.prepare/_new` 将冷启动选为 `parent`；`LiveTaskCache._start_snapshot_queries` 仅对恢复等会话绑定内存直接后备、快照下层和 provider。`results/rollout-cache-calibration-retry-20261117/REPORT.md` 对源实例也明确采用 VMM 页表确认口径。把七支登记为 restored、用 rootfs 盘冒充内存后备、或把三项未查填零，均不是正确接线。段内协议已验证，跨真实 guest 的通知、暂停、缓存核对和放行未验收。

## 第二阶段：没有实跑

| 支名 | 应有缓存点 | 本轮缓存点 | PSS 点数、周期、最大间隔 |
|---|---:|---|---|
| backbone | 29 | 未采 | 未采 |
| early_1 | 21 | 未采（离线 6 步不是缓存点） | 未采 |
| early_2 | 21 | 未采 | 未采 |
| early_3 | 21 | 未采 | 未采 |
| late_1 | 32 | 未采 | 未采 |
| late_2 | 32 | 未采 | 未采 |
| late_3 | 33 | 未采 | 未采 |

没有业务回执，无法判断整轮漏点、串支或暂停对官方检查的影响。业务耗时、测量耗时、跨支同步等待、累计暂停和整轮墙钟均未测；没有把并行角色耗时相加当作墙钟。官方检查未运行，历史的每实例 0 通过、2 失败未当作本轮结果。未进入实跑空间准入，未启动业务或新服务。

**下一步取决于要交付的指标：** 若接受冷启动会话的既有口径，就报告“通过 guest 前后清单确认、在该 VMM 映射中驻留的宿主物理页”，未确认量另列，快照专属来源标不适用。随后才验收七角色真实绑定和按动作触发。若必须沿用恢复场景四来源并集，本 GRPO 配置不应开跑。

---

## 上轮前置核查（接线前的工作区，保留历史）


**没有启动 rollout。** 冻结轨迹保留了每个动作的原命令和执行顺序；但当前完整 GRPO 入口既不能启用指定的轻量 PSS，也不能把诊断内核和七角色缓存会话一起接入业务。按“任一前提不成立即停”，未改代码、未开卷，未进入接线或实跑。

核查对象：指定仓库的当前工作区（HEAD `eb63579`，含已有未提交修改）；以下行号对应核查时的文件。业务配置为 `configs/platform-prettier6604-grpo.json`，采用其 `frozen_export` 指定的输入。

## 第一阶段：三项核查

**1. GRPO 能否使用指定的轻量 PSS？不能，不只是一处限制。** PSS 指虚拟机进程的驻留内存，共享页按比例分摊。底层采样并不要求 BPO：`scripts/light_memory_round.py:32–42` 按已绑定进程读取 PSS，`scripts/light_prettier_rollout.py:34–44` 按角色激活；业务控制器也有创建前准备、获得沙箱 ID 后注册的接口（`scripts/run_prettier6604_grpo.py:619–634`）。实际入口有以下要求：

- 轻量启动器固定传 BPO（`scripts/run_prettier_light_bpo.py:51–52`）；即使直接使用下层入口，`scripts/run_schedule_smoke.py:150–153` 和 `scripts/light_prettier_rollout.py:24–31` 也分别拒绝 GRPO。后者由平台端和业务端各调用一次（`scripts/validate_isolated_agentenv.py:465–471`；`scripts/run_prettier6604_grpo.py:306–307`）。
- 平台另要求私有诊断服务和专属 VMM 进程组，以便把角色绑定到正确进程（`scripts/validate_isolated_agentenv.py:167–173,468–476`）。普通 GRPO 的周期 PSS 与结束时的 PSS 汇总是另一路监控（`scripts/run_schedule_smoke.py:36–53,105`），本轮不以它冒充指定路径。

**2. 能否保留原 shell 上下文逐动作重放？轨迹可以；宿主当前不能在动作之间拦住下一步。** 冻结包 `.artifacts/prettier6604/grpo-input-audit-20261008T120626Z/workload/guest-runner.sh:18–19,48–66` 逐行读清单，核对命令摘要；每个动作原本就在同一沙箱的 `/testbed` 下用独立的 `bash -o pipefail -c` 执行，继承段环境，保留文件系统变化，记录原退出码。并不存在必须跨动作维持的交互 shell。宿主核对每段清单、每步顺序、退出码与角色（`scripts/run_prettier6604_grpo.py:206–222`）。

宿主按段提交一次阻塞的执行请求，返回后才下载记录（`IncrementalDAX_moti/motivation/experiments/exp3-RL-fork/controller.py:207–229`）。因此**不必拆命令包**，但要实现目标，必须在原循环的动作结束处增加“通知宿主、等待宿主核对并放行”的同步点，保留同一角色、环境、超时和原命令。现有动作后记录只写本地文件，不等待宿主。不能轮询事后日志并假定下一步还没开始；也不能把整段重复启动，因为段开头会重置输出文件。原冻结 runner 受摘要校验（`scripts/run_prettier6604_grpo.py:171–190`），以后若做测量版，需保持原件及其校验，不可静默改包。

**3. GRPO 能否传入诊断内核并建立七角色缓存会话？底层能装，完整入口不通。** 两条参数路径分别核对如下：

- 任务配置的 `live_cache_kernel` 仅允许 `single`，通过后才转成诊断内核及缓存开关（`scripts/platform_tasks.py:82–89,245–248`）。调度入口虽解析 `--diagnostic-guest-kernel`，但必须同时开轻量模式，而该模式只接受 BPO；参数通过后才会转发（`scripts/run_schedule_smoke.py:141–153,227`）。
- 私有服务确有安装逻辑：核对内核文件，将其复制进本次环境并设置 `kernel.image_path`（`scripts/validate_isolated_agentenv.py:350–360,1319–1328`）。这不是 GRPO 可从入口使用它的证据。业务路径构造 `LightRollout(...,roles)` 未传 `page_cache`，默认值为 None；于是角色生命周期不会注册缓存会话（`scripts/run_prettier6604_grpo.py:1009–1015`；`scripts/light_prettier_rollout.py:35–44`）。已有独立七支会话入口要求七支**恢复**校准，选择另一执行程序，不是本次 GRPO（`scripts/validate_isolated_agentenv.py:314–328,1684`）。

## 第二、三阶段：未执行

第二阶段未接线、未改 BPO、未跑回归。第三阶段未申请卷，因此没有运行时的磁盘准入结果。下表只列验收分母；“未采”不是零值或业务失败。

| 支名 | 应有缓存点 | 本轮缓存点 | 本轮 PSS 点数、实际周期、最大间隔 |
|---|---:|---|---|
| backbone | 29 | 未采 | 未采 |
| early_1 | 21 | 未采 | 未采 |
| early_2 | 21 | 未采 | 未采 |
| early_3 | 21 | 未采 | 未采 |
| late_1 | 32 | 未采 | 未采 |
| late_2 | 32 | 未采 | 未采 |
| late_3 | 33 | 未采 | 未采 |

无动作回执，无法核对漏点或串支；累计暂停时长、业务墙钟均未测。官方检查未执行，历史每实例 0 通过、2 失败仅是后续对照（`results/prettier-grpo-local-no-proxy-controls-20261101/REPORT.md`），本轮无可判定的业务或采集结果。

**下一步建议（未执行）：** 若继续，先单独授权离线接线验证：确认同一 GRPO 调用能带上诊断内核、轻量 PSS 和七个冷启动角色各自的缓存会话；用原多动作段验证宿主放行前下一步不会开始，命令、顺序、退出码与最终补丁不变。通过后再安排一次正式运行，不用段末采样代替。
