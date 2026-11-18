# 恢复内存后备层对应关系

## 当前状态：Prettier 第一步已通过

2026-11-15第三次验证为一次BPO：业务84实际／189逻辑动作、两次保存六次恢复及七实例官方0通过／2失败检查通过。六角色配置读取、两个RAM设备、两个内存后备文件的身份及前后稳定性检查通过；离线核对PASS。对应文件驻留分别为572.675781、620.675781 MiB，按共享引用对象单列，不分摊为角色独占量。

证据：`results/prettier-memory-links-validation-20261115T095941Z/REPORT.md`；原始批次`.artifacts/storage-isolated-service/20261115T100108Z-3434646/`。该次成功卷已按入口规则自动移除。前两次FAIL与失败卷均保留。原扩展清单PARTIAL、loop UNKNOWN、专属父组UNAVAILABLE仍分列；本次只通过后备层对应关系，不代表完整page cache或逐沙箱物理分摊。

## 使用与字段

要知道恢复沙箱引用哪些内存快照文件，显式开启 Prettier 采样选项 `--memory-backing-links`（入口 `scripts/run_schedule_smoke.py`），同时开启 `--expanded-files --cache-layers`。普通入口默认关闭。

新结果位于 `schedule-smoke-metrics.json:memory_backing_links`，包括：

- `devices`：实际 RAM rdev、引用角色、恢复模板、卷内服务日志的配置对应证据。
- `devices[].configs`：各角色视角的配置内容、读取前后身份、全部 lower 原引用和稳定文件身份。
- `files`：按设备号／inode 合并的后备文件、角色／设备引用、独立 mincore 查询与状态。

身份缺失、配置冲突、文件或映射变化保留 UNKNOWN／INCOMPLETE；显式开启后本项不完整会使采集 FAIL。原 PSS、P1 和各缓存层不被改写。新增查询窗口与旧窗口分开，不将容量相加；引用关系不等于独占物理使用。

实现与首次验证记录：`results/prettier-memory-links-validation-20261115/REPORT.md`。

## 历史失败

2026-11-15首次：BPO业务84/189动作和七份官方0通过/2失败检查通过，新增采集因错误使用禁用的Docker日志接口失败。已改读本组卷内日志；首次FAIL与32 GiB卷保留。

同日用户确认后再验证一次：卷内日志读取成功，已保存两份真实内存配置，但检查器把路径均为空的upper字典误判为可写层，整体仍FAIL。已离线修复并增加回归，新模块7项与schedule_smoke_metrics 9项通过；未再启动业务。真实配置与既有文件记录离线匹配出六个恢复角色、两个设备、两个后备文件，缺配置后置身份与全部角色读取，因此完整在线验收仍未完成。报告与复算为 `results/prettier-memory-links-retry-20261115T093749Z/REPORT.md`。两个失败卷均保留，未自动重试。
