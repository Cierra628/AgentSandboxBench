# 三任务持久化输入入口：离线实现完成

## 用原轻量配置，加一个显式选项

Prettier #6604、Sphinx #7590、Xarray #6992的完整单任务现已接通`--persistent-input`：把原RAM整卷输入替换为本轮新建的普通文件，保留guest和host每0.1秒采样、`memory.current`主指标、默认回收、原动作和评测规则。

本阶段完成了代码、玩具文件与模拟流程检查。**Prettier已完成一轮[真实完整验证](HOST_PERSISTENT_PRETTIER_VALIDATION.md)，Sphinx也已完成[完整35步](HOST_PERSISTENT_SPHINX_VALIDATION.md)，Xarray于2026-10-30完成[完整35步](HOST_PERSISTENT_XARRAY_VALIDATION.md)**；已有Prettier双沙箱短测继续按[原报告](HOST_PERSISTENT_INPUT_VALIDATION.md)引用，不当作这次完整任务的实跑结果。

以下命令会真实恢复输入并启动沙箱，不是离线检查。离线实现阶段未执行，后续三任务各一轮真实结果见上述报告；再次运行前仍须确认固定源服务健康、磁盘和内存条件满足：

```bash
export ASB_AGENTENV_SOURCE_SERVICE=asb-source-rebuilt-20261018T160925Z-2726362
bash scripts/run-host-file-monitor.sh \
  configs/platform-prettier6604-host-current.json --persistent-input
```

另外两项只替换配置名为`platform-sphinx7590-host-current.json`或`platform-xarray6992-host-current.json`。不需要改旧配置；未加新选项仍使用原RAM输入。旧双沙箱`--current-pair --persistent-input`调用方式保留。

## 输入来源、容量和保护分任务固定

[离线清单](HOST_PERSISTENT_THREE_TASKS_OFFLINE.md)已核对三个固定来源，六类输入全部保留，不凭文件名裁剪：

| 任务 | 原完整动作 | 来源镜像容量 | 普通输入预算 | 准备前可用磁盘 |
|---|---:|---:|---:|---:|
| Prettier #6604 | 29 | 20 GiB | 8 GiB | 严格>54 GiB |
| Sphinx #7590 | 35 | 20 GiB | 10 GiB | 严格>56 GiB |
| Xarray #6992 | 35 | 24 GiB | 12 GiB | 严格>62 GiB |

准备前门槛覆盖完整预留的临时来源、普通输入、20 GiB host保留量及6 GiB余量。临时来源先卸载、分离loop并删除，才分配原8 GiB运行卷；此时当前可用仍须严格>34 GiB。沿用MemAvailable>64 GiB、原卷内余量、动态磁盘保护、写盘锁和有限超时。三份来源格式按原完整性协议处理；Python任务另外核对对应镜像身份，不能用一份种子冒充另一任务。

完整性校验会读取文件并使缓存变热，本模式不称为冷缓存。失败保留临时来源／部分输入及日志，不自动删除失败卷或复用旧目标inode（文件在文件系统中的身份编号）。

## 实际接入顺序

```text
组外恢复该任务固定备份，完整预留临时磁盘镜像
→ 建立新统计组，启用内存及I/O记账
→ 组内进程新建并填充普通输入，核对内容、记录新文件身份并逐文件／目录fsync
→ 正常卸载来源、分离loop、删除成功临时镜像
→ 原8 GiB运行卷及输入准备，只读绑定新commits；同步该自有运行卷
→ 新私有服务从创建起进入组内；服务就绪后保存无guest同步基线
→ 原完整任务、原评测与检查、原自有资源清理
→ 普通输入仍保留时，执行一次输入驻留核对
→ 生成报告；报告与核对通过后清理普通输入，确认释放标记和目录消失
```

`host_persistent_input.py`复用同一恢复、导入、验证及清理实现。`run_host_file_workflow.py`和`use_platform.py`只向组内`platform_tasks.run`转交内部种子路径；原任务的检查命令完全相同，隔离运行命令只替换种子选项，不把GRPO专用参数传给普通任务。`validate_isolated_agentenv.py`按实际任务核对种子，新增同步仅作用于新完整单任务，旧同步条件不变。

原RAM、host-file、关闭采样、递增和双沙箱入口保持原行为；新模式首版拒绝混入关闭采样、回收干预、分支调度、checkpoint或其他额外分析。Prettier `strict`、Sphinx `sphinx-pipeline-v2`、Xarray `xarray-approved-sigpipe`保持，原官方失败和诊断状态不改。

## 报告分开显示组内总量、准备基线和结束后输入驻留

- host周期曲线仍是统计组`memory.current`及anon、file、kernel、shmem分项，覆盖组内准备、控制程序、服务和沙箱。无guest基线包含已准备输入与服务；峰值增量包含后续服务、检查和清理，不是纯任务进程内存。
- 组外来源恢复收据放在`source_preparation`，组内导入身份和时间放在`input_import`；组内`workflow_elapsed_ns`包括预检查、导入、运行、查询、清理与报告，不含组外恢复。
- 输入驻留使用一次mincore，即只查询现有文件缓存页是否驻留、不读取数据页。查询发生在隔离运行及自有清理结束后，标为`AFTER_ISOLATED_RUNTIME_CLEANUP`，**不是任务运行中驻留点**。
- 按设备号＋inode去重，保存`path_count`和`unique_file_count`，核对前后身份、驻留合计和前后file；另存查询起止及`thread_cpu_ns`。查询时间不计入工具、回放或任务控制程序时间，但包括在外层流程中。
- 查询组身份、导入／同步／创建／查询的顺序均需相符。报告把`input_file_check`、查询阶段与成本放在独立的`persistent_input`字段；原全文件收尾扫描仍为NOT_REQUESTED，不能据此误认新模式没有做这一次查询。

没有PSS、周期mincore、BPF、暂停或强制驻留。`ownership=NOT_QUERIED`保持；驻留没有超过file仅是容量交叉核对，不代表已逐页证明归属，也不覆盖整个host。新旧准备方式不同，不从绝对current之差计算物理节省。

## 正常和失败路径已离线检查

输入查询失败或出现驻留超过file的差额时，新外层检查失败；原任务结果不改，输入保留。采样、报告或输入清理失败也不会记成功；查询与采样同时失败时保留首先失败的阶段；清理子进程返回后仍核对释放标记和目录确已消失。只关闭本轮对应资源，不替换原服务。

有效相关检查共86项：

| 检查 | 项数 | 本次证据 |
|---|---:|---|
| 新三任务接入 | 13 | 恢复后通过；三任务命令／策略、预算、来源格式、Python镜像身份、新文件、去重、顺序、成本、报告及8类失败（含查询和采样同时失败） |
| 原持久化输入 | 15 | 恢复后通过；原Prettier调用、来源释放、参数和正常／失败清理 |
| 普通current任务 | 7 | 复用暂停前有效证据，旧入口对应路径未变 |
| 关闭采样 | 5 | 同上 |
| 双沙箱 | 7 | 同上 |
| 原递增 | 12 | 同上 |
| 隔离存储 | 27 | 同上 |

恢复后补查28项，其余58项复用有效结果，不重复运行全部测试。新增测试确认7个路径对应6个唯一文件时只查6次；错误组身份被拒绝；清理返回但释放标记为false时仍失败。Python语法及相关diff格式检查通过。测试只操作临时玩具文件及模拟设备／服务／统计组／mincore，没有恢复真实归档、执行真实任务或取得新性能数值；日志里的模拟FAIL用于检查失败路径。

旧current测试有一处过时字符串定位，已改为实际的`host_current_only or current_pair`分支，未改变运行逻辑。暂停记录和当时代码保留；最终候选及日志在`.artifacts/host-persistent-three-tasks-implementation-20261029/tested-code/`及同级目录。[脱敏验证结果](evidence/host-persistent-three-tasks-implementation-20261029.json)列出各项日志和范围。

## 后续：三任务均各完成一轮真实完整验证

预定的一轮Prettier29步已[真实完成](HOST_PERSISTENT_PRETTIER_VALIDATION.md)：一个新guest、原动作和评测、0.1秒两端采样、运行结束后一次输入驻留核对，以及实际来源、同步基线、报告、最终卷检查和清理均通过。一轮结束后停止，没有附加重复、关闭采样或多沙箱实验。真实数据与成本见该报告，不套用离线玩具数据或旧双沙箱短测数值。

Sphinx随后也已[完成一轮35步](HOST_PERSISTENT_SPHINX_VALIDATION.md)，诊断PASS、严格FAIL及官方24／1保持，测量和清理通过。空间清理后，Xarray于2026-10-30也[完成一轮35步](HOST_PERSISTENT_XARRAY_VALIDATION.md)：原诊断PASS、官方945／12保持，采样、116个输入驻留核对、报告、边界与清理通过。三项当前均有真实单任务结果，各一轮；没有以此计算并发共享率或任务减速率。下一步建议先离线核对普通输入的多沙箱接入，不自动追加实跑。
