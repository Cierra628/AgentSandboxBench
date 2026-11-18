# 普通三任务的轻量host选项（2026-10-28）

**普通三任务均已完成显式`host_current_only`模式的真实完整采集。** [#6604一轮](HOST_CURRENT_PRETTIER6604.md)和[Sphinx／Xarray各一轮](HOST_CURRENT_PYTHON_TASKS.md)取得精确current基线、有效采样与成本，自动报告、最终卷检查及清理全部通过。原官方0／2、24／1、945／12保持，各任务修复结果与采集成功分开记录。

新模式保留guest轻量采样和host current／anon／file／kernel，关闭PSS与最终mincore查询；旧配置不变。以下保留实现和离线验证说明。

## 使用新配置，不修改旧配置

| 任务 | 新配置 | 原完整动作 |
| --- | --- | ---: |
| Prettier #6604 | `configs/platform-prettier6604-host-current.json` | 29 |
| Sphinx #7590 | `configs/platform-sphinx7590-host-current.json` | 35 |
| Xarray #6992 | `configs/platform-xarray6992-host-current.json` | 35 |

每份新配置仅在对应旧`*-host-file.json`上增加`"host_current_only": true`。沿用AgentENV、单沙箱、2 CPU／4096 MiB、无checkpoint和0.1秒采样；没有改任务、默认回收或准备同步策略。

先离线核对本机冻结输入：

```bash
bash scripts/use-platform.sh check \
  configs/platform-prettier6604-host-current.json --offline
```

以后执行完整测量仍使用已有外层入口，保证输入准备前建立统计组；源服务须已健康，入口不自动启动或替换它：

```bash
export ASB_AGENTENV_SOURCE_SERVICE=asb-source-rebuilt-20261018T160925Z-2726362
bash scripts/run-host-file-monitor.sh configs/platform-prettier6604-host-current.json
```

另两个任务只需替换配置名。使用8 GiB自有卷，启动前可用磁盘须严格超过34 GiB，仍须通过原缓存准备、卷内余量及资源检查。三个任务均已完成上述入口的真实完整采集，见本页顶部报告。

原`*-host-file.json`仍按原行为采PSS和最终文件驻留；普通guest配置也不改变。新选项限完整轻量单任务，拒绝恢复、分支调度、精细物理分析及回收干预，不改变旧CSV／Exp1专用入口的条件。

## 实际减少的是两项查询

1. `platform_tasks.run`在新配置下向既有host采样器传入`include_pss=False`，不运行进程映射扫描。
2. 原隔离入口收到`--host-current-only`后不做最终文件驻留清单查询，保存`NOT_REQUESTED`，不是零驻留。

其余路径继续复用：统计组在准备前建立，内存／I/O记账与新服务归属检查保持；创建guest前保存同步current基线；原工具命令、退出码、关键输出、patch、独立和官方结果检查不变。guest采样和host计数频率不改，也没有增加逐页查询、暂停或强制驻留。

最终可写文件清单、只读根检查、固定卷容量及余量检查仍实际位于成功判定之前；自有资源清理与失败卷保留规则不改。跳过mincore不是跳过这些检查。

自动host报告改用同一轮创建记录，无需驻留清单；继续输出current绝对曲线、创建前基线、峰值及增量，以及采样线程CPU、计数读取用时、点数和最大间隔。PSS与mincore标为未请求。数据仍是整个实验组的总记账，不等于纯任务进程或全部host物理占用，定义见[current方案](HOST_CURRENT_PRIMARY.md)。

## 本次验证结果

51项相关检查通过：新增7项，原host计数／报告15项、平台14项、CSV入口10项、Exp1轻量入口5项。另经实际bash离线入口核对三份配置，冻结输入全部`VALIDATED`。

新增检查直接覆盖：三配置仅增加选项、原guest检查命令完全相同、native命令只多该标志、新模式拒绝错误组合、创建统计组及记账参数、PSS关闭、报告不再依赖mincore、成本保存、任务失败后的停止采样与缓存回收。模拟成功与失败都只使用临时目录和替代命令，没有访问真实服务或设备。

最终卷检查和创建前基线代码继续复用，后续#6604一轮已实际通过这些检查，见本页顶部结果。

- [脱敏配置及验证摘录](evidence/host-current-task-option-20261028.json)。
- 本机完整检查和修改前源码：`.artifacts/host-current-option-offline-20261028/`。
- 既有曲线、配置差异及可比较范围：[四任务清单](HOST_CURRENT_TASK_INVENTORY.md)。旧采集结果不追溯改成新模式。

## 资源依赖解除及后续

前两次启动前因共享磁盘空间不足没有实跑；用户授权的两份旧8 GiB失败卷已定向删除，卷外日志保留。随后按新要求清理2.514 GiB可重建构建输出，保留可用二进制、源码和实验数据，见[清理记录](SPACE_CLEANUP_20261028.md)。共享盘恢复后已完成上述#6604一轮，源服务及自有资源回收，原磁盘门槛未降低。

本阶段三任务真实采集已完成，停止追加。随后已[离线接通关闭两端采样的对照入口](HOST_CURRENT_SAMPLING_CONTROL.md)，84项检查及三任务关闭配置核对通过；两组保留同条件统计组、记账和任务检查。随后[关闭→开启首对58动作](HOST_CURRENT_SAMPLING_PAIR.md)已完成，原任务、开关、自动报告、卷检查与清理通过；各耗时窗口分列，冷启动波动单列，不自动追加。
