# 同一统计组内按1、2、4、7个沙箱递增（2026-10-22）

**最新结果：**用户已改为只研究共享文件缓存。当前新模式取消PSS、在N=1／7分别查询驻留，已完成[五轮冷启动短实验](HOST_SHARED_FILE_REPEATS.md)、[一次关闭周期采样对照](HOST_SHARED_SAMPLING_CONTROL.md)及[一轮恢复短验证](HOST_SHARED_RESTORE.md)。恢复查询已发现独立盘扫描的非必要成本，公共导入原始层只列驻留、不乘七。下文保留原离线实现记录，anon／current主指标及仅末尾查询安排不再用于新实验。

**冷启动和已有快照恢复入口已完成离线修改与回归，尚未实跑。** 每条序列只用一个新统计组、一个私有服务和一个可写卷；老沙箱保持运行，只增加新沙箱。anon与current作为主要规模指标，file保留用于解释缓存变化，不生成整体物理节省率。

[离线增长分解](HOST_LIGHT_GROWTH_BREAKDOWN.md) · [整体核对与统计边界](HOST_TOTAL_OFFLINE_AUDIT.md) · [实现验证记录](evidence/host-scaling-entry-20261022.json)

## 两种序列使用相同的递增方式

```text
创建新cgroup，开启内存和I/O记账
→ 准备输入，恢复模式导入已有快照
→ sync -f 本次自有文件系统
→ 创建私有服务，确认无预启动VMM
→ 记录无沙箱基线
→ 增加到1个 → 2个 → 4个 → 7个
→ 最后一次指定对象驻留查询
→ 原结果检查、删除自有沙箱／服务、原清理
```

- **冷启动：**七个新guest各执行相同原11步前缀，共77个原工具动作；不做后缀、快照或官方测试。
- **恢复：**复用已验证第11步快照，七个新guest各执行原early_1的相同10步后缀，共70个新动作；不重跑11步前缀或制作新快照。七个角色都按early_1原动作／退出码核对，保留文件、进度、进程连续性、补丁和每实例原官方0通过／2失败检查。

入口复用原GRPO控制器的创建、执行及finally清理，但执行的是新增递增序列，不冒称原完整GRPO／BPO调度。恢复模式直接引用原前缀证据，不把其11动作复制进本轮backbone动作记录。

已有导出源`.artifacts/storage-isolated-service/20261021T031738Z-3920554/`的原外层FAIL保持；记录的导出恢复与归档验证元数据、原前缀身份核对通过，允许按既有`resumed_capture`契约复用，新增动作0。本轮只读元数据，没有重新扫描或校验整个归档内容。

## 每批保存什么

固定读取一次无沙箱基线，每批创建前和前缀／后缀完成后再读取，合计9次检查点：

- 实际统计组路径与身份、host单调时钟起止、`memory.stat`／`memory.current`直接计数及查询后计数。
- 全组PSS及每个VMM的PID／启动身份，PARTIAL原样保留，不补完整总量。
- 每个仍运行guest的`MemTotal`、`MemAvailable`、`Cached`、`Shmem`，通过原控制通道只读meminfo。
- 每批新增角色、任务结束时刻、相对共同基线、相对本批开始及相对上次端点的增量。
- 老guest在本批前后的Cached变化，以观察自然回收／时间影响；不把这类变化误算成新guest独占占用。

每批端点读完后，立即核对本批各guest的原动作、顺序、退出码与数量，再允许创建下一批；N=1检查失败就停止扩展，交给原finally定向清理，不先跑到7个再判断首批是否正确。

开启周期采样时仍为guest 0.1秒meminfo、host 0.1秒计数和每秒全组PSS。关闭周期host采样时，guest采样和上述固定检查点保持。这和旧matched-task关闭host全部观察的语义分开，旧调用保持原行为。

中间阶段不做完整mincore；只在7个guest结束原动作后、结果检查及删除之前，做一次末尾驻留观察。冷启动查询实际共享只读驱动；恢复查询实际共享内存设备及原始层。任务结束时刻、查询用时及控制程序总用时分列。不同对象不是同时读取，PSS、file与驻留不相加。

开启组收尾另保存普通文件清单，新增`env/firecracker-work`目录，并沿用“内层文件先查、同步自有文件系统、卷镜像最后查”的顺序。关闭周期host采样的一组仍做前述运行中末尾查询，但沿用关闭模式跳过清理后的附加普通清单；这部分额外成本单列，不混进任务减速。

## 运行入口与资源边界

以下是后续获准实跑时的调用，不表示本轮已经运行。复用当前普通host配置，不改写旧配置。源服务须按既有流程确认身份并处于健康状态，设置环境变量本身不会启动服务；入口不替换原服务。

```bash
export ASB_AGENTENV_SOURCE_SERVICE=asb-source-rebuilt-20261018T160925Z-2726362

# 冷启动：16 GiB卷，启动前须超过42 GiB可用磁盘。
bash scripts/run-host-file-monitor.sh \
  configs/platform-prettier6604-host-file.json --scaling cold

# 恢复：24 GiB卷，启动前须超过50 GiB可用磁盘。
bash scripts/run-host-file-monitor.sh \
  configs/platform-prettier6604-host-file.json --scaling restore \
  --resume-prefix .artifacts/storage-isolated-service/20261021T031738Z-3920554
```

恢复的关闭周期采样对照使用同一命令加`--host-sampling off`。资源预算由现有磁盘保护、固定卷、独立cgroup及私有服务边界执行；保留20 GiB保护量及6 GiB余量，失败卷不自动删除。不清全机缓存、不额外暂停、不预读负载、不强制驻留，不改DAMON。

输出位于`.artifacts/host-scaling/<运行标识>/`：`result.json`给出阶段和清理结果，`report/REPORT.md`、`scaling.png/svg`给出规模图；实际任务目录`prettier6604/host-scaling.json`保留完整检查点，原guest三指标图由隔离流程照常生成。开启组另有`host-file/memory.tsv`和`pss.jsonl`，关闭组不补造周期曲线。

成功任务的报告若需修补，可直接离线重建，不重跑沙箱：

```bash
python3 -B scripts/host_scaling.py \
  --report .artifacts/storage-isolated-service/<运行标识>/prettier6604 \
  --output .artifacts/<新的报告目录>
```

## 已验证与未验证

12项新增离线用例覆盖递增顺序、每条路径只执行一次、首批原动作检查失败不扩展、同组／旧VMM身份、恢复来源、原官方失败保留、关闭周期采样、同步失败及部分PSS。88项相关用例已有通过证据：首次87项中85项通过，两个失败分别为旧源码拼写检查过时和CLI错误说明变动，修复后3项受影响用例通过，随后新增用例连同12项新用例通过。未重新执行未改动的真实mincore小文件用例，其既有证据复用。

两种模式的真实命令行check-only通过；参数检查不启动服务。图形使用明确标记的测试数值查看布局，未作为实验结果发布。代码修改前副本、差异、原失败及补验日志在`.artifacts/host-scaling-offline-20261022/`。

**真实递增启动、七实例共享状态、实际同步后脏页、运行期开销及清理仍待实跑验证。** 同组递增减少两次独立准备的差异，但没有消除老guest的时间变化；线性参考也不等于实测不共享后端。旧1.7 GiB归档的当前组外容量不自动加进新结果，仍按对应对象／阶段处理。

**下一步建议：**先执行一条冷启动序列，N=1作为新路径的必要短验证，通过后同组继续到2／4／7，不另加smoke。以77动作、同步和基线、四个阶段的计数及身份、guest样本、末尾对象查询和清理齐全为完成条件。之后再按已讨论范围做恢复70动作及关闭周期采样70动作，不在本轮自动启动。
