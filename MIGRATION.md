# AgentENV 迁移交接

目标是在新机器跑通官方 AgentENV 单沙箱，再验证暂停和恢复。2026-09-22 已完成 Alpine 模板功能 smoke 和单次暂停/恢复、来宾进程连续性验证；详见 [cpu-15 验证报告](CPU15_RESULTS.md)。

## 官方示例进展（2026-09-23）

已复用新机器服务跑通 AgentENV 官方 Quick Start 的 Ubuntu 22.04 模板及 Python SDK Usage。固定 SDK 为 `e2b 2.26.0`；2.51.0 使用 v2 创建接口，与当前固定镜像不兼容（405）。运行入口是 `scripts/10-run-official-python-example.sh`，证据和结论边界见 [官方示例报告](KIMI_AGENTENV_EXAMPLE.md)。本轮测试 sandbox/template 已清理，服务保留运行。

## 已有证据

源码在 `AgentENV/`，提交为 `2f48c9c78ec58e4d73c49aa07cb81d984a4a4184`，打包前上游工作区干净。实际服务使用官方预构建镜像，其版本独立于本地源码：image ID `sha256:db9eb044ae3d85f9b276b9d2e5dea28cb0be95263d217ea4989f095e1d828681`，镜像引用 `ghcr.io/kvcache-ai/aenv-server@sha256:c5cd67759d365669b558d3754a18bdf52fa8b7614fe23bcb34250e723cf2ee03`。本地 CLI 为 aenv 0.2.2。

旧机器服务健康及认证 API 已通过。Ubuntu 镜像请求曾因注册表连接失败而超时；Alpine 模板随后完成镜像解析，但在共享只读 ublk 设备启动时返回 `start_dev ctrl request: Invalid argument (os error 22)`。主证据目录是 `.artifacts/template-build-diagnostic-20260922T072524Z-3134572`。失败设备有删除记录，本次模板清理通过；更早的脚本异常留下一个 error 状态模板，不能宣称整个服务无残留。

旧机器运行 `6.17.0-19-generic`。内核兼容性只是待检验假设，EINVAL 本身不足以证明内核版本不兼容，也不能证明升级或降级必然解决。README 中此前将此归因为兼容性并建议直接换内核的表述过强，以本交接说明为准。`/boot/vmlinuz` 链接不等于 GRUB 实际默认启动选择。

## 迁移内容

`scripts/08-pack-migration.sh` 生成私密压缩包，包含源码及其 `.git`、AGENTS.md、脚本、报告、历史 `.artifacts` 和 CLI 二进制。排除 `runtime/config/` 凭据、`.cache/` 和上游构建目录。压缩包含未脱敏本地日志，仅在协作机器间通过 SSH 传送，不发布到 GitHub。用户级 Codex 设置和对话不在此包内。

新机器将包解压到新建的 `~/agentenv_experiment`，不要覆盖同名已有项目。传输后先运行 `sha256sum -c SHA256SUMS`，再解压。CLI 二进制仅适用于兼容架构；新服务认证信息需重新生成。

## 新机器执行结果与当前入口

主机为 `cpu-15`，内核 `6.19.9+`。固定镜像准备通过；首轮越过 ublk 后在等待 envd 时超时，补充内部网段 NO_PROXY 后单轮功能和暂停恢复均通过。成功服务结果目录为 `.artifacts/server-smoke-20260922T114006Z-1387374`，API 为 `127.0.0.1:18000`，代理为 `http://127.0.0.1:17891`。实例保留运行，测试 sandbox/template 已清空，旧机器和首轮结果不得当作当前服务入口。

02 已固定 digest，03 可直接接收代理和端口创建新实例，06 自行生成新服务凭据，09 验证暂停恢复；不再需要先运行 04 或替换容器的 05。05 尚保留旧路径，当前不要调用。复现和证据见上述报告，不重复已完成 smoke。

## 迁移时的下一步计划（历史）

先读取 AGENTS.md，再运行 `bash scripts/01-check-host.sh`；需要特权的补充检查由用户运行 `sudo bash scripts/01-check-host.sh`。根据结果决定运行环境准备，不直接沿用旧服务结果目录或容器 ID。

新部署前需要调整脚本：02 当前使用 latest，正式对照应改用上述记录的镜像 digest；05 硬编码了旧准备结果目录，应改为读取新机器的准备结果。旧代理地址 `127.0.0.1:17890` 仅适用于旧机器。核实新机器代理后再选择网络模式。03 服务通过后用新结果路径调用 04，生成新服务凭据；后续模板试验才能调用 06。07 用于导出底层失败证据。

验收要求服务 API、沙箱启动、命令执行及文件读写分别通过，清理单独记录。服务健康或诊断采集 PASS 不等于沙箱跑通。先单轮 smoke，通过后再扩展；不要直接重复旧机器的长时间失败实验。
