# Agent 沙箱实验平台：阶段交接

当前目标是复用 IncrementalDAX_moti，建立冻结轨迹执行、动作边界 checkpoint/restore、独立采集和可复算报告闭环。优先单机 CLI，不以模型训练、网页或集群部署为前置条件。

## 仓库与同步

2026-09-24 在 `/home/yyxie/agentenv_experiment` 初始化独立 Git 仓库，分支 `main`，remote 为 `https://github.com/Cierra628/AgentSandboxBench.git`。本地提交 `902c805` 已将 `AGENTS.md` 从 Git 跟踪中移除并加入 `.gitignore`；截至本记录远端仍在 `628514b`，因为服务器没有可用的 GitHub HTTPS 凭据，推送被拒绝。因此远端当前版本仍含 `AGENTS.md`，需要有凭据的用户执行 `git push origin main` 才能移除当前分支中的该文件。其历史提交仍可访问；如需从历史中彻底清除，须另做影响所有提交的历史改写。原 `AgentENV/` 和新 `IncrementalDAX_moti/` 仍是独立嵌套仓库，已整体忽略；运行时和 `.artifacts/` 也忽略。不要 `git add -f` 这些目录。

平台可同步内容为 `scripts/`、`configs/`、`patches/` 和文档。`configs/source-revisions.json` 固定源码版本；`patches/incrementaldax-platform.patch` 保留学长代码的局部改造。新机器可运行 `bash scripts/14-fetch-research-sources.sh` 获取相同源码及补丁；该脚本尚未在全新目录做完整复现验收，不覆盖版本不同的现有 checkout。

- 学长主仓库：`a9bc7deadb68a6e24b7242eab343dfedf953ea38`。
- AgentENV fork：`6c67f92afb8e697bd4d5eb1715864119c1e10c5d`。
- TrEnv-X fork：`53041d8c924514fca27e363cce7f5af8079267ce`。
- 子模块 SSH 在端口 22 被重置；通过同一仓库 HTTPS 地址获取，保留原 gitlink SHA 和 `.gitmodules`，没有替换成不同上游。未递归获取历史 cgexec gitlink。
- 本次未修改两个 fork 的源码；运行的仍是已有官方预构建 AgentENV，不能称为 fork 构建验收。

## 服务器只读盘点

证据：`.artifacts/platform-inventory-20260924T030710Z/`；重跑入口 `python3 scripts/12-inventory-platform.py`（只写本地结果，不修改系统配置）。

| 项目 | 状态与边界 |
| --- | --- |
| Host | cpu-15，x86_64，Linux 6.19.9+；不等同 guest 内核 |
| CPU | 128 逻辑 CPU，2 socket × 32 core × 2 thread，Xeon Platinum 8562Y+ |
| 内存/磁盘 | 总内存约 503 GiB；当时约 474 GiB available；项目所在磁盘约 148 GiB 可用，属于共享资源而非预留额度 |
| KVM | 实际 open + ioctl API=12；此前 microVM 已成功运行 |
| ublk | 控制设备可读写打开；此前设备启动成功；本轮盘点未创建设备 |
| cgroup v2 | controllers 包含 memory/cpu/io；根 cgroup 无 memory.current 是层级位置差异，服务 cgroup 另行核对 |
| BPF | bpftool/bpftrace 可执行、BTF 存在、程序查询成功；未做程序加载/挂载测试 |
| pagemap | 本进程实际驻留页 PFN 非零、kpageflags 读取成功；未据此声称 guest→host 映射已校准 |
| 工具 | Python 3.12、jq/column/zstd/flock、Docker 可用；无系统依赖安装 |
| 其他实验 | 存在其他用户进程、大量 Docker 工作负载和另一套 aenv-server；未停止、重启或清缓存 |

当前项目服务为 `.artifacts/server-smoke-20260922T114006Z-1387374`，API 18000；8000 的 aenv-server 属于既有其他工作，禁止替换。实例池 VMM 不等同活跃用户 sandbox。

## 真实代码审计与复用

已按交接顺序阅读主 README、deployment 两页、两个 baseline 说明、workload 索引及 experiments README，并检查 Exp1 runner、guest runner、分析器、host collector、Exp2 transition 与 Exp3 controller/physical_cache。

| 模块 | 第一版策略 |
| --- | --- |
| Exp1 冻结输入 | 直接复用 26 个 action 和 SHA256 manifest；每步独立 bash、cwd=/testbed；008/016 的 127 为预期失败 |
| Exp1 runner/guest-runner/analyze | 直接复用；外层 `run_exp1.py` 固定服务、版本、输入哈希及独立输出，增加顺序/失败集合/oracle/清理核验 |
| Host collector | 原版全机 pgrep 取最大 VMM，已修为选定服务 cgroup 内；仍是该组最大 RSS VMM，不称为精确 sandbox 归因；后续复用 host-vmm collector 的汇总并记录池开销 |
| Exp2 snapshot/create/start | 已复用持久 snapshot 生命周期，在 Exp1 第 013 步后保存并删除原实例，恢复后从 014 续跑；本机可恢复和进程连续性已验证 |
| Exp3 controller | 直接保留既有 GRPO/BPO/TVCache 调度形状，暂不运行并发；不是 RL policy 训练 |
| 物理页探针 | `physical_cache.py` 使用 4096 字节页及 guest 地址过滤 `<0x140000000`；必须先校准共享/私有/CoW，再用于定量结论 |
| TrEnv-X checkpoint DAX | 标为仅文件状态继承；干净 VM 模板不保持任意父进程、匿名内存和打开文件状态 |

已核实公共路由仍指向旧 Exp3/4/5 目录；当前 Exp3 必须用私有入口。其 controller 生效动作超时 600s、guest 采样 0.1s，不能用 config.env 默认值代替。原仓库 `raw/` 不存在，只有报告不能复算历史数据。

## 分阶段验收

1. **环境、版本和安全入口**：本轮已完成上述读取、盘点、版本固定与最小服务归因修正。
2. **Exp1 单轨迹**：入口 `bash scripts/13-run-exp1.sh`；配置 `configs/exp1-agentenv.json`。资源为 2 vCPU/4096 MiB；保留 guest cache，不做任何 host drop_caches；采样 0.1s。该组标为官方镜像默认功能组，不等同学长 balloon-off 历史组。首次运行 `20260924T031016Z-1652038` 在固定 OCI 镜像首次转换时超过 CLI 的 120 秒请求超时，未进入动作执行。随后用 `bash scripts/15-prewarm-exp1-image.sh` 异步构建同一 digest 的模板，结果 `.artifacts/exp1-image-prewarm/20260924T035140Z-1671165/` 为 PASS，模板已清理。重跑 `20260924T035910Z-1677178` 为 PASS：26 个动作顺序和 SHA-256 输入检查通过；仅 008/016 返回预期 127；第 024 步关键输出符合 oracle；最终 patch SHA-256 为 `f1cc23ac4f5ac1cff87eb7f8c54f8176c1a47cec37fa4cb6907b9dd8ad199af2`；sandbox、模板均已清理。证据在 `.artifacts/platform-exp1-agentenv-default/{raw,audit}/20260924T035910Z-1677178/`，离线 `summary.md` 和 SVG 曲线在同一 output_root。冷启动 2587 ms，guest 动作耗时之和 2127 ms；两者不等于完整控制器端到端时间。23 条 host 与 95 条 guest 原始采样可复算报告。历史 raw/最终 patch 参考缺失，未验证 patch 一致性或独立任务测试，未量化监控开销；这仍是单次功能与采集管线验收。
3. **指定步骤恢复**：入口 `bash scripts/16-run-exp1-checkpoint.sh`，第 013 步结束后运行 `aenv snapshot create`、删除原实例、从 snapshot 新建实例并续跑 014–026。运行 `20260924T072030Z-1690521` 为 PASS：动作输入 SHA-256、顺序、008/016 的预期失败、第 024 步关键输出均通过；最终 patch 与完整重放逐字节一致，SHA-256 均为 `f1cc23ac4f5ac1cff87eb7f8c54f8176c1a47cec37fa4cb6907b9dd8ad199af2`。来宾探针 PID=361、start_ticks=79、boot ID 恢复前后相同，计数从 26 增至 31；这支持 VM 内进程连续性，而不表示宿主机 Firecracker PID 原地保留。原始结果和逐项验收在 `.artifacts/platform-exp1-checkpoint/{raw,audit}/20260924T072030Z-1690521/`。两个沙箱及 snapshot 删除成功，最终列表均为空。`raw/cleanup.log` 是在运行后根据 audit 中三个 CLI 删除回执重建，已标注来源；无需重复长实验。打开文件描述符、跨服务重启或远端持久化尚未验证。
4. **指标和报告**：guest meminfo/vmstat 与 host 服务 cgroup/最大 VMM 分开，不混加。上游分析器已去除写死的历史 Direct I/O 对照和缓存数值，图例明确 guest `Cached+Buffers+SReclaimable-Shmem` 与 host cgroup `file` 口径不同。离线运行 `python3 scripts/report_exp1_checkpoint.py .artifacts/platform-exp1-checkpoint/audit/20260924T072030Z-1690521` 可重建阶段报告；当前结果为创建至工具可用 2853.8 ms、checkpoint API 779.8 ms、恢复至工具可用 198.3 ms，guest 26 步执行时间之和 2706.0 ms。原始采样 36 条 host、101 条 guest，SVG 曲线在 `.artifacts/platform-exp1-checkpoint/figures/`。本轮没有记录完整控制器端到端时间，不能用阶段之和替代；新控制器已加总耗时字段，留待后续新运行验证。观测峰值仅是采样最大值；更精细的 Cached/Shmem/slab 拆分和监控开销仍待完成。
5. **开销与精细内存**：当前属于诊断采样组，未测监控开销；需无采集/轻量采集配对并验证相同语义，5% 只作待确认目标。精细扫描按边界独立计时；未测 BPF 加载、DAX/CoW 校准或 guest→host PFN 覆盖率。
6. **历史组和调度扩展**：另行构建固定 fork 的隔离服务，明确 free-page reporting、DAMON、I/O 和缓存配置；不改共享服务。之后才推进 TrEnv-X 与多分支成对运行。

不运行的步骤标未验证；独立任务测试不以 patch 一致代替。所有重试使用新 run_id，不覆盖原始运行。raw、配置、输入哈希、退出码和日志只保留本地，Git 只保存可共享代码及摘要。
