# 在另一台机器验证平台

本次目标是在独立机器、独立 checkout 中完成一次真实运行，检验部署步骤是否完整，而不是复算本机旧结果。先选择 AgentENV 单实例，不把 TrEnv-X、七实例或多轮性能比较作为前置条件。

## 当前前提

- Git 中包含平台代码、配置、测试和公开结果摘要；不包含 `runtime/`、两个上游 checkout、`.artifacts/`、镜像、原始日志或凭据。`AGENTS.md` 也只保留在各自本地。
- 固定源码与子模块版本在 `configs/source-revisions.json`。`scripts/14-fetch-research-sources.sh` 获取指定版本并应用平台补丁；访问失败时记录缺少权限的仓库，不擅自替换版本。
- `configs/platform-*.json` 是已准备机器的示例，部分字段引用本机历史输入及参考结果。新机需要生成输入，并在本地配置中填写新路径。
- `scripts/validate_isolated_agentenv.py` 的 `SERVICE`、`IMAGE`，以及部分旧脚本中的服务结果路径仍绑定本机准备状态；不能照搬服务名称。需要时只将本次运行依赖的值改为新机器配置，保持固定镜像 digest 和结果核对规则。
- `deployment --offline` 不查询服务，但仍要求本地 CLI、源码、输入及设备权限；它不是空白机器安装器，也不是与环境无关的单元测试。缺失时记录缺失项，不重复执行相同检查。
- 固定服务镜像为 `ghcr.io/kvcache-ai/aenv-server@sha256:c5cd67759d365669b558d3754a18bdf52fa8b7614fe23bcb34250e723cf2ee03`，本机 CLI 为 aenv 0.2.2，官方 Python SDK 示例使用 e2b 2.26.0。

## 建议顺序与完成标准

1. 拉取提交后的 main，记录平台 commit、机器信息、权限、正在运行的服务及磁盘空间。阅读 README、docs/USAGE.md、本说明及适用的本地 AGENTS.md。运行只读 `bash scripts/01-check-host.sh`，核验 KVM、ublk、cgroup v2、页大小和实际设备访问权限；物理页测量另核验 pagemap/PFN 与 BPF 权限。分别记录宿主机和 guest 内核。
2. 先运行下方不依赖旧实验数据的离线测试，随后获取固定上游。准备依赖、CLI和新的私有 AgentENV 服务，确认模板启动和工具通道可用。`02-prepare-runtime.sh` 会拉镜像并可能加载 ublk 模块，`03-smoke-server.sh` 会建立特权容器；先说明影响，遵守当地授权，不直接重启共享服务或照搬本机代理地址。
3. 使用现有准备脚本获取选定任务的冻结输入、固定镜像和独立测试数据。优先选择 Prettier #14400 完整单轨迹；若新任务准备链更适合当地环境，可改用 Prettier #6604 单路径并说明选择。需要历史参考 raw 的入口不得伪造参考文件或引用本机不存在的目录；记录缺失并补最小准备步骤，不先搭建所有研究任务。
4. 通过统一入口真实重放一次。核对动作哈希、顺序、预期失败、关键输出和最终 patch；独立任务评测单列。验证默认 guest 内存/page cache、CPU、I/O和 host VM 进程指标曲线及阶段时延。物理缓存量与 guest `Cached` 分开，精细分析的暂停与扫描用时单列；设备权限不支持时明确缺口。
5. 在该任务已支持的边界保存、恢复，并只执行后缀；核对文件、步骤进度和适用的进程状态。再从本次 raw 在新目录离线生成报告，核对结果一致。每项真实验证只先运行一次，失败定位首个边界，不无目的重复长测。

无需在线模型/API key 即可重放冻结轨迹。`collect-demo` 可作为工具采集的附加检查；`checkpoint-demo` 验证小负载的长期包保存与加载，不能代替完整任务恢复，也不要引用本机未提供的离线包。

初步成功标准：另一台机器的一条完整轨迹通过、一个支持边界恢复后状态和续跑通过、曲线与时延报告实际生成、仅本次拥有的运行资源清理通过。全新机器的监控性能开销未做对照时标为未验证，不自行添加性能阈值或多轮要求。

可先运行的离线检查（不启动 VM，不读取本机旧 raw）：

```bash
python3 -B -m unittest discover -s tests -p 'test_tool_trajectory.py'
python3 -B -m unittest discover -s tests -p 'test_native_checkpoint_bundle.py'
python3 -B -m unittest discover -s tests -p 'test_shared_task_cache.py'
bash scripts/use-platform.sh --help
```

## 交付给项目负责人的内容

在独立分支提交必要的最小部署修正、可复现脚本，以及一份 `docs/records/SECOND_MACHINE_<日期>.md`。报告写清机器环境、平台/上游版本、准备命令、实际验证命令、退出码、结果路径、任务正确性、恢复状态、指标范围和清理结果。执行失败记录首个失败边界与原因；不要仅凭单元测试或公开摘要宣布真实运行通过。

原始结果保留在新机 `.artifacts/`，重试使用新 run_id；不要上传密钥、原始环境、未脱敏日志、镜像、checkpoint或状态数据库。新机不继承本机历史删除授权，只清理本次拥有且当地已授权的资源。普通实现细节自行处理；依赖安装、内核或共享系统配置修改按当地权限确认。
