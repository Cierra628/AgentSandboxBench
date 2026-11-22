# 分支归并记录 · 2026-11-22

**38 个归档标签已在 GitHub 核实。38/38 段已合入 integration/20261122，逐段离线检查全部通过。main 未变，未删除分支或代码。**

起点：本地与远端 main 同为 `dd9ba4c9ebae59db68055da5e8ad566b87d6eb16`。两份磁盘记录先以 `de8d882` 提交到原工作分支。

## 本地非 main 分支（按 tip 提交时间，北京时间）

“领先”按 main..分支计；“仅此支”排除其他所有本地分支可达的提交。包含关系用本表序号（不含自身）；不以内容相似代替祖先关系。主要文件示例取自 tip 提交。归档标签名为 `archive/<去掉第一个斜杠前缀的分支名>-20261122`。

|序号|分支|tip|领先 / 仅此支|包含|被包含于|最近时间|主要文件（节选）|归类|
|---:|---|---|---:|---|---|---|---|---|
|1|analysis/seven-reader-cost-20261119|`64113b0`|1 / 1|—|—|2026-11-19 11:43|`docs/records/seven-reader-cost-20261119/REPORT.md`<br>`docs/records/seven-reader-cost-20261119/stages.csv`|独立分析|
|2|analysis/seven-query-granularity-20261119|`24905e9`|1 / 1|—|—|2026-11-19 11:52|`docs/records/seven-query-granularity-20261119/REPORT.md`|独立分析|
|3|analysis/seven-query-cost-model-20261119|`0e6ffb2`|1 / 1|—|—|2026-11-19 12:09|`docs/records/seven-query-cost-model-20261119/REPORT.md`|独立分析|
|4|analysis/segment-sampling-error-20261119|`3152244`|2 / 2|—|—|2026-11-19 12:22|`docs/records/segment-sampling-error-20261119/REPORT.md`|独立分析|
|5|study/paused-host-cache-20261119|`5748816`|1 / 1|—|—|2026-11-19 13:24|`docs/records/paused-host-cache-20261119/REPORT.md`<br>`docs/records/paused-host-cache-20261119/compare.py`|有独有内容|
|6|study/paused-four-source-timing-20261119|`fa8d1c8`|1 / 1|—|—|2026-11-19 13:37|`docs/records/paused-four-source-timing-20261119/REPORT.md`<br>`scripts/observe_paused_four_sources.sh`|有独有内容|
|7|study/immediate-pause-all-layers-20261119|`df870d4`|1 / 1|—|—|2026-11-19 13:46|`docs/records/immediate-pause-all-layers-20261119/REPORT.md`<br>`scripts/observe_immediate_image_layers.sh`|有独有内容|
|8|study/paused-vm-config-drives-20261119|`667073a`|1 / 1|—|—|2026-11-19 13:56|`docs/records/paused-vm-config-drives-20261119/REPORT.md`<br>`scripts/observe_paused_vm_drives.sh`|有独有内容|
|9|study/paused-native-collector-20261119|`b09f601`|1 / 1|—|—|2026-11-19 14:02|`docs/records/paused-native-collector-20261119/REPORT.md`<br>`scripts/observe_paused_native.sh`|有独有内容|
|10|study/restore-export-ready-20261119|`a0e24ca`|1 / 1|—|—|2026-11-19 14:44|`docs/records/restore-export-ready-20261119/REPORT.md`<br>`scripts/observe_restore_export_ready.sh`|有独有内容|
|11|study/fast-export-pause-20261119|`f2c5ae6`|1 / 1|—|—|2026-11-19 15:07|`docs/records/fast-export-pause-20261119/REPORT.md`<br>`scripts/observe_fast_export_pause.sh`|有独有内容|
|12|analysis/paused-union-reference|`5eab5a9`|1 / 1|—|—|2026-11-19 15:20|`docs/records/paused-union-reference-20261119/REPORT.md`|独立分析|
|13|study/seven-paused-export|`daeff18`|1 / 1|—|—|2026-11-19 15:32|`docs/records/seven-paused-export-20261119/REPORT.md`<br>`scripts/observe_seven_paused_export.sh`|有独有内容|
|14|analysis/paused-single-pass|`ccec65e`|1 / 1|—|—|2026-11-19 15:40|`docs/records/paused-single-pass-20261119/REPORT.md`<br>`docs/records/paused-single-pass-20261119/replay.py`|独立分析|
|15|study/seven-paused-serial|`85915ca`|1 / 1|—|—|2026-11-19 15:57|`docs/records/seven-paused-serial-20261119/REPORT.md`<br>`scripts/observe_seven_paused_serial.sh`|有独有内容|
|16|docs/seven-peak-method-20261119|`30457f6`|1 / 1|—|—|2026-11-19 16:28|`docs/records/seven-peak-method-20261119/REPORT.md`|有独有内容|
|17|study/seven-query-parallelism|`df2fb1e`|1 / 1|—|—|2026-11-20 16:13|`docs/records/seven-query-parallelism-20261120/REPORT.md`<br>`docs/records/seven-query-parallelism-20261120/bench.py`|有独有内容|
|18|study/seven-real-process|`bdaf849`|1 / 0|—|20, 22, 23, 32, 33, 34, 35, 36|2026-11-20 17:03|`docs/records/seven-real-process-20261120/REPORT.md`<br>`scripts/observe_paused_process.sh`|主干链阶段（后续已包含）|
|19|study/provider-first-frame|`455f5f9`|1 / 1|—|—|2026-11-20 17:12|`docs/records/provider-first-frame-20261120/REPORT.md`<br>`docs/records/provider-first-frame-20261120/replay.py`|有独有内容|
|20|study/seven-process-round|`37531ae`|2 / 0|18|22, 23, 32, 33, 34, 35, 36|2026-11-20 17:18|`docs/records/seven-process-round-20261120/REPORT.md`<br>`docs/records/seven-process-round-20261120/summarize.py`|主干链阶段（后续已包含）|
|21|study/batch-union-cost|`6df88ce`|1 / 1|—|—|2026-11-20 17:23|`docs/records/batch-union-cost-20261120/REPORT.md`<br>`docs/records/batch-union-cost-20261120/replay.py`|有独有内容|
|22|study/uint64-live-union|`8948bff`|3 / 0|18, 20|23, 32, 33, 34, 35, 36|2026-11-20 17:29|`docs/records/uint64-live-union-20261120/REPORT.md`<br>`docs/records/uint64-live-union-20261120/check_merge.py`|主干链阶段（后续已包含）|
|23|study/seven-process-five|`ea6f7eb`|4 / 0|18, 20, 22|32, 33, 34, 35, 36|2026-11-21 11:50|`docs/records/seven-process-five-20261121/REPORT.md`<br>`docs/records/seven-process-five-20261121/summarize.py`|主干链阶段（后续已包含）|
|24|study/fixed-snapshot-device-identity|`4bf984a`|1 / 1|—|—|2026-11-21 12:15|`docs/records/fixed-snapshot-device-20261121/REPORT.md`<br>`docs/records/fixed-snapshot-device-20261121/branches.csv`|有独有内容|
|25|fix/provider-slot-lifetime|`6bca996`|1 / 0|—|26, 31, 32, 33, 34, 35, 36|2026-11-21 12:21|`docs/records/provider-slot-lifetime-20261121/REPORT.md`<br>`scripts/provider_slot_cache.py`|主干链阶段（后续已包含）|
|26|fix/provider-slot-test-constructors|`515c875`|2 / 0|25|31, 32, 33, 34, 35, 36|2026-11-21 12:23|`docs/records/provider-slot-test-constructors-20261121/REPORT.md`<br>`docs/records/provider-slot-test-constructors-20261121/rust-tests.txt`|主干链阶段（后续已包含）|
|27|study/volume-retention-034046|`95f3715`|1 / 1|—|—|2026-11-21 13:20|`docs/records/five-run-volume-retention-20261121/REPORT.md`|有独有内容|
|28|study/archive-five-run-memory-layer|`8669453`|1 / 1|—|—|2026-11-21 13:26|`docs/records/five-run-memory-archive-20261121/REPORT.md`|有独有内容|
|29|study/rootfs-rebuild-audit|`2bcc2d0`|1 / 1|—|—|2026-11-21 13:35|`docs/records/rootfs-rebuild-audit-20261121/REPORT.md`<br>`docs/records/rootfs-rebuild-audit-20261121/layers.csv`|有独有内容|
|30|study/archive-rootfs-final|`bf561dd`|1 / 1|—|—|2026-11-21 13:40|`docs/records/five-run-rootfs-archive-20261121/REPORT.md`|有独有内容|
|31|study/provider-live-three|`f427934`|3 / 0|25, 26|32, 33, 34, 35, 36|2026-11-21 13:51|`docs/records/provider-slot-live-three-20261121/REPORT.md`<br>`scripts/observe_provider_slot_lifetime.sh`|主干链阶段（后续已包含）|
|32|study/repaired-five-speed|`cad1058`|9 / 0|18, 20, 22, 23, 25, 26, 31|33, 34, 35, 36|2026-11-21 14:01|`docs/records/repaired-five-speed-20261121/REPORT.md`<br>`scripts/observe_paused_process.sh`|主干链阶段（后续已包含）|
|33|study/margin-sensitivity-20261121|`483f0e7`|10 / 0|18, 20, 22, 23, 25, 26, 31, 32|34, 35, 36|2026-11-21 15:33|`docs/records/margin-sensitivity-20261121/REPORT.md`<br>`docs/records/margin-sensitivity-20261121/analyze.py`|主干链阶段（后续已包含）|
|34|study/first-frame-opt-20261121|`3da8bd6`|11 / 0|18, 20, 22, 23, 25, 26, 31, 32, 33|35, 36|2026-11-21 15:47|`docs/records/first-frame-opt-20261121/REPORT.md`<br>`docs/records/first-frame-opt-20261121/equivalence.py`|主干链阶段（后续已包含）|
|35|study/page-count-divergence-20261121|`9ff075b`|12 / 0|18, 20, 22, 23, 25, 26, 31, 32, 33, 34|36|2026-11-21 15:53|`docs/records/page-count-divergence-20261121/REPORT.md`<br>`docs/records/page-count-divergence-20261121/first-exports.csv`|主干链阶段（后续已包含）|
|36|study/working-set-variance-20261121|`f91d3a5`|16 / 4|18, 20, 22, 23, 25, 26, 31, 32, 33, 34, 35|—|2026-11-21 19:48|`docs/records/seven-peak-report-20261121/REPORT.md`|有独有内容|
|37|docs/seven-peak-brief|`ffcb614`|2 / 0|—|38|2026-11-21 23:09|`docs/records/grpo-per-action-20261121/REPORT.md`<br>`docs/records/grpo-per-action-20261121/fixed/pss-gap-comparison.csv`|已被后续分支包含|
|38|study/grpo-per-action-20261121|`de8d882`|6 / 4|37|—|2026-11-22 15:31|`docs/records/disk-audit-20261121/REPORT.md`<br>`docs/records/disk-cleanup-20261121/REPORT.md`|有独有内容|

## GitHub 分支（合并前）

|分支|tip|本地有同名分支|被哪些本地分支包含|处理|
|---|---|---|---|---|
|analysis/seven-sandbox-sampling|`f97cb1da91678e65fbf125c6f18d028cba2facab`|否|—|仅远端有，保留，不合并、不删|
|codex/trenv-task-cgroup|`b83386670618980fc925750f4c1f6289229e691c`|否|main、本地表全部 38 支|仅远端有，保留，不合并、不删|
|feat/agentenv-five-workloads|`d0987e6f0d1f326989bb13a5691cbe9d0cbf810c`|否|—|仅远端有，保留，不合并、不删|
|feature/trenvx-file-physical-memory|`7cb9923058114caddf344eafdedf0e624cec7197`|否|main、本地表全部 38 支|仅远端有，保留，不合并、不删|
|main|`dd9ba4c9ebae59db68055da5e8ad566b87d6eb16`|是|main、本地表全部 38 支|主线|

## 上一轮：备份完成，首次推送未获认证

- 本地完整备份：`/data/asb-backup/repo-20261122.bundle`，23,223,171 字节。提交磁盘记录后、打标签前执行 `git bundle create … --all`；`git bundle verify` 退出 0，确认是完整历史。该文件备份的是本地引用，不代替远端独有分支的备份。
- 已在本地创建 38 个归档标签，各指向上表对应 tip。通过本轮授权的 `127.0.0.1:17891` 可以读取远端引用，但 `git push origin --tags` 退出 128：`fatal: could not read Username for 'https://github.com': terminal prompts disabled`。当前非交互 Git 会话未取得 HTTPS 写入凭据。
- 失败后只读复核：GitHub 仍只有 `handoff-20261118` 一个标签，38 个归档标签均未上传。未创建 integration 分支，未执行任何合并或分段离线检查；本地、远端删除清单均为空，代码清理候选未盘点。
- 停止时：本地 39 个分支、39 个标签；远端仍是上表的 5 个分支，4 个仅远端存在的分支原样保留。两端 main 均为 `dd9ba4c9ebae59db68055da5e8ad566b87d6eb16`。

## 归档推送与逐段合并

凭据自检：GH_TOKEN 非空，本仓库 credential.helper 已配置为读取该变量。`git push --dry-run origin main` 退出 0（Everything up-to-date）。随后 `git push origin --tags` 退出 0；用 `git ls-remote --tags origin` 逐名、逐对象核对，38 个 archive 标签全部与本地一致。加上既有 handoff 标签，两端各 39 个标签。没有重建备份或标签。

从 main 创建 `integration/20261122`，按盘点表序号用 `--no-ff` 合并。每次合并后执行 `bash scripts/check-platform-offline.sh`：

|序号|分支|合并提交|离线检查（退出码；末行）|
|---:|---|---|---|
|1|analysis/seven-reader-cost-20261119|`58a1ad1`|0；`status=PASS scope=platform-offline`|
|2|analysis/seven-query-granularity-20261119|`97ad9e4`|0；`status=PASS scope=platform-offline`|
|3|analysis/seven-query-cost-model-20261119|`62e5c1a`|0；`status=PASS scope=platform-offline`|
|4|analysis/segment-sampling-error-20261119|`dfe3d88`|0；`status=PASS scope=platform-offline`|
|5|study/paused-host-cache-20261119|`bb358fd`|0；`status=PASS scope=platform-offline`|
|6|study/paused-four-source-timing-20261119|`f504de3`|0；`status=PASS scope=platform-offline`|
|7|study/immediate-pause-all-layers-20261119|`77a01a1`|0；`status=PASS scope=platform-offline`|
|8|study/paused-vm-config-drives-20261119|`49f603c`|0；`status=PASS scope=platform-offline`|
|9|study/paused-native-collector-20261119|`5829cf9`|0；`status=PASS scope=platform-offline`|
|10|study/restore-export-ready-20261119|`24b2e43`|0；`status=PASS scope=platform-offline`|
|11|study/fast-export-pause-20261119|`36d9acf`|0；`status=PASS scope=platform-offline`|
|12|analysis/paused-union-reference|`c43190a`|0；`status=PASS scope=platform-offline`|
|13|study/seven-paused-export|`594e71a`|0；`status=PASS scope=platform-offline`|
|14|analysis/paused-single-pass|`11cc6fd`|0；`status=PASS scope=platform-offline`|
|15|study/seven-paused-serial|`5fc4259`|0；`status=PASS scope=platform-offline`|
|16|docs/seven-peak-method-20261119|`81b8345`|0；`status=PASS scope=platform-offline`|
|17|study/seven-query-parallelism|`400dcfc`|0；`status=PASS scope=platform-offline`|
|18|study/seven-real-process|`015bbfa`|0；`status=PASS scope=platform-offline`|
|19|study/provider-first-frame|`6704ed5`|0；`status=PASS scope=platform-offline`|
|20|study/seven-process-round|`5ff1fb3`|0；`status=PASS scope=platform-offline`|
|21|study/batch-union-cost|`df69791`|0；`status=PASS scope=platform-offline`|
|22|study/uint64-live-union|`a7168d8`|0；`status=PASS scope=platform-offline`|
|23|study/seven-process-five|`cbb9653`|0；`status=PASS scope=platform-offline`|
|24|study/fixed-snapshot-device-identity|`373e79c`|0；`status=PASS scope=platform-offline`|
|25|fix/provider-slot-lifetime|`be409f7`|0；`status=PASS scope=platform-offline`|
|26|fix/provider-slot-test-constructors|`06b4414`|0；`status=PASS scope=platform-offline`|
|27|study/volume-retention-034046|`01d48ae`|0；`status=PASS scope=platform-offline`|
|28|study/archive-five-run-memory-layer|`bc597be`|0；`status=PASS scope=platform-offline`|
|29|study/rootfs-rebuild-audit|`0faf68b`|0；`status=PASS scope=platform-offline`|
|30|study/archive-rootfs-final|`2289962`|0；`status=PASS scope=platform-offline`|
|31|study/provider-live-three|`6c119dd`|0；`status=PASS scope=platform-offline`|
|32|study/repaired-five-speed|`b7886fa`|0；`status=PASS scope=platform-offline`|
|33|study/margin-sensitivity-20261121|`dc18c9c`|0；`status=PASS scope=platform-offline`|
|34|study/first-frame-opt-20261121|`bb708c1`|0；`status=PASS scope=platform-offline`|
|35|study/page-count-divergence-20261121|`6fdf527`|0；`status=PASS scope=platform-offline`|
|36|study/working-set-variance-20261121|`c298993`|0；`status=PASS scope=platform-offline`|
|37|docs/seven-peak-brief|`6d9609c`|0；`status=PASS scope=platform-offline`|
|38|study/grpo-per-action-20261121|本报告所在的合并提交（双亲 `6d9609c`、`de8d882`）|0；`status=PASS scope=platform-offline`|

### 第 6 段：两个暂停入口均保留

只改了 `scripts/validate_isolated_agentenv.py` 的四处冲突：

1. 读取两个开关，共用原有前置条件：启用缓存会话、启用恢复下层、提供私有 provider、子实例数为 2，且未启用 unified rounds。两个开关同时给出时先抛出 ValueError：`--paused-host-cache and --paused-four-sources are independent workflows; select only one`。
2. 缓存会话按下表选择执行脚本。
3. 每个入口保留原验收字段，未选入口时仍写原字段。
4. 命令行保留两个独立的 add_argument 及各自原 help 文本。

|缓存会话选择|执行脚本|写入的验收字段|
|---|---|---|
|仅 --paused-host-cache|run_paused_host_cache.py|paused_host_layer_observation|
|仅 --paused-four-sources|run_paused_four_sources.py|paused_host_four_source_observation|
|都不开|run_rollout_cache_calibration.py|guest_cache_metadata_without_pause|
|同时开启|参数错误，不执行|不写入|

第 6 段解决后无冲突标记。按顺序验证：py_compile 通过；两个 runner 均存在；`python3 tests/test_paused_entry_modes.py` 四种组合 4/4 通过（执行源码中的参数声明、校验和选择语句，无需 root 或沙箱）；平台离线检查退出 0。随后提交为双亲合并 `f504de3`（父提交 `bb358fd`、`fa8d1c8`）。

### 第 7 段：开关、执行脚本和验收字段集中到一张表

`scripts/validate_isolated_agentenv.py` 的 `PAUSED_ENTRY_MODES` 以命令行参数的属性名为键，每行保存一对值：执行脚本、成功时写入的验收字段。`paused_entry` 在入口处选择一次；调度和回执使用同一对值。三个命令行开关和各自的 help 文本均保持原样。

|开关|执行脚本|验收字段|
|---|---|---|
|--paused-host-cache|run_paused_host_cache.py|paused_host_layer_observation|
|--paused-four-sources|run_paused_four_sources.py|paused_host_four_source_observation|
|--immediate-image-layers|run_immediate_image_layers.py|fixed_image_layer_observation|
|均未开启（缓存会话）|run_rollout_cache_calibration.py|guest_cache_metadata_without_pause|

单选时必须同时满足：缓存会话、恢复下层和私有 provider 均已启用，子实例数为 2，且未启用 unified rounds。多选抛出 `ValueError: these are independent workflows; select only one`。未选不引入这些附加条件。

第 7 段依次通过：py_compile、三个脚本存在性检查、`tests/test_paused_entry_modes.py`（4 个测试方法，覆盖全部 8 种开关组合，并逐项检查 5 个前置条件）、平台离线检查（退出 0，末行见上表）。双亲合并提交为 `77a01a1`。

**后续同类冲突解决规则：** 前置条件相同、仅增加一个独立流程时，保留集成侧的表结构，补一行新开关、原脚本名、原验收字段，并保留原 argparse 声明及 help；不退回 if 链，不整文件取一边。第 8–11 段据此分别加入 paused_vm_drives、paused_native、restore_export_ready、fast_export_pause；第 12 段无冲突。各段单独通过平台离线检查。

### 第 13 段：两子实例与七支恢复分别选择入口

两子实例沿用 `PAUSED_ENTRY_MODES` 和原有前置条件。七支单设 `SEVEN_PAUSED_ENTRY_MODES`：`--seven-paused-export` 对应 `run_seven_paused_export.py`，成功字段为 `seven_paused_export`。它要求同时指定 `--immediate-restores` 与 `--seven-restored`，缺任一项仍报 `paused export requires parallel seven restores`。七支恢复原有的会话、恢复下层、provider、GET_DATA、统一轮次和 7 个子实例检查不变。两个恢复参数各只声明一次。

两类专用开关同时开启时，优先报 `two-instance and seven-restore workflows are separate; select only one class`。未开启专用开关时，普通缓存会话和七支恢复均回落到 `run_rollout_cache_calibration.py`，写入 `guest_cache_metadata_without_pause`。

第 13 段按顺序通过编译、七支 runner 文件存在性检查、入口回归（8 个测试方法全过）及平台离线检查（退出 0，末行见上表）。回归保留原两子实例组合，并覆盖七支导出、七支默认回落、缺少任一或全部恢复标志、两类混开。双亲合并提交为 `594e71a`。

第 15 段的串行入口前置条件与七支导出相同，只在七支表补一行：`--seven-paused-serial → run_seven_paused_serial.py → seven_paused_serial`。新增串行单选及与并行导出互斥断言，9 个入口测试方法全过。第 14、16、17 段无冲突；第 13–17 段均已分别通过离线检查。

### 第 18 段：保留原串行入口，另接进程版

同名脚本 `run_seven_paused_serial.py` 采用待合入侧版本。核对源码：直接执行仍调用 `main()`，默认 `process_mode=False`；原路径仍调用 `prime_tracers`、使用 `Reader` 并前后读取 `lower_bitmap`。只有新入口 `run_paused_process.py` 显式调用 `main(process_mode=True)` 并使用 `paused_process_workers.py` 的 Workers/Publication。三份脚本均在 scripts/ 下。无需注册到控制器目录：该调用直接由宿主 Python 执行 `ROOT/'scripts'/runner`，上述两个导入均从脚本所在目录解析。

入口脚本按集成侧映射分派，不照搬旧 if 链。七支立即恢复类三项如下，任意两项或与两子实例入口混开均报错；不选专用入口仍走原校准路径。

|开关|执行脚本|成功字段|
|---|---|---|
|--seven-paused-export|run_seven_paused_export.py|seven_paused_export|
|--seven-paused-serial|run_seven_paused_serial.py|seven_paused_serial|
|--paused-query-processes 1 或 7|run_paused_process.py|paused_query_processes|

进程版缺少七支恢复或立即恢复时沿用原错误 `process benchmark requires seven immediate restores`。只有进程模式才向 runner 传 `--workers 1` 或 `--workers 7`；上层不另加 --workers 开关。

第 18 段按顺序通过：四个脚本 py_compile；入口回归 12 个测试方法全过（含原两实例组合、七支三项单选和两两互斥、跨类互斥、缺前置报错及 runner 实际调用表达式的 workers 参数）；平台离线检查退出 0，末行见上表。双亲合并提交为 `015bbfa`。以上为离线验证，未跑真实设备。

### 第 19–37 段

第 31 段另加七支槽位生命周期类：`provider_slot_lifetime → verify_provider_slot_lifetime.py → three_paused_slot_lifetimes`。沿用七支恢复条件，不新增立即恢复要求；和其他专用流程互斥。第 32 段复用已合入的进程及槽位映射，保留 workers 透传。两段的入口回归均为 13 个测试方法通过。第 19–37 段均逐段通过平台离线检查。

### 第 38 段：合并双路线汇报

以 `de8d882` 的双路线完整版为底稿。“一秒从哪里算”补入恢复请求发出时刻未单列，查询时长不能代表中断时长；恢复场景末尾补入缓存增长是否饱和仍未知。“待学长决定”的一秒指标行补入一次垃圾回收实测 131–186 ms（`3da8bd6`），并将三个旧任务的新口径结果与第二个对照基线并列为未验证项。

核对结果：恢复表与底稿逐字一致；五批范围与 `cad1058`、三次独立快照逐行与 `c05266e` 一致，旧法一行与 `30457f6` 一致。原有数字均未改变，重复提及的百分比仍区分精确值与约数。图的相对路径解析到合并树中的 PNG。冷启动两节、四行待决定（含缓存粒度）、两处分页标记、取代旧简版的说明及“两条路线的页数和查询耗时都不可互比”均保留。

**集成完成：38/38 段。** 第 38 段离线检查退出 0，末行 `status=PASS scope=platform-offline`。只更新集成分支；本地 main 仍为 `dd9ba4c9ebae59db68055da5e8ad566b87d6eb16`，本段未推送。

**删除和清理：** 本地、远端分支删除清单均为空，未执行删除命令。4 个仅远端存在的分支均保留。未进入代码引用盘点，清理候选数量未知；未删除代码。
