# 同伴五任务：适配和离线验证

后续更新：2026-11-09 五条固定完整轨迹和所需采集已核验，原 FAIL 状态及失败现场保留；见[实跑记录](EXTERNAL_FIVE_MEASUREMENT_20261109.md)。下文保留 11 月 5 日的历史状态。

2026-11-05，Asia/Shanghai。代码适配及离线回归已完成，**真实测量尚未运行（0/5）**。本记录取代 `results/external-five-preflight-20261105/REPORT.md` 中仍待完成的代码接线状态；保留原报告作为历史，不修改旧证据。

基准仓库已执行 `git pull --ff-only origin main`，当前最新提交为 `07f9e6fdb49d80690624e8ce471a28be5257f6c8`。已完整阅读适用的 `AgentENV/AGENTS.md`、协作指南及测量计划；本次不修改 AgentENV Rust 源码。已有诊断脚本、其测试和历史未跟踪文件保留。

上游本机路径为 `/home/rashen/work/AgentSandboxWorkloadResults`，固定提交 `cbe767b2aa23ceb59875b1cb0a7a6141c749312a`，按 `provenance/agentenv-runs.json` 的 primary 选择完整输入；不调用模型生成轨迹。

## 五项执行与验收

| 任务 | 配置（仓库 configs/ 下） | 原动作数 | 资源与验收范围 |
| --- | --- | ---: | --- |
| HF | external-hf-model-inference-measured.json | 11 | 2 vCPU / 8192 MiB；固定模型脚本、辅助输入、后台服务及原正负样例 HTTP 检查；保留参考非零退出 |
| PDF.js #20957 | external-mozilla__pdf.js-20957-measured.json | 5 | 2 vCPU / 8192 MiB；原 npm ci、两轮 unittestcli；各 1127 specs / 0 failures / 56 pending；原目录检查 |
| Log4j2 | external-apache__logging-log4j2-measured.json | 10 | 2 vCPU / 8192 MiB；持久 Bash；最后 Maven BUILD SUCCESS、5 条 class 路径；命令跳过测试；原检查器 141 继续记 FAIL |
| token 计数 | external-count-dataset-tokens-measured.json | 16 | 2 vCPU / 8192 MiB；原依赖约束及辅助输入；答案 79586、science rows 26；原语法检查，不称完整大数据集 |
| CompCert | external-compile-compcert-measured.json | 318 | 2 vCPU / 32768 MiB；18 个读取动作所需的固定 file_reader；九处后台完成等待；原编译器编译运行检查 |

`external_five.py` 校验主记录、配置版本/资源、run/replay/steps/validation/OCI 来源和 C 辅助程序的哈希，并与固定 Git 对象逐字节核对。全量上传原始脚本及辅助文件，不改动作内容或预期退出。Log4j2 使用同一个 Bash；其他任务逐工具独立 Bash。CompCert 依赖等待另存起止和后台退出；原异步脚本未改写。原 shell pipeline 语义保留。

检查器仍以原 `bash -o pipefail` 执行。总体接线验收可以匹配预期退出，但报告始终单列原检查器实际 PASS/FAIL；预期 141 不变成测试通过。失败记录和下载目录保留，不重放失败动作或自动重跑任务。

## 固定镜像输入

新增 `external_five_oci.py` 接入只读本地 OCI 布局，按 primary 的 manifest/config/所有 layer digest、平台和 rootfs diff IDs 验证。原镜像必须先以完整 OCI 布局提供，单个 `latest` 引用指向固定平台 manifest；验证不重建镜像。

私有服务内的 regctl 包装只允许固定 manifest/config 和限定目标的本地 OCI copy，不回退 registry。镜像转换仍由原生 AgentENV 完成。HF 的源引用是多平台索引；实际 API 请求使用 primary 中确认的 linux/amd64 manifest，同时保存原源引用。PDF.js、Log4j2 本地镜像也通过同一路径接入，不把 localhost 标签当作公共镜像拉取。源服务和原依赖程序不修改。

OCI 校验会读取已指定的压缩 blob 来核验 digest，这是准备阶段；不预读转换后的下层数据来制造驻留。指定下层文件的测量只执行终点 mincore。当前没有下载、导出或重建任何实际镜像。

## 采集、计时与报告

- guest meminfo / Cached 原值及 host cgroup current/file/anon/kernel/shmem/dirty/writeback 每 0.1 秒采样；绑定本次实际 sandbox 的 VMM，周期 PSS 为 1 秒。
- 创建后、原动作前保存初始下层身份。重放及结果下载完成后、原结果检查和删除前，读取一次 PSS/N（N=1）和初始下层 mincore；按设备号/inode 去重，核验配置、文件和 VMM 身份，未知/变化立即报错，保留失败数据。不使用其他任务的固定文件数量。
- P1 前后保存同组 current/file。指定文件驻留大于 file 时标记覆盖不足；不将差额解释为已确认组外容量或共享节省。upper 等未查询对象未知。
- task 窗口从控制器入口、创建前开始，到原路径和下载返回结束；保存 replay/download 窗口、逐工具单调计时与累计、控制器和外层时长；父进程 systemd 调用及停止窗口另写预检目录的 launch.json，结果摘要保留其路径；无保存/恢复。
- 保存采样器 CPU/有效点数/观测时长/最大间隔；终点 PSS/mincore 分别保存 CPU 和墙钟；初始身份查询、采样管理调用单列。成本不解释为任务减速率，不增加采样开关对照。
- 原始采集、检查和清理保留在原 native 路径；结果入口位于新的 `results/external-five/<时间-PID>/`。`report/summary.json`、`REPORT.md`、`tools.tsv`、guest/host/PSS PNG/SVG 由保存的数据生成。host 统计列出创建前基线及全流程、task、replay 的观测峰值和样本中位数。

PSS/N、guest Cached、host current/file 和指定下层 mincore 分属不同物理范围，互有重叠，不相加构造总内存。

CompCert 的工具窗口为 14400 秒，控制器为 16200 秒；内部运行窗口 16500 秒，外层 TERM 截止 16620 秒、随后最多 120 秒 KILL 余量，native 调用预算 16800 秒，workflow 为 17400 秒。另四项采用 3600 秒工具窗口及对应嵌套预算。旧 900 秒 shell 截止和 20 分钟默认窗口不用于五任务入口，旧任务默认限制保留。超时仍失败，不延长到成功、不重跑。

## 验证证据

- `bash scripts/check-platform-offline.sh`：PASS；含新增 10 项五任务测试和原有回归。
- 五任务控制器测试覆盖 25 个组合：每项正常、重放失败、退出序列不一致、检查器不一致、终点身份错误；另测外层超时/失败收尾；均验证只重放一次、采样器停止及自身 guest 删除。全部为 mock/toy 输入，不创建真实沙箱。
- 合成 OCI 测试验证完整性和禁止 registry 回退；报告测试验证数量分开、时段统计、原检查器 FAIL 保留与缺失采集拒绝。
- 固定上游五项及辅助程序核验通过；实际 `file_reader.c` 以正式静态编译参数通过，并用临时文本检查读取范围。
- `git diff --check` 和所改 Python 文件语法检查通过。

本机证据：`results/external-five-adaptation-20261105/`。这不是五任务真实运行或测量验收证据。

## 当前测量阻塞

以 `preflight-final/preflight.json` 的带时间戳只读观测为准：可用磁盘约 **13.49 GiB**，小于严格大于 **58 GiB** 的入口门槛（32 GiB 固定工作卷 + 20 GiB 主机保留 + 6 GiB 余量）。这是入口运行预算，不是某个任务的业务磁盘需求或五项合计。服务器有其他活动，余量会变化，启动时必须再查。

另外，`runtime/bin/aenv` 尚未准备；当前用户无 Docker 访问及 root 运行权限，`sudo -n` 需要密码；未指定已核验的自有源服务；五项未提供可用、验证过的本地 OCI 输入路径。KVM/ublk 在宿主机存在，可用内存约 473 GiB，内存门槛通过。镜像和在线依赖是否可用仍未实跑确认。

源服务须是本次可管理的服务、运行中且镜像等于 `validate_isolated_agentenv.py` 的固定 IMAGE；后续从新实验 cgroup 启动私有服务/VMM。取得权限后可继续只读核对及准备客户端/镜像。不会请求在聊天中发送密码。

## 后续入口（本次不执行）

资源齐备后，每次只选一项、用新结果目录检查并运行。例如 PDF.js：

```bash
export ASB_AGENTENV_SOURCE_SERVICE=已确认可用的源服务名
python3 -B scripts/check_external_five.py /本机路径/AgentSandboxWorkloadResults \
  results/pdfjs-preflight-新的时间戳 --task mozilla__pdf.js-20957 \
  --oci-input /已准备的固定pdfjs-oci布局

# 需要实际 root 权限；其余四项替换配置和对应的 OCI 布局。
env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
    -u http_proxy -u https_proxy -u all_proxy \
  bash scripts/run-external-workload-light.sh \
    configs/external-mozilla__pdf.js-20957-measured.json \
    /本机路径/AgentSandboxWorkloadResults --oci-input /已准备的固定pdfjs-oci布局
```

测量前预检 BLOCKED 即停止，资源门槛不降低。每项一次完整轨迹；无重复、压力、多沙箱或多调度。发生失败保留原路径并检查日志，不自动重跑。

## 同日 runtime 与权限更新

后续会话已切换为完全访问；经用户授权验证 sudo root 和 Docker 成功。`runtime/bin/aenv` 已从既有平台复制并校验，版本 0.2.2、约 17 MB，版本/命令帮助/动态库检查通过。客户端缺失和权限不再是阻塞；连接及真实 API 尚未实跑验证。最新 root 预检可用磁盘约 12.84 GiB；兼容源容器均已停止，五项固定 OCI 布局仍待准备。证据为 `results/external-five-runtime-20261105/REPORT.md`、`runtime.json` 和 `root-preflight/preflight.json`；上节原预检值保留为历史。实际测量仍为 0/5。
