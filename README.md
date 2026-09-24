# Agent 沙箱实验平台

## 独立 Git 仓库（2026-09-24）

当前根目录是平台自己的 Git 仓库，分支为 `main`，GitHub 同步地址为 https://github.com/Cierra628/AgentSandboxBench。`AgentENV/` 与 `IncrementalDAX_moti/` 保留独立仓库并被根仓库忽略。同步内容为文档、脚本、配置和补丁；凭据、运行时及原始实验数据不纳入 Git。

源码版本见 `configs/source-revisions.json`；获取固定源码和应用平台补丁的入口为 `bash scripts/14-fetch-research-sources.sh`。服务器核验、实现审计及分阶段进展见 [平台交接记录](PLATFORM_HANDOFF.md)。

## 官方示例已跑通（2026-09-23）

已使用固定服务镜像和 `e2b 2.26.0` 跑通 Kimi K3 报告所链接的 AgentENV 官方 Python SDK 示例，包括 Ubuntu 22.04 模板、创建、列举、命令、暂停和删除。版本兼容性及复现入口见 [官方示例报告](KIMI_AGENTENV_EXAMPLE.md)。

## 当前状态（cpu-15，2026-09-22）

固定官方镜像的 Alpine 模板功能与单次暂停/恢复已通过，来宾进程连续性及测试对象清理分别验证。当前实例为 `127.0.0.1:18000`，代理为 `127.0.0.1:17891`。配置、证据、保留资源及结论边界见 [cpu-15 验证报告](CPU15_RESULTS.md)。以下为旧机器历史记录，不作为当前操作步骤。

目标：在独立目录验证 Kimi K3 所引用的 AgentENV 能否运行。原 `sandbox_experiment` 项目、服务和实验数据保持不变。

## 代码与设置

- 官方仓库：https://github.com/kvcache-ai/AgentENV
- 2026-09-22 拉取版本：`2f48c9c78ec58e4d73c49aa07cb81d984a4a4184`。
- 上游代码位于 `AgentENV/`，保留独立 Git 仓库，未修改上游代码。
- 原项目 `AGENTS.md` 原样复制到本目录，原文件保留。没有复制认证信息、镜像、状态数据库或旧实验产物；用户级 Codex 设置保持原状。
- 本目录 `.gitignore` 将上游嵌套仓库和本地运行产物排除；上游版本由上述提交号记录。

## 首轮结果

当前结论：代码拉取成功，尚未证明真实沙箱跑通。

| 检查 | 结果 | 边界 |
| --- | --- | --- |
| 官方代码拉取 | 通过 | 上游工作区干净 |
| 官方 `verify-install-buildctl.sh` | 通过 | 使用模拟下载和临时目录，仅验证 CLI 打包安装逻辑 |
| Rust stable 工具链准备 | 失败 | rust-std 下载缓存文件重命名失败，原因未进一步确认 |
| 使用已有 Rust 1.89 尝试服务端构建 | 超时 | 45 秒上限内仍在获取依赖，未得到编译结果；这不是源码编译失败证据，也未验证该旧工具链兼容性 |
| KVM 普通用户访问 | 失败 | 实际打开 `/dev/kvm` 返回权限拒绝 |
| Docker 普通用户访问 | 失败 | daemon socket 权限拒绝 |
| ublk 前提 | 不完整 | 内核模块文件存在，但 `/dev/ublk-control` 不存在 |
| 创建、执行、暂停、恢复沙箱 | 未验证 | 运行权限与依赖尚未就绪 |

证据：`.artifacts/initial-check/{installer-test,toolchain,build}.log`；`.artifacts/host-check-20260922T062619Z/host.txt`。工具链准备涉及用户级 Rust 下载缓存，但未更改默认工具链或系统服务。

补充：已读取用户执行的 sudo 检查结果 `.artifacts/host-check-20260922T063654Z/host.txt`：Docker 29.0.2 可访问，KVM ioctl 返回 API 版本 12；ublk 模块文件存在，但控制设备尚不存在。此前日志所有权问题已由用户修正，检查脚本也已增加结果所有权交还逻辑。

## 下一步

2026-09-22 环境准备已通过，证据为 `.artifacts/runtime-prepare-20260922T064000Z-3098078`。Docker 镜像 ID 为 `sha256:db9eb044ae3d85f9b276b9d2e5dea28cb0be95263d217ea4989f095e1d828681`，拉取 digest 为 `sha256:c5cd67759d365669b558d3754a18bdf52fa8b7614fe23bcb34250e723cf2ee03`；ublk 已临时加载，控制设备存在。

当前下一步为 `scripts/03-smoke-server.sh PREPARE_RESULT_DIR`，由用户使用 sudo bash 执行。它按上述 image ID 创建独立特权 Docker 容器，挂载 `/dev`（这是主机设备访问，不是严格安全隔离），使用独立网络/PID 命名空间及仅本机可访问的随机端口，不挂载旧项目数据。健康等待上限 180 秒，认证请求限时 10 秒，密钥不打印。成功只表示服务健康和认证 API 可用，尚未验证沙箱功能；成功时保留服务供后续测试，失败时尝试停止本次容器并保留日志和容器数据，不自动删除。脚本语法及非 root 拒绝执行检查通过，真实执行待验证。

首次执行结果为 `status=FAIL stage=server-health`，证据为 `.artifacts/server-smoke-20260922T064426Z-3103403`；第二次仍因错误期待 200 而失败，证据为 `.artifacts/server-smoke-20260922T064954Z-3110256`。容器日志显示服务已完成初始化并监听 `0.0.0.0:8000`。源码定义 `/health` 成功响应为 HTTP 204 No Content，脚本现已接受 200/204，并记录实际状态码；同时保留宿主机映射端口探测、90 秒上限和 `INT/TERM/HUP` 收尾。

### 环境准备复现

只读主机检查已完成。下一步由用户拉取官方预构建镜像并临时加载 ublk：

```bash
cd /home/yyxie/agentenv_experiment
sudo -v
sudo bash scripts/02-prepare-runtime.sh
```

此脚本下载 `ghcr.io/kvcache-ai/aenv-server:latest`，记录 image ID 和完整镜像元数据，随后在模块尚未加载时执行 `modprobe ublk_drv`。不写持久配置、不更改已有模块参数、不安装或重启服务。镜像拉取限时 300 秒，完整输出保存在结果目录。模块不会自动卸载，以免影响其他使用者。脚本语法检查和普通用户拒绝执行检查已通过；特权准备步骤待用户执行。

后续部署应使用记录的 image ID，不继续跟随可变的 latest 标签；预构建镜像不一定对应本地源码提交，须分别记录，不能将其运行成功描述为当前源码构建通过。准备完成后再实施独立容器内的服务启动、单沙箱命令执行及暂停恢复验证，不使用 host 网络、不改旧 Kuasar 服务。只有真实功能验收通过才能称为跑通。

服务 smoke 通过后，使用 `scripts/04-smoke-cold-sandbox.sh SERVER_RESULT_DIR` 做真实冷启动验证。脚本在 `runtime/`（已忽略）放置官方 CLI 和临时凭据，从已通过的服务容器读取 API key，不把 key 写入结果日志；创建 `ubuntu:24.04` 冷启动沙箱，运行一次文件写入/读取命令，最后删除沙箱。冷启动、命令执行和清理分别设有超时并分别记录；任何一步失败都不是跑通。脚本语法检查、非 root 拒绝执行和失败状态输出已通过，真实特权执行待验证。

首次冷启动 smoke 在 CLI 下载阶段失败（GitHub TLS 连接重置），证据为 `.artifacts/cold-sandbox-smoke-20260922T065926Z-3116157`；服务容器仍未被判定为失败。随后已用普通用户通过官方 `AgentENV/scripts/install-cli.sh` 将 `aenv 0.2.2` 和 `aenv-buildctl` 安装到 `runtime/bin`，安装日志为 `.artifacts/cli-diagnose-20260922T070000Z/install-user.log`。因此下一次 smoke 会跳过下载，仅验证 CLI 连接服务和真实冷启动沙箱。

第二次冷启动 smoke 在 `stage=cold-start` 失败，证据为 `.artifacts/cold-sandbox-smoke-20260922T070310Z-3118217`。AgentENV 服务和 ublk 初始化均已完成；首个失败边界是 rootfs 镜像解析：容器内的 `regctl` 访问 Docker Hub 时连接被重置，GHCR 的 `ubuntu:24.04` 候选未授权。原因是主机代理仅监听 `127.0.0.1:17890`，桥接网络容器无法访问该回环地址。新增 `scripts/05-restart-server-host-proxy.sh`，只校验并替换本实验创建的容器，改用 host 网络并显式传入代理；成功后服务固定在 `127.0.0.1:8000`，再重跑冷启动 smoke。该步骤需要用户执行 sudo。

host-network 服务通过后，第三次冷启动请求在 `stage=cold-start` 超过 CLI 的 120 秒 HTTP 请求超时，证据为 `.artifacts/cold-sandbox-smoke-20260922T071225Z-3123211`。这说明请求已越过服务健康和认证阶段，但不能据此判定镜像转换或 Firecracker 启动成功；`aenv list` 未发现残留沙箱。为把长时间镜像转换与同步创建请求分离，新增 `scripts/06-smoke-template-sandbox.sh`：用 `alpine:3.20` 异步创建模板，轮询构建完成，再启动模板、执行命令并清理模板和沙箱。该实验的成功标准仍分别要求模板构建、沙箱启动、命令执行和清理通过。

模板 smoke 的首个失败边界已通过 `scripts/07-diagnose-template-build.sh` 定位。模板创建成功，但构建 sandbox 在获取共享只读 ublk 设备时失败：`start UVMUblkDev ... Invalid argument (os error 22)`；服务容器内的 `ublk-daemon.log` 同时记录 `UBLK_CMD_START_DEV` 失败。证据为 `.artifacts/template-sandbox-smoke-20260922T072102Z-3131969` 和 `.artifacts/template-build-diagnostic-20260922T072524Z-3134572`。主机当前内核为 `6.17.0-19-generic`，而上游 `storage/ublk/README.md` 明确列出的测试内核是 Ubuntu `6.8.0-87-generic` 和 `6.17.0-1007-oem`；这只定位了旧机器的失败边界，内核兼容性仍是待验证假设，不能据此直接要求切换内核。新机器结果见本文开头链接。

当前主机内核盘点结果：正在运行 `6.17.0-19-generic`；`/boot` 中可启动的内核为 `6.17.0-19-generic` 和 `7.0.0-31-generic`，其中 `/boot/vmlinuz` 默认链接到 `7.0.0-31-generic`。`/lib/modules` 下还存在多组 6.14、旧版 6.17 和旧版 7.0 目录，但对应 `dpkg` 状态为 `deinstall ok config-files`，属于卸载后的残留模块目录，不应作为可用内核统计。当前未发现上游 ublk README 列出的 6.8 generic 或 6.17 OEM 内核。

上游快速安装脚本会写入系统服务、模块配置及内核参数，当前未执行。需要此类操作时，另行提供有明确影响范围和恢复方案的脚本，由用户运行。
