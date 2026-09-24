# cpu-15 AgentENV 单轮验证

2026-09-22，官方预构建镜像的 Alpine 模板功能 smoke 和单次暂停/恢复均通过。此结论不等于 Kimi K3 benchmark 完成，也不等于本地源码构建通过。

## 配置和范围

主机 hostname 为 `cpu-15`，内核 `6.19.9+`，Docker `29.4.0`，KVM ioctl API 版本为 12。ublk 已加载，未更改模块参数、内核或系统服务。

上游源码提交为 `2f48c9c78ec58e4d73c49aa07cb81d984a4a4184`，工作区干净。顶层目录无法识别为 Git 仓库，未暂存、提交或推送。

固定服务镜像引用为 `ghcr.io/kvcache-ai/aenv-server@sha256:c5cd67759d365669b558d3754a18bdf52fa8b7614fe23bcb34250e723cf2ee03`，实际 image ID 为 `sha256:db9eb044ae3d85f9b276b9d2e5dea28cb0be95263d217ea4989f095e1d828681`，与迁移记录一致。CLI 为 `aenv 0.2.2`。Alpine 3.20 在本轮解析到 `sha256:c64c687cbea9300178b30c95835354e34c4e4febc4badfe27102879de0483b5e`。

发现主机已有其他工作的 `aenv-server`，占用 8000 端口且镜像版本不同。本实验创建独立容器，使用 host 网络、`127.0.0.1:18000` API 和 `http://127.0.0.1:17891` 代理；PID 和容器数据独立，挂载主机 `/dev`。host 网络会创建主机网络规则和 veth，不能称为完全隔离。默认 ublk metrics 监听 `0.0.0.0:9103`。

## 结果

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 主机前提 | PASS | `.artifacts/host-check-20260922T112815Z/host.txt` |
| 固定镜像准备 | PASS | `.artifacts/runtime-prepare-20260922T113309Z-1364154` |
| 首轮服务健康、认证 API | PASS | `.artifacts/server-smoke-20260922T113346Z-1364951` |
| 首轮模板 | FAIL：等待 envd 60 秒超时 | `.artifacts/template-sandbox-smoke-20260922T113412Z-1367643` |
| 首轮诊断 | 共享只读及独占 ublk 已成功获取；未复现旧 EINVAL | `.artifacts/template-build-diagnostic-20260922T113556Z-1375880` |
| 补充 NO_PROXY 后服务健康、认证 API | PASS | `.artifacts/server-smoke-20260922T114006Z-1387374` |
| Alpine 模板构建、模板启动、命令及文件读写 | PASS | `.artifacts/template-sandbox-smoke-20260922T114045Z-1388687` |
| 单次暂停/恢复、进程连续性、恢复后文件读写 | PASS | `.artifacts/pause-resume-smoke-20260922T114444Z-1399613/checks.json` |
| 测试对象清理 | PASS：最终 sandbox 和 template 列表均为空 | `.artifacts/template-build-diagnostic-20260922T114536Z-1400352/final-checks.json` |

首轮只绕行回环地址。源码中的 envd 健康探测使用 HTTP client，来宾地址属于 `10.11.0.0/16`。补充 `10.11.0.0/16,10.12.0.0/16,169.254.0.20/30` 到大小写 NO_PROXY 后，envd 就绪且功能 smoke 通过。该结果支持内部请求代理路径导致首轮超时；这次同时开启了诊断日志，并非严格单变量消融，未捕获首轮代理请求轨迹。

暂停/恢复测试直接冷启动一个 Alpine 沙箱，启动持续递增计数的 shell 进程。API 列表确认暂停态及恢复后的运行态。恢复前后 guest PID 均为 365，进程 start_ticks 均为 55，boot ID 和文件标记相同，内存计数由 3 继续到 5。恢复后新增文件写入/读取成功。这验证了来宾进程状态连续性，不表示宿主机 Firecracker PID 原地保持，也未验证跨服务重启或跨主机恢复。

旧机器 `6.17.0-19-generic` 的 EINVAL 与新机器成功结果不能单独证明具体内核兼容性原因：迁移还改变了主机环境。本次没有更换内核或修改上游代码。

## 当前保留状态

首轮实验容器已停止并保留数据；成功实例仍运行于 `127.0.0.1:18000`，容器 ID 见 `.artifacts/server-smoke-20260922T114006Z-1387374/container-id.txt`。实例资源池、镜像缓存、串口日志、凭据和容器数据仍保留，未执行完整环境卸载。API 最后复查返回 204。

原有 `aenv-server` 的容器 ID、镜像、运行状态、启动时间及重启次数与实验前一致；未测试其他工作的应用功能。主机网络前后盘点见 `.artifacts/cpu15-baseline-20260922T1133Z` 和最终诊断目录。

## 脚本及后续

02 已固定镜像 digest。03 新增可选代理和 API 端口参数，能直接创建新实例；`AENV_SMOKE_DIAGNOSTICS=1` 启用 envd trace 与持久串口日志。06 从指定新服务生成凭据，存在旧凭据时先在私密配置目录备份，因此不必为生成凭据重复 04 的 Ubuntu 冷启动。09 新增独立的单轮暂停恢复验证。脚本修改前备份位于 `.artifacts/migration-script-backup-20260922T113247Z`。

本轮 02、03、06、09 均通过语法检查和对应真实执行。05 保留为旧迁移历史脚本，仍引用旧准备目录及固定端口，当前流程不使用它。

复现单次暂停恢复的调用如下；已有 PASS 无需无故重复：

```bash
sudo bash scripts/09-smoke-pause-resume.sh .artifacts/server-smoke-20260922T114006Z-1387374
```

后续可在确定轮数及负载后做稳定性和性能实验。当前不外推到并发、多轮、Ubuntu、Kimi K3 任务成功率或跨主机迁移。原始日志、凭据及运行时数据仅保留本地，不上传。
