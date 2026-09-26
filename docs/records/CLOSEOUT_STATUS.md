# 第一版验收收尾（2026-09-26）

本轮完成针对性独立功能测试、计时字段拆分及任务 cgroup 冻结原型验证。所有新 smoke、输出核对和清理通过。完整基准任务评测、5% 监控开销统计验收和 TrEnv-X 生产 controller 接入仍未完成，不能把本轮 PASS 扩大解释。

## 独立功能与冻结轨迹

入口 `bash scripts/37-closeout-exp1.sh`，证据 `.artifacts/exp1-closeout/20260926T034752Z-1853329/`。复用原 AgentENV 固定镜像 digest、2 vCPU/4096 MiB 和学长 Exp1 的 26 个动作。本次为诊断，不采样、不清缓存、不合并入历史性能批次。

新增独立属性用例 `scripts/exp1_independent_check.cjs` 检查 SVG script、嵌套 SVG script 的 JavaScript 格式化及幂等性，并用普通 HTML script 作对照。初始镜像两项 SVG 格式化失败，普通 HTML 对照通过；完整重放后所有属性通过。用例不读取轨迹输出作为期望值，也不依赖 patch 哈希作为功能判据。

同时找到固定版本的实际 Jest 入口：

```bash
cd /testbed
CI=1 node --experimental-vm-modules node_modules/jest/bin/jest.js tests/format/html/svg --runInBand --ci
```

重放后 **1 个 suite、1 个 test、1 个 snapshot 通过**，没有更新 snapshot。完整重放的动作哈希、26 步顺序、008/016 的预期 127 和最终 patch 与已有参考逐字节一致也通过。属性用例和上游测试均位于冻结重放窗口之外。

这补上了“只有 patch 和原轨迹 oracle”的局限，但不是 SWE-PolyBench 官方完整评测：尚未获取并执行任务专属 test_patch / FAIL_TO_PASS / PASS_TO_PASS 清单，也未跑完整 Prettier 测试集。两个新 SVG 属性在修复前后的变化提供针对性功能证据；原有 SVG suite 单独通过不足以证明修复。

## 计时口径

上述诊断中的一次动作 024 分别记录：guest 子进程启动、执行及输出收集 **522.63 ms**；host CLI 调用至返回 **580.02 ms**；stdout/stderr 本地写入 **0.266 ms**。原始字段在 `timing.json` 和 `timed-action.json`，stdout 与原动作输出逐字节一致。

guest 时间使用 guest 单调时钟，只计算持续时间，不与 host 时间戳相减。CLI 时间包含 guest wrapper 启动、工具通信和 guest 计时文件写入，不能把两者之差称为纯网络/RPC 开销；下载计时文件单列在后续命令中。这是诊断探针，不是监控开销测量。

正式 `run_exp1_monitor_calibration.py` 已新增 **timing_schema=2**：

- `invocation_ns`：CLI 子进程启动至返回，排除本地 stdout/stderr 文件写入。
- `archive_ns`：本地 stdout/stderr 写入，不含后续哈希检查和 TSV。
- `duration_ns`：保留上述两者之和，避免把旧字段悄悄重新定义为纯调用时间。
- phase 增加两个分项总和；`tool_window_ns` 仍包含哈希和 TSV 等循环开销，明确标注。

新 smoke 入口 `bash scripts/24-run-exp1-monitor-calibration.sh smoke`，证据 `.artifacts/exp1-monitor-calibration/20260926T034924Z-1854251/`。准备阶段完整重放通过，两个阶段各 4 次动作调用通过。离线运行：

```bash
python3 scripts/audit_call_timing.py .artifacts/exp1-monitor-calibration/20260926T034924Z-1854251
```

8 次调用的时间加和、阶段总和、输出哈希及记录的清理状态核对 PASS，结果为 `timing-audit.json`。旧批次原始数据和结论未改写。没有新增 10 对统计实验，5% 目标仍为未判定；每次生产校准调用尚未加入 guest 计时 wrapper，避免把新增探针混入旧测量形状。

## 后台进程归属与捕获

在上述真实 AgentENV guest 6.1.175 中，创建本次专属 cgroup，将启动进程先放入组内再 fork。父进程退出，后代执行 setsid 脱离原会话，仍留在同一 cgroup。冻结后等待 `cgroup.events` 的 `frozen 1`，计数文件在 200 ms 检查窗口保持不变；解除冻结后继续增长。最后只清理该组的进程和目录，结果 `freezer.json` 全部 PASS。

随后入口 `bash scripts/38-check-task-cgroup-upper.sh` 将任务 cgroup 接入固定 CH / guest 6.1.134 的源 OverlayFS 捕获 fixture。证据 `.artifacts/online-ch-upper/20260926T035041Z-1855117/`，全流程 PASS：

1. worker 在派生命令前加入本次任务 cgroup；到达已知内容边界后继续等待，由组外控制脚本冻结 cgroup 并确认事件。
2. 冻结期间归档 tmpfs、guest sync，随后 CH pause/query、复制及 resume。
3. 父 guest 解冻任务，确认任务完成且 cgroup `populated=0`；文件最终到达 `1/1`。
4. 新 guest 从实际封存层重建 `1/0`，检查 whiteout、opaque、权限、链接和 runtime generation，封存层哈希保持不变。

CH 的 `host-cleanup-audit.json` 确认无关联 VMM、loop 或当前 namespace 挂载残留；两个 AgentENV smoke 均删除本次 sandbox，结束时项目列表为空。

这些验证覆盖继承 cgroup 的进程归属和已知边界，不是针对恶意任务的隔离机制。拥有 cgroup 管理权限的任务主动迁组、外部 daemon 代写、任意时刻的应用事务一致性，以及冻结超时的控制器恢复路径尚未验证。未将本次 fixture 直接接入学长生产 controller；其工具启动流程必须先保证每次工具及其后台后代进入指定任务组。

## 下一阶段

基础收尾证据已具备。下一阶段优先在隔离 TrEnv-X 服务中接入任务 cgroup 生命周期及失败解冻逻辑，再用真实冻结轨迹验证在线 checkpoint；随后固定 schema=2 的性能统计协议，按原定规模开展新数据采集。完整任务评测和更多 checkpoint 边界单列验收，不由本轮结果代替。

本轮没有安装依赖、重启共享服务或修改系统配置，没有提交/推送 Git。`AGENTS.md` 继续仅保留在本地。
