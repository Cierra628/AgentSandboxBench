# 阶段交接记录（历史）

这里保存阶段实验和本机证据；当前状态与下一步以[状态页](../STATUS.md)为准。所有命令从仓库根目录执行。

## 仓库与同步

2026-09-24 在 `/home/yyxie/agentenv_experiment` 初始化独立 Git 仓库，分支 `main`，remote 为 `https://github.com/Cierra628/AgentSandboxBench.git`。`AGENTS.md` 已从 Git 跟踪中移除并加入 `.gitignore`；本轮核对远端与本地均在 `1726558`，当前树不含该文件。旧提交中的历史副本仍可访问；如需从历史中彻底清除，须另做影响所有提交的历史改写。原 `AgentENV/` 和新 `IncrementalDAX_moti/` 仍是独立嵌套仓库，已整体忽略；运行时和 `.artifacts/` 也忽略。不要 `git add -f` 这些目录。

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
5. **开销与精细内存**：诊断 pilot 入口 `bash scripts/18-run-exp1-monitor-overhead.sh`；单组配对 `20260924T113554Z-1703968` 为 PASS，先无采样、后 guest/host 100 ms 采样。工具调用控制器耗时 4116.5→4260.6 ms（+3.5%），端到端 7442.3→8245.3 ms（+10.8%），冷启动 2540.0→2872.3 ms（+13.1%）。该单组不能判断 5% 目标。随后按约定运行 10 对轻量 host 模式，入口 `bash scripts/19-run-exp1-monitor-series.sh`，结果 `.artifacts/exp1-monitor-series/20260924T115556Z-1710191/{result.json,report.md}` 为 PASS。奇数对无监控→性能模式、偶数对反向；性能模式每 1 秒异步采集所选服务 cgroup 的 memory.current/stat，两臂都不采 guest，不扫 VMM smaps，不清缓存。首对 smoke 后才继续；20 次重放的输入哈希、26 步顺序、008/016 预期失败、第 024 步关键输出、最终 patch 与沙箱清理均通过；10 个性能臂各有 4–5 条 host 样本，无监控臂为 0 条。任务窗口包含监控启动与停止，配对变化中位数 -0.8%，范围 -10.3% 至 +21.0%，20,000 次配对 bootstrap 中位数 95% 区间 -5.3% 至 +11.5%；完整控制器耗时变化中位数 +1.8%。因此暂定 5% 开销目标为**未能判定**，不宣称达标或超标。短任务、共享主机负载和缓存变化仍是限制；该结果只覆盖轻量 host 服务级采集，不能外推到 guest、VMM PSS 或精细物理页扫描。原始数据、每臂配置、日志、主机 loadavg/内存 PSI 和执行代码快照在本地结果目录；结束后 sandbox/template 列表均为空。精细扫描需按边界独立计时；未测 BPF 加载、DAX/CoW 校准或 guest→host PFN 覆盖率。

   对上述 10 对又做了只读原始数据复算，入口 `bash scripts/20-analyze-exp1-monitor-series.sh .artifacts/exp1-monitor-series/20260924T115556Z-1710191`，结果为同目录的 `variance-analysis.{json,md}`。20 次运行的动作、patch、audit 和计时文件再次核对通过。任务窗口差 -457.9 至 +745.2 ms，而监控启停所在的工具调用外段差为 +0.3 至 +4.7 ms（中位数 +1.8 ms）；这不是采集总开销估计。guest 动作配对差与任务窗口配对差相关（样本内 Pearson r=0.91），第 023/024 步 Node/Prettier 调用占 guest 动作时间 84.0%。相邻两对的同模式运行也出现无监控 -12.5% 至 +19.6%、性能模式 -9.6% 至 +6.7% 的变化，但并非正式 A/A 对照。当前记录的 memory PSI 增量为零，缺少当时的 CPU/I/O PSI 和 guest 缓存状态，不能把波动归因于采集或其他单一原因。下一轮应先确定 A/A 噪声基线与配对轮数，并补齐边界 CPU/I/O 压力记录；新数据单独归档，不并入这 10 对。
6. **A/A 噪声基线**：沿用前轮 10 对规模，入口 `bash scripts/21-run-exp1-monitor-aa.sh`，结果 `.artifacts/exp1-monitor-aa/20260924T123515Z-1727686/{result.json,report.md}` 为 PASS。两臂同为无 host/guest 采样，运行配置相同；奇数对 A→B、偶数对 B→A。首对 smoke 通过后完成 20 次独立运行，输入哈希、26 步顺序、预期失败、关键输出、最终 patch、无采样与沙箱清理均通过；结束时 sandbox/template 列表均为空。A/A 的任务窗口 B/A 变化中位数 +5.3%，绝对变化中位数 9.4%，范围 -19.8% 至 +11.6%，配对 bootstrap 中位数 95% 区间 -10.9% 至 +9.9%。此前 A/B 的绝对变化中位数为 9.7%。这是不同时间的两批实验，不能直接相减作因果校正；但同配置 A/A 已显示本短任务有与 A/B 相近量级的自然波动。每臂边界记录 CPU/I/O/内存 PSI 和 loadavg；20 臂 CPU PSI some 增量为 11.0–17.5 ms，I/O PSI some 为 0–0.039 ms，memory PSI some 均为零。这些边界包围完整控制器运行，不精确等于动作窗口，也不足以解释 guest 第 023/024 步时延差。当前协议无法可靠判断 5% 目标；下一版应先改进窗口稳定性并单独验证测量口径，不能只靠增加相同的短任务轮数。
7. **交错块测量**：先运行四次重放的 ABBA smoke，结果 `.artifacts/exp1-monitor-block-smoke/20260924T125355Z-1739933/` 为 PASS；随后按授权继续至共 10 块，入口 `bash scripts/23-run-exp1-monitor-block-series.sh`，结果 `.artifacts/exp1-monitor-block-series/20260924T125713Z-1744514/{result.json,report.md}`。奇数块 ABBA、偶数块 BAAB；每块 2 次无采样和 2 次轻量 host cgroup 采样，各次均是独立完整 Exp1 轨迹。10 块共 40 次重放的动作、patch、采样状态和清理全部通过；结束时 sandbox/template 列表为空。块内两模式任务窗口均值的相对变化中位数 +1.6%，均值 -0.3%，绝对变化中位数 5.0%；配对 bootstrap 中位数 95% 区间 -6.2% 至 +5.1%，仍跨过暂定 5% 边界。已有 A/A 20 次运行按相邻两对分成 5 个四次重放安慰剂块时，绝对变化中位数仍为 6.1%；不同时间段不能直接相减。交错与块内汇总改善了波动，但**当前约 4 秒轨迹尚不能可靠判断 5% 开销目标**。初次系列脚本在 40 次运行完成后因报告函数的未定义变量退出；失败栈保留在 `failure.txt`。修复后运行 `bash scripts/23-run-exp1-monitor-block-series.sh --report-only .artifacts/exp1-monitor-block-series/20260924T125713Z-1744514`，独立核对全部 40 个 run_id 和原始结果并离线生成 PASS 报告，没有重跑沙箱；原运行代码和报告修复代码各有快照。此处 PASS 表示数据与修复后的报告通过，不掩盖首次自动汇总失败。下一次开销试验需改变测量窗口或任务设计并重新核对语义，而非只继续堆同样的短重放轮数。
8. **长窗口同 VM 校准**：将既有 40 次完整重放按相邻 ABBA+BAAB 合并成 5 个八次重放窗口，绝对变化中位数约 4.2%，但 5 个窗口的 bootstrap 中位数 95% 区间仍跨 5%；不继续用同样的短沙箱堆轮数。另行建立**诊断**入口 `bash scripts/24-run-exp1-monitor-calibration.sh smoke|full`：每个独立 VM 先完整重放 Exp1 的 26 步，核对 patch 与预期失败，再暖机并在相同文件状态下重复原轨迹第 024 步。单个调用经 AgentENV 工具通道执行，stdout 与原轨迹逐字节一致；轻量 host cgroup 采集只在性能阶段运行。工具窗口约 21–25 秒，不含另行记录的监控启停。smoke `.artifacts/exp1-monitor-calibration/20260924T131617Z-1769564/` 通过；随后固定四种 AA/AB 组顺序和 ABBA/BAAB 阶段顺序，入口 `bash scripts/25-run-exp1-monitor-calibration-series.sh`，结果 `.artifacts/exp1-monitor-calibration-series/20260924T132211Z-1772954/{result.json,report.md}` 为 PASS。4 个 VM 的 A/B 变化分别为 +2.02%、-0.02%、+1.67%、+4.91%，中位数 +1.84%；无采样 A/A 安慰剂分别为 -1.88%、-5.48%、-2.95%、-6.24%，中位数 -4.21%。bootstrap A/B 中位数 95% 区间 -0.02% 至 +4.91% 仅有 4 个 VM，不能作 5% 达标证据；A/A 的同向差异也尚无可验证原因。运行 `bash scripts/26-audit-exp1-monitor-calibration.sh .artifacts/exp1-monitor-calibration-series/20260924T132211Z-1772954` 从原始数据独立复核了 4 个 sandbox、32 个阶段、1024 次重复调用的输出哈希、耗时、采样、准备阶段 patch 与清理，结果 `raw-audit.{json,md}` 为 PASS。主机 CPU PSI some 每阶段增量 35.8–50.7 ms，I/O 0–0.031 ms，内存为零；这些快照不能单独解释调用波动。该同 VM 暖状态诊断与完整 26 步轨迹的任务窗口/端到端口径不同，不能混报；下一步需解释 A/A 差异并确定可验收的性能指标，或转入独立的内存归因校准。
9. **历史组和调度扩展**：另行构建固定 fork 的隔离服务，明确 free-page reporting、DAMON、I/O 和缓存配置；不改共享服务。之后才推进 TrEnv-X 与多分支成对运行。

不运行的步骤标未验证；独立任务测试不以 patch 一致代替。所有重试使用新 run_id，不覆盖原始运行。raw、配置、输入哈希、退出码和日志只保留本地，Git 只保存可共享代码及摘要。
