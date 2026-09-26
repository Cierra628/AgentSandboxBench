# rashen 用户环境准备（2026-09-26）

当前服务器内核 `6.19.9+`，实际执行用户 `rashen`（UID/GID 1012）。使用独立 worktree `/home/rashen/.codex/worktrees/trenv-task-cgroup/AgentSandboxBench`、分支 `codex/trenv-task-cgroup`。原 `/home/rashen/work/AgentSandboxBench` 未改动。

**已准备并验收的是开发、客户端与隔离验证环境；完整 AgentENV/TrEnv-X 服务尚未在 rashen 下部署或验收。** 没有重启共享服务、全局清缓存、修改用户组、接管已有模板或进行长时间性能实验。

## 已准备内容

| 内容 | 当前状态 |
| --- | --- |
| Python / Go / QEMU | 使用已有 Python 3.12.3、Go 1.22.2、QEMU 8.2.2；gcc、git、ldd、静态 BusyBox 可用。没有系统包安装或升级。 |
| 固定源码 | 官方 AgentENV、IncrementalDAX_moti 及其 AgentENV/TrEnv-X 子模块四个 checkout 已补齐，HEAD 与 `configs/source-revisions.json` 一致。保持独立且忽略。 |
| 固定补丁 | platform、TrEnv-X task cgroup 与 controller checkpoint 补丁已应用，幂等和实现一致性检查通过。 |
| Go 依赖与构建 | 缓存放在本 checkout 的 `runtime/go`、`runtime/go-cache`；静态 patched envd 已编译至 `runtime/bin/envd-task-cgroup`。尚未放入服务模板。 |
| TrEnv-X Python 客户端 | `runtime/trenvx-venv`，按固定 fork 的 `sandbox-sdk/poetry.lock` 导出主依赖闭包并核验下载哈希；通过 `.pth` 使用本地 SDK 源码，未修改 fork 的依赖锁。`pip check` 和 Sandbox/gRPC 导入通过。 |
| AgentENV Python 客户端 | `runtime/e2b-venv`，使用现有 `scripts/e2b-requirements.txt`，E2B 2.26.0。`pip check` 和 Sandbox 导入通过。两个 SDK 分开，避免 protobuf 5/7 依赖混用。 |
| AgentENV CLI | 官方固定 `v0.2.2` 发布包，`runtime/bin/aenv` 与 `aenv-buildctl`；版本输出为 aenv 0.2.2 / BuildKit 0.33.0。CLI 包不是对固定源码提交的本地编译证明。 |
| 隔离测试内核 | 使用已有只读 `/var/lib/trenvx/kernels/ch-6.1.134/vmlinux`；QEMU TCG 不需要 KVM、sudo 或网络。 |
| shell 环境 | `runtime/activate.sh` 只改变 source 它的当前 shell；PATH 指向私有 bin/TrEnv-X venv，Go 缓存和 XDG config 都在本 checkout。未修改 `.bashrc`。没有设置服务地址、API key 或复制旧凭据。 |

这些 runtime 文件归 rashen 所有；两个 venv、bin、Go 缓存根目录权限为 0700。缓存、二进制与原始结果没有提交到 Git。

## 当前服务器如何使用

无需再次安装依赖，直接运行：

```bash
cd /home/rashen/.codex/worktrees/trenv-task-cgroup/AgentSandboxBench
bash scripts/41-verify-trenvx-server.sh
```

该入口核对固定源码/补丁、两个 SDK 和 CLI，运行 Go/Python 契约、实际 envd 包检查及编译，最后在一次新建的无网络 QEMU guest 中测试真实 cgroup 和实际 simple HTTP / RPC / PTY 启动代码。终端打印 `status=PASS` 和结果目录；各阶段独立保存日志，失败会记录 FAIL 与最后阶段。host go test 中内核用例 SKIP 不算通过；内核与启动路径的 PASS 来自新 guest。

交互开发时可用：

```bash
source runtime/activate.sh
python -c 'from sandbox_sdk.sandbox import Sandbox; print("TrEnv-X SDK OK")'
aenv --version
# AgentENV SDK 用它自己的解释器：
runtime/e2b-venv/bin/python -c 'from e2b import Sandbox; print("E2B SDK OK")'
```

`41` 是短验收入口，不会启动完整服务或运行 trajectory。完整 checkpoint/controller 的使用边界和集成接口见 [TrEnv-X 后端记录](TRENVX_TASK_CGROUP_BACKEND.md)。

## 准备步骤的复现

以下从 worktree 根目录执行，需要网络下载依赖；不调用 sandbox 服务。它不是全新服务器的系统包/内核安装器：

```bash
bash scripts/14-fetch-research-sources.sh
python3 scripts/40-apply-trenvx-task-cgroup.py
bash scripts/42-prepare-python-clients.sh all

# 固定 CLI，避免调用上游 installer 的 releases/latest。
mkdir -p runtime/bin runtime/agentenv-client
curl --fail --location --connect-timeout 10 --max-time 60 \
  https://github.com/kvcache-ai/AgentENV/releases/download/v0.2.2/aenv-linux-x86_64.tar.gz \
  -o runtime/agentenv-client/aenv-linux-x86_64-v0.2.2.tar.gz
printf '%s\n' 'bb5fc2237b94a38e942931c2e3f606d86d70009759a597356629b9ae64862368  runtime/agentenv-client/aenv-linux-x86_64-v0.2.2.tar.gz' | sha256sum -c -
tar -xzf runtime/agentenv-client/aenv-linux-x86_64-v0.2.2.tar.gz \
  --no-same-owner -C runtime/bin aenv aenv-buildctl
chmod 700 runtime/bin runtime/bin/aenv runtime/bin/aenv-buildctl
bash scripts/41-verify-trenvx-server.sh
```

CLI SHA-256 是本次官方固定 URL 下载内容的锁定值，内置 manifest 声明 0.2.2 / linux-amd64；没有取得 GitHub Release API 的发布者 digest（API 请求返回 403）。Go 按固定 fork 的 go.mod/go.sum 解析；本机已准备缓存，新环境首次编译仍可能需要网络。`ASB_TEST_KERNEL` 可覆盖验收入口的内核路径，需要兼容 CLONE_INTO_CGROUP/freezer/cgroup.kill。

## 实际结果与剩余权限

最终验收 `20260926T090846Z-1954195`：8 个 Go 契约 PASS（含 race）、4 个 Python 契约 PASS；固定 envd 包检查/编译 PASS；guest `TestKernelDescendantsFreezeCleanup` 和 `TestIsolatedEnvdLaunchers` PASS。覆盖 shell 退出后的 setsid 后代归属、冻结停止写入、解冻继续、异常解冻和只清理本 manager 的任务。两个 SDK 导入和依赖一致性、固定 CLI 版本通过。最小证据为 [rashen-server-setup-20260926.json](evidence/rashen-server-setup-20260926.json)。

初次一键验收 `20260926T085635Z-1942097` 在 guest 的 RPC 启动失败：私有 host umask 导致打包的 guest 目录为 0700，普通 guest user 无法执行 bash。已把 guest 公共目录显式打包为 0755、`/tmp` 为 01777，并复测通过。失败原始结果仍保留，没有覆盖或算为成功。

当前 OS 权限盘点：rashen 仅属于 rashen/users；`/var/run/docker.sock` 属 root:docker、`/dev/kvm` 和 `/dev/ublk-control` 属 root:kvm，均为 0660。Docker 访问和 KVM 打开实际返回 Permission denied；`sudo -n true` 返回需要密码。没有自行调整这些权限。

完整服务还需要管理员/集成方：

1. 配置 rashen 对实验所需 Docker 和 KVM/ublk 设备的访问方式，并为现有 `checkpoint_dax.py` 的受控 mount/tar/chown/umount 提供权限；它目前使用 `sudo -n`，加入设备组并不能解决文件层封存权限。
2. 准备独立 TrEnv-X data_root、服务端口、CH socket 路径和 DAX 模板，将新 envd 装入该模板并配置可写 guest cgroup v2 parent / `ASB_TASK_CGROUP_PARENT`。已有系统 CH/Firecracker 二进制的存在不代表这一服务组合通过。
3. 对 AgentENV 使用固定镜像并创建本实验独立实例、镜像/模板和私有凭据；不能直接沿用历史配置中的 `.artifacts` 服务记录。

服务准备后仍须验收真实任务执行、controller 的 pause/copy/resume 和新 guest 文件恢复，再做 trajectory 重放与指标收集。当前 PASS 不证明完整 CR、任意父进程内存恢复或跨后端性能收益；TrEnv-X 当前恢复的是文件状态。
