# 平台后端交付与普通用户复跑（2026-09-26）

本轮交付固定 TrEnv-X fork 的任务 cgroup 生命周期及 checkpoint/controller 补丁，提供固定客户端准备和短验证入口。首次验收及复跑均通过契约、编译和隔离 guest 测试；**完整 TrEnv-X 服务、真实轨迹 checkpoint/restore 和任务级性能对照仍未验收**。

本记录说明平台进展与本轮证据。项目状态以 [STATUS](../STATUS.md) 为准；历史实验记录保留当时的结果和后续计划，不把其“下一步”当作当前进度。

## 原有平台与本轮改动

| 平台部分 | 原有能力 | 本轮完成 | 还缺什么 |
| --- | --- | --- | --- |
| 沙箱运行与轨迹重放 | Python/Bash 包装器复用 AgentENV 和 IncrementalDAX 的固定 workload/runner；历史 Exp1 26 步重放、预期失败和针对性功能检查通过。 | 提供固定源码和客户端准备入口；把 TrEnv-X 的 simple HTTP、process RPC 和 PTY 三条实际工具启动路径纳入任务 cgroup。 | TrEnv-X 完整服务部署；真实 TrEnv-X trajectory；更多任务与官方完整评测；本轮未验收新 trajectory 的自动生成。 |
| checkpoint/restore 管理 | 历史 AgentENV 指定动作边界 snapshot 后续跑通过；DAX/OverlayFS upper 封存与新 guest 重建有受控 fixture 证据。 | 任务创建/加入、inventory、冻结确认、解冻和定向清理；controller 捕获前冻结、runtime 归档/sync、CH pause/copy/resume、finally 恢复及清理的可复现代码补丁。 | 新 envd 模板与完整 controller 服务联调；真实文件层恢复和服务故障注入。 |
| 指标采集与分析 | guest/host 内存、事件、正确性和时延文件；已知负载物理页/DAX 校准、schema=2 计时审计。 | 保存短验证的版本、日志、状态及失败边界，提供一键复跑入口。 | 实际任务的指标口径、跨后端公平对照和监控开销验收；本轮没有新增性能数据。 |

任务组不会因启动 shell 退出而消失，后台后代仍可被 inventory 找到并冻结。`setsid` 改变会话，不改变 cgroup 归属。冻结/超时失败会尝试解冻；解冻失败会明确报错并保持工具准入关闭。清理只接受本 manager 记录的任务 ID，并核对目录 identity，不按 host PID 扫描清理。

现有冻结动作、动作哈希及预期失败规则保持原语义。隔离启动测试核对 stdout/stderr、退出码 127 和 task_id；它没有重放完整 Exp1，也没有替代独立任务评测。

## 实际使用的组件

| 组件 | 在平台中的作用 |
| --- | --- |
| 本仓库 Python/Bash 脚本 | 运行包装、有效配置/版本记录、动作校验、正确性检查、采样、时延审计与报告。 |
| 官方 AgentENV 与固定预构建服务镜像 | baseline 的实例启动、命令执行和 snapshot 路径；历史运行依赖 Firecracker、OverlayBD/ublk。客户端使用 aenv 0.2.2 和独立 E2B 2.26.0 环境；SDK 导入不证明服务连接或镜像可运行。 |
| IncrementalDAX_moti 固定版本 | 复用 workload、冻结动作、runner、controller、分析及 archive_upper/build_layer 的文件层封存逻辑。 |
| TrEnv-X 固定 fork | Go envd 的工具启动路径、原 Python sandbox SDK，以及既有 Cloud Hypervisor / DAX / OverlayFS 文件状态方案。 |
| Linux cgroup v2 | 按任务归属后代，确认 freezer 状态并定向清理；需要 guest 支持 CLONE_INTO_CGROUP、freezer 和 cgroup.kill。 |
| QEMU TCG 与固定 guest kernel | 本轮短验证的临时无网络环境，运行真实内核与实际启动代码；不代表完整 TrEnv-X 服务部署。 |

源码版本固定于 [`configs/source-revisions.json`](../../configs/source-revisions.json)：IncrementalDAX `a9bc7deadb68a6e24b7242eab343dfedf953ea38`，TrEnv-X fork `53041d8c924514fca27e363cce7f5af8079267ce`。上游 checkout 被忽略，安装修改保存在本仓库 `patches/`，不是仅留在本机嵌套仓库里。未升级上游版本。

## 复现前提与验证入口

本轮验证环境使用 Python 3.12.3、Go 1.22.2、QEMU 8.2.2、静态 BusyBox、6.1.134 guest kernel、两套独立 Python SDK 环境及 aenv 0.2.2 CLI。TrEnv-X Python 依赖来自固定 fork 的 Poetry 锁；AgentENV 客户端使用本仓库既有锁定清单。编译后的 envd 输出到忽略的 `runtime/bin/envd-task-cgroup`，并不随 Git 分发。

准备好的服务器从仓库根目录执行：

```bash
bash scripts/41-verify-trenvx-server.sh
```

新环境需要 Python 3.11+、Go/gcc、git、file、ldd、timeout、QEMU x86_64、静态 `/usr/bin/busybox` 和 `/bin/bash`。guest kernel 需支持 CLONE_INTO_CGROUP、cgroup freezer 和 cgroup.kill。`41` 是验证入口，不是系统安装器；默认内核路径为 `/var/lib/trenvx/kernels/ch-6.1.134/vmlinux`，其他环境可用 `ASB_TEST_KERNEL` 覆盖。QEMU guest 为 TCG、256 MiB、2 vCPU，无网络，不需要 sudo/KVM，也不写 host cgroup。

从仓库根目录准备固定源码和客户端；以下下载需要网络，不启动服务：

```bash
bash scripts/14-fetch-research-sources.sh
python3 scripts/40-apply-trenvx-task-cgroup.py
bash scripts/42-prepare-python-clients.sh all

# 验证入口要求固定 aenv 0.2.2；不调用 releases/latest。
mkdir -p runtime/bin runtime/agentenv-client
curl --fail --location --connect-timeout 10 --max-time 60 \
  https://github.com/kvcache-ai/AgentENV/releases/download/v0.2.2/aenv-linux-x86_64.tar.gz \
  -o runtime/agentenv-client/aenv-linux-x86_64-v0.2.2.tar.gz
printf '%s\n' 'bb5fc2237b94a38e942931c2e3f606d86d70009759a597356629b9ae64862368  runtime/agentenv-client/aenv-linux-x86_64-v0.2.2.tar.gz' | sha256sum -c -
tar -xzf runtime/agentenv-client/aenv-linux-x86_64-v0.2.2.tar.gz \
  --no-same-owner -C runtime/bin aenv aenv-buildctl
chmod 700 runtime/bin runtime/bin/aenv runtime/bin/aenv-buildctl
```

CLI 哈希锁定本次官方固定发布 URL 的下载内容，内置 manifest 声明 0.2.2 / linux-amd64；未核对发布 API 的 publisher digest。Go 按固定 fork 的 go.mod/go.sum 解析，首次构建可能需要网络。运行者需要自行准备兼容 kernel 和系统工具；上述步骤尚未完成全新机器的完整服务复现验收。

终端输出解释：

| 阶段 | 检查内容 | 结果边界 |
| --- | --- | --- |
| `patches` | 固定源码提交和已应用补丁的一致性。 | 检查代码安装状态，没有验证服务。 |
| `python-clients` | 两个 SDK 的依赖/导入及 CLI 版本。 | 没有建立完整服务连接。 |
| `go-contracts` | 8 个任务 cgroup 契约，含 race。 | 模拟 cgroup I/O；真实内核测试在 host SKIP。 |
| `python-contracts` | 4 个捕获/恢复/清理契约。 | 模拟 guest/CH，包括各阶段失败和异步取消后等待 worker。 |
| `envd-packages` | 实际 fork 的 process/terminal/taskcgroup 包检查。 | 接入可编译；terminal 包没有独立 host 测试。 |
| `envd-build` | 编译新版 envd。 | 未部署到模板或启动完整 envd/main。 |
| `isolated-guest` | 一次新的 QEMU guest 中运行真实内核、实际 simple HTTP/RPC/PTY 启动代码。 | 不含 orchestrator、template-manager、CH 服务或 DAX 文件层恢复。 |

`running=... log=...` 表示正在执行该阶段及其日志路径。脚本在阶段失败时停止；最后的 `status=PASS stage=isolated-guest` 表示整套短验证通过、最后完成的阶段是隔离 guest。`result_dir` 是该次独立结果目录，不是 sandbox 服务地址。

## 实际验证与复跑证据

| run_id | 结果 | 范围 |
| --- | --- | --- |
| `20260926T090846Z-1954195` | PASS | 首次 SDK/CLI、Go/Python 契约、envd 包检查/编译及隔离 guest。 |
| `20260926T091827Z-1959097` | PASS | 独立复跑相同入口，逐阶段日志与 result.json 核对一致。 |
| `20260926T091830Z-1960776` | PASS | 复跑对应的隔离 guest；两个真实 guest 用例通过。 |

复跑的 8 个 Go 契约、4 个 Python 契约均通过；guest 的 `TestKernelDescendantsFreezeCleanup` 与 `TestIsolatedEnvdLaunchers` 均通过，串口结果 `ASB_KERNEL_SMOKE_RC=0`。覆盖父 shell 退出后的 setsid 后代归属、冻结后 200 ms 写入不增长、解冻后继续、取消冻结后恢复和只清理本 manager 的任务。另一 manager 的进程保留；实际 simple HTTP、RPC、PTY 后代与 freeze/thaw endpoint 通过。

复跑的最小结果字段：

```json
{
  "status": "PASS",
  "last_stage": "isolated-guest",
  "exit_code": 0,
  "run_id": "20260926T091827Z-1959097",
  "full_controller_service_verified": false,
  "full_agentenv_service_verified": false
}
```

本机原始证据：`.artifacts/trenvx-server-validation/20260926T091827Z-1959097/` 的各阶段日志、environment.json 和 result.json，以及 `.artifacts/trenvx-task-cgroup/20260926T091830Z-1960776/` 的串口日志和 result.json。它们不随 Git 上传，复现者无需访问这些目录才能运行测试。平台短验证摘要已保存为 [trenvx-server-validation-20260926.json](evidence/trenvx-server-validation-20260926.json)，后端证据见 [trenvx-task-cgroup-20260926.json](evidence/trenvx-task-cgroup-20260926.json)。

固定 guest kernel SHA-256：`a50bbc107954f603fce3c8eb8105c6c65290dbe2224402061acc022c95968730`。复跑的 guest 测试二进制 SHA-256：`3f97d3086f7d8c740bfce3e3862608174eaaeb1ec58de2eb916ff00d6f0699d5`。短入口可复跑，不等于服务稳定性或性能统计验收。

## 完整服务还需要的接口与验收

| 集成要求 | 当前状态与验收方式 |
| --- | --- |
| 主机权限 | 集成环境需具备 Docker、KVM/ublk 访问及受控文件层操作权限。原 checkpoint_dax.py 使用 sudo -n 执行 mount/tar/chown/umount；隔离短验证没有验证这些权限或完整服务，需集成方确认。 |
| 新模板与工具入口 | 将 `runtime/bin/envd-task-cgroup` 装入新隔离模板，启动前设置可写 guest v2 parent / `ASB_TASK_CGROUP_PARENT`；验证工具和后台后代都经受控启动路径。当前 envd 仅已编译。 |
| controller 地址与归属 | 对齐独立 `DATA_ROOT`、guest 私有地址、服务端口和 CH socket；需要时设置 `ASB_CH_SOCKET_DIR`。使用本实例的任务 inventory 和 token，不扫描其他任务/服务。 |
| 完整文件捕获与恢复 | 验证 `/tmp` 归档、guest sync、CH pause/确认、upper copy、resume/确认、解冻、upper 封存与新 guest 文件恢复。上述完整服务链路尚未通过。 |
| 异常和清理证据 | 在服务中注入失败/超时，核对恢复与清理；保留 `checkpoint-*-quiesce.json`、`cleanup-*-tasks.json` 和工具输出/退出码。契约测试中的模拟异常不替代真实服务异常验收。 |
| 真实轨迹与指标 | 保留动作哈希和预期失败，执行独立任务评测；之后再按统一内存/计时口径做跨后端对照。本轮没有运行这些实验。 |

接口和补丁调用细节见 [TRENVX_TASK_CGROUP_BACKEND](TRENVX_TASK_CGROUP_BACKEND.md)。本轮没有重启共享服务、全局清缓存或运行长时间性能实验。

TrEnv-X 当前是**文件状态继承**：新 guest 使用封存文件层和全新 writable upper，恢复选定 `/tmp` 文件。父 VM 的 resume 是暂停继续；新 guest 不复活任意父 shell、后台任务、打开 FD 或完整父 VM 内存。因此下一步验收应明确检查文件内容与后续任务正确性，不能按任意父进程恢复宣称成功。
