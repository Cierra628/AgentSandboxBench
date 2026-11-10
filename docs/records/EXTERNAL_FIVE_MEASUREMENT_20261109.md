# 五项任务：实跑、严格验收与证据索引（2026-11-09）

**当前选定的五条完整固定轨迹均严格匹配预期退出码，所需采集证据齐全；原生全链路为 5 PASS / 0 FAIL。** HF已按用户授权完成修复后的完整纠正运行，当前原生回执PASS，历史FAIL/INCOMPLETE保留。Log4j2原检查器实际退出141，继续记FAIL且与原预期一致；其镜像为固定重建版本，与primary字节不相同。按原预期验收通过，不表示原检查器全部退出0。

正式总报告和各任务 MD 归档于 `docs/records/`；小型脱敏审计 JSON、逐工具 TSV、SVG 曲线归档于 `docs/records/evidence/external-five-20261109/`。`results/`、`.artifacts/` 保留本机原始运行产物、失败现场与固定镜像；不搬移原始证据，不把镜像、凭据、数据库或大日志加入文档。旧自动生成 MD 保留为历史产物，正式入口以本目录为准。

已 `git pull --ff-only origin main`，基准提交 `07f9e6fdb49d80690624e8ce471a28be5257f6c8`。上游固定提交 `cbe767b2aa23ceb59875b1cb0a7a6141c749312a`，以primary为准。当前更新包含用户明确授权的HF纠正重跑；其余四项沿用已通过的单沙箱运行，不进行重复、压力、多调度实验或调用模型。总记录和各任务文档持续覆盖更新，不为每次复核新开文档。

|任务 / 详细记录|动作数|原控制器/native/外层|原检查器|primary 镜像|
|---|---:|---|---|---|
|[HF 模型推理](EXTERNAL_FIVE_HF_20261109.md)|11|PASS/PASS/PASS|PASS / 0|精确匹配|
|[PDF.js #20957](EXTERNAL_FIVE_PDFJS_20957_20261109.md)|5|PASS/PASS/PASS|PASS / 0|精确匹配|
|[Log4j2](EXTERNAL_FIVE_LOG4J2_20261109.md)|10|PASS/PASS/PASS|FAIL / 141|固定重建，新摘要|
|[数据集 token 计数](EXTERNAL_FIVE_TOKEN_COUNT_20261109.md)|16|PASS/PASS/PASS|PASS / 0|精确匹配|
|[CompCert 编译](EXTERNAL_FIVE_COMPCERT_20261109.md)|318|PASS/PASS/PASS|PASS / 0|精确匹配|

原检查范围：HF 正负样例服务；PDF.js 两轮各 1127 specs / 0 failures / 56 pending；Log4j2 Maven 编译与五条 class 路径、原命令跳过测试；token 计数仅原 26 行、答案 79586；CompCert 32 GiB、318 步、九处后台完成等待及原编译器编译运行检查。预期非零保留，不扩展为完整官方隐藏验收。

|任务|任务窗口秒|原工具累计秒|终点 PSS/N MiB，N=1|终点初始下层 mincore MiB|下层文件数|
|---|---:|---:|---:|---:|---:|
|HF 模型推理|134.101|120.524|927.129|1578.855|6|
|PDF.js #20957|200.226|184.101|1931.742|2048.020|9|
|Log4j2|309.470|292.015|1986.125|2568.371|11|
|数据集 token 计数|107.219|100.530|1076.191|461.801|5|
|CompCert 编译|2097.039|1975.020|3367.691|281.691|3|

上述内存量互有重叠，不能相加。各任务页另列 guest used/Cached 曲线、host current/file 的创建前基线、全流程/task/replay 观测峰值与样本中位数、各采集器 CPU/点数/最大间隔、终点查询成本，以及采集对象身份和覆盖不足。upper 等未查对象未知，不记为零；没有采样开关、重复、压力或多调度实验。

## HF：当前回执与历史失败

HF已按用户授权完成修复后的完整纠正运行，当前原生回执PASS，历史FAIL/INCOMPLETE保留。原目录错误是读取下载结果时多拼了一层input/。当前数值来自本页选定的真实运行，旧失败目录仍可追溯。HF仅在本guest禁用Xet、走Hub HTTP，固定模型和原命令保持。

## Log4j2：固定重建与原检查器 FAIL

原 primary OCI 已被清理，采用固定上游文档允许的原 Containerfile、固定基础镜像和源码提交重建；全部基础 diff IDs、构建输入及新 OCI blobs 经核验，新旧 manifest 在任务页单列。实际检查器退出 141 与参考一致，只表示保留了原检查语义，不表示检查器成功。

## 失败历史、采集与清理

11 月 6 日的失败及 11 月 9 日纠正运行分别留在原路径，详见各页源路径和本机 `results/external-five-recovery-20261109/progress.json`、`serial-status.tsv`。此前 HF 下载超时，PDF.js 安装连接断开，token 的 Hugging Face 请求断开，CompCert 安装依赖失败；没有静默删除失败轮次。服务器曾重启，后四项前重验源服务。

自有 guest、私有服务、挂载/loop、relay 和批次源服务已清理；失败原卷仅回收零数据区，前后 SHA256、inode 与逻辑大小核验保留。共享主机的外部 VMM 变化继续记录，自有源服务检查通过，不主张主机独占或因果性能。

[本次严格复核计数](evidence/external-five-20261109/audit.json)；[原批次清理回执](evidence/external-five-20261109/batch-cleanup.json)。每个任务 audit.json 包含对原始源文件的 SHA256；本机源路径缺失时应明确报缺，不将离线副本当成新的真实运行。

本次归档时服务、镜像、磁盘与权限检查另见 [host-check.json](evidence/external-five-20261109/host-check.json)。源服务已按批次清理停止；本次只读文档审计不需要重启服务。

本次 `bash scripts/check-platform-offline.sh` 通过；它验证离线接线，不代替原生严格回执。未提交、推送或发布原始运行数据。

## 按原任务要求逐项检查

最近核对日期：2026-11-10。结果：**PASS**，范围为原任务规定的功能和采集验收；此次核对没有重新运行任务。继续更新本节及原审计摘要，不另开复核记录。

|要求|完成情况|核对依据|
|---|---|---|
|准备、权限与固定输入|原实跑PASS；当前源服务已清理|原五次运行前预检均READY，root权限、内存/磁盘门槛及固定输入通过。当前离线检查PASS，镜像与输入仍在；现有结果验收完成，重新实跑须重新准备源服务并满足严格大于58 GiB预算。|
|固定版本与完整原轨迹|PASS|上游cbe767b2固定primary；脚本和辅助输入逐字节匹配；当前11/5/10/16/318步退出码全部匹配，每次选定运行仅一个guest创建和一次完整重放。|
|PSS/N|PASS|实际sandbox ID绑定PID/start_ticks/boot_id和自有cgroup；N=1；周期PSS及终点PSS均有原始记录，样本数量与CPU/间隔回执核对。|
|guest内存/Cached与host current/file|PASS|0.1秒目标周期；guest保留MemTotal、MemAvailable、Cached、Shmem、Buffers；host保留current/file/anon/kernel/shmem/dirty/writeback，基线和各窗口统计匹配原始TSV。|
|指定下层终点mincore|PASS|初始实际清单按device:inode去重，文件数依次6/9/11/5/3；元数据稳定。终点在下载完成后、原检查及删除前；P1前后current/file与覆盖不足标记完整。|
|任务时间、逐工具及累计|PASS|从控制器入口、创建前至原轨迹及结果下载结束；全部工具起止差值和累计一致，预期非零动作数依次4/0/1/1/33均保留；其他时间边界单列，无保存/恢复实验。|
|采集成本与物理量范围|PASS|三类采样器CPU/点数/时长/最大间隔、终点查询墙钟/CPU及采样管理调用齐全；CompCert只读进度查询另计。图和表分别报告各物理量，不相加构造总内存或减速率。|
|原检查及CompCert等待|PASS，含原约定|HF正负样例、PDF.js两轮1127/0/56、token原26行答案79586均满足；Log4j2编译成功、五条class、跳过测试并保留141；CompCert实际32 GiB，九处等待及九个完成标记退出0，原编译运行检查通过。|
|资源清理与失败现场|PASS，已核实细节|guest、私有服务、挂载/loop和自有relay均已清理，失败原始目录/卷保留。事后stop返回5：service已not-found，slice为inactive/dead且ControlGroup为空，无残留进程；不能仅用该返回码判作清理失败。|
|范围、纠正重跑与镜像|符合已授权范围|无统计重复、压力、多调度或新模型轨迹。失败后的纠正运行已获授权并保留旧现场；四项镜像精确匹配primary，Log4j2按文档固定基础镜像/Containerfile/源码提交重建，字节摘要差异明示。|

本次核对时可用磁盘 60.151 GiB；新实跑入口仍要求严格大于58 GiB。自有源服务已按任务要求清理停止。当前新跑条件与已完成结果分别记录，后续运行前仍须重新满足资源及源服务条件。

注意：每项当前通过结果均来自单沙箱完整轨迹；此前失败和用户授权的纠正重跑全部留存，并非历史上每项只启动过一次。Log4j2退出141及固定重建镜像差异按原范围单列，原检查器并非全部退出0。
