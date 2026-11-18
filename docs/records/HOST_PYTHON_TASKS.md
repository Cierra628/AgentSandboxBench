# Sphinx与Xarray完整任务的host采样（2026-10-21）

**两个任务各执行一次完整35步，共70个实际动作。工具时延、guest内存／Cached、host file曲线、PSS及最终文件驻留清单齐全，原任务结果和清理检查保持。** 加上此前Prettier完整任务，三个普通单任务均已有host入口实跑证据；共享恢复的验证范围仍为Prettier。

[任务期间host变化图](evidence/host-python-tasks-window-20261021.png) · [脱敏数据](evidence/host-python-tasks-20261021.json) · [测量含义与验收范围](HOST_MEASUREMENT_ACCEPTANCE.md)

## 本次只接入两个配置，没有修改采样程序

新增`configs/platform-sphinx7590-host-file.json`和`configs/platform-xarray6992-host-file.json`，各自在原普通配置上仅增加`host_file_monitor: true`。保留原冻结输入、参考结果及诊断策略：Sphinx为`sphinx-pipeline-v2`，Xarray为`xarray-approved-sigpipe`。普通配置的默认行为不变。

两组均为AgentENV单guest，普通guest内核、2 CPU／4096 MiB、8 GiB自有卷。guest和host每0.1秒采样，PSS（按共享比例分摊的进程映射内存）每1秒读取。输入准备前建立独立统计组，同时开启内存和I/O记账；新服务和VMM从创建起就在组内，池和预热关闭。采样不暂停沙箱，不预读任务文件、不强制驻留，不改变DAMON设置。

## 原任务结果保持，用时分别报告

| 项目 | Sphinx #7590 | Xarray #6992 |
| --- | ---: | ---: |
| 实际工具动作 | 35 | 35 |
| 工具累计用时 | 11.228946秒 | 33.208810秒 |
| 冻结轨迹回放墙钟 | 14.855366秒 | 36.089973秒 |
| 任务控制程序墙钟 | 26.119915秒 | 67.036290秒 |
| 原官方测试：通过／失败 | 24／1 | 945／12 |
| 原诊断规则检查 | PASS | PASS |
| 严格重放状态 | FAIL | FAIL |

任务控制程序还包括沙箱创建、采样控制、验证和清理，不包含前置输入与私有服务准备。动作、退出码、关键输出、补丁与任务检查通过；官方失败用例身份与原参考逐项一致，严格FAIL未改写。本轮没有关闭host采样对照，不能由上述用时计算这两个任务的host采样减速率；已有Prettier一次开销对照[单列报告](HOST_SAMPLING_CONTROL.md)。

## 三条guest曲线与host读数齐全

| 项目 | Sphinx | Xarray |
| --- | ---: | ---: |
| guest采样点 | 227 | 632 |
| guest内存峰值：MemTotal−MemAvailable | 307.496 MiB | 753.090 MiB |
| guest Cached峰值 | 111.277 MiB | 343.797 MiB |
| 全流程host计数点 | 1271 | 1778 |
| 全流程PSS扫描数 | 127 | 178 |
| guest采样期间host计数点 | 233 | 641 |
| guest采样期间完整单VMM PSS点 | 24 | 64 |
| guest采样期间VMM PSS峰值 | 401.969 MiB | 1155.926 MiB |
| host最大采样间隔 | 0.100145秒 | 0.100123秒 |

Xarray有一次不完整PSS扫描，发生在guest采样结束后；没有用它代替完整读数或补零。Sphinx无不完整扫描。

- Sphinx：[guest工具／内存／Cached图](evidence/host-sphinx7590-guest-20261021.png)、[host全流程图](evidence/host-sphinx7590-host-lifecycle-20261021.png)。
- Xarray：[guest工具／内存／Cached图](evidence/host-xarray6992-guest-20261021.png)、[host全流程图](evidence/host-xarray6992-host-lifecycle-20261021.png)。

## 大部分host file发生在创建guest之前

| 项目 | Sphinx | Xarray |
| --- | ---: | ---: |
| 创建guest前的直接file基线 | 16.988647 GiB | 20.722668 GiB |
| 全流程file观测峰值 | 17.126804 GiB | 20.878662 GiB |
| 创建guest后file最高值相对上述基线的增量 | 141.473 MiB | 159.738 MiB |

绝对file包含输入准备、服务和shmem成本，大部分在第一个guest创建之前已存在，不能把约17／21 GiB解释为任务的page cache。另存的[任务期间放大图](evidence/host-python-tasks-window-20261021.png)使用同一创建前基线，显示guest采样期间的file变化、current／anon上下文及实际单VMM PSS；该窗口含采样启动和最终验证，不只覆盖工具执行。全流程峰值与这个较短窗口的峰值不必相等。

**guest Cached是guest内部文件缓存统计，host file是宿主统计组的文件缓存记账。** 块设备模式下，guest缓存保存在guest RAM中，在host还可能记为VMM匿名内存。PSS只覆盖映射内存，三者不能直接相加或相减计算物理节省。

## 最终普通文件清单没有检出净不足

| 项目 | Sphinx | Xarray |
| --- | ---: | ---: |
| 清单根数／去重文件数 | 9／157 | 9／157 |
| mincore全局驻留 | 17.100025 GiB | 20.851376 GiB |
| 查询前后cgroup file，前后相同 | 17.102577 GiB | 20.854313 GiB |
| 检出的净不足 | 0 | 0 |

清单包含仍存在的普通输入和私有文件、输入RAM镜像及私有loop卷，按设备号和inode去重；文件身份稳定且无查询错误。mincore只查询页驻留状态，不读文件内容。状态均为`NO_SHORTFALL_DETECTED_NOT_PROOF_OF_COMPLETE_ACCOUNTING`：未检出净不足，其他组内缓存仍可能掩盖清单内的组外页，不能据此认定记账完整。

这是普通单任务的最终9根清单，不包含运行中ublk块设备查询，不能扩写成共享恢复观测。此前Prettier共享恢复最终190文件的0.480469 MiB净不足[继续单列](HOST_LIVE_FILE_INVENTORY.md)。本轮没有测得guest Cached对应的host物理页集合或整体物理节省。

## 证据与完成情况

| 任务 | 工作流程 | 原始结果 | 自动报告 |
| --- | --- | --- | --- |
| Sphinx | `.artifacts/platform-tasks/20261020T164427Z-3886208/` | `.artifacts/storage-isolated-service/20261020T164558Z-3887137/` | `.artifacts/platform-tasks/report-20261020T164427Z-3886208/` |
| Xarray | `.artifacts/platform-tasks/20261020T164737Z-3889772/` | `.artifacts/storage-isolated-service/20261020T164916Z-3890897/` | `.artifacts/platform-tasks/report-20261020T164737Z-3889772/` |

配置回归1项及两份离线输入检查通过。采样实现未改动，复用既有实现检查；本次从原始工具表、guest表、host表、PSS和清单独立核对计数、峰值、原失败身份、创建顺序及清理，结果`PASS_TWO_FULL_TASKS`。五张图片均已实际查看。检查脚本、日志和汇总在`.artifacts/host-python-tasks-20261021/`。

两组启动前均已检查超过34 GiB可用磁盘，运行最低分别48.312／48.300 GiB。成功资源已释放，同一源服务重新停止；结束检查56.291 GiB可用，没有删除旧失败卷。两个任务各运行一次，本阶段完成。

## 下一步建议供检查

停止追加校准，直接使用已验证的三任务配置采集正式数据。若研究目标转为“整体物理节省”，另行确定同一比较窗口内的内存范围、重叠处理和参考组，再决定最小实验；不把这一新目标加入本次曲线交付的验收条件。
