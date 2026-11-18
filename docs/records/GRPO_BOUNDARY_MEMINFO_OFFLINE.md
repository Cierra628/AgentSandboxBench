# 边界meminfo离线核对：保留读数，优先复用现有计时进程

更新：[两个动作的本地原型已通过](GRPO_BOUNDARY_MEMINFO_TIMER_PROTOTYPE.md)，记录与退出保持，实际少8次date／awk调用，尚未接入实跑。以下保留此前离线用途核对；原型进一步明确Python启动与解析仍在原计时口径内。

## 结论：不能删点，但可以研究减少外部命令

2026-11-01只读刚完成的四次七沙箱GRPO日志和对应代码，确认每轮448次边界meminfo读取都有现存用途。它们参与原任务结果中的峰值计算，并同步更新周期采样的阶段标签；当前轻量内存曲线本身使用另一套0.1秒周期记录。

**建议保留全部448条记录，优先验证把378次动作前后读取合并到已有Python计时进程。** 每轮已有378次这样的计时调用；不另开进程，段首／段尾70次保持原方法。按源码推导可少启动378次date和378次awk，共756次外部命令。实际是否更快尚未测量，候选未实现、未接入实跑入口。本段没有改运行代码、配置、采样器或既有指标。

## 448次分别发生在哪里

每轮原189个动作、35个执行段：

| 时刻 | 条数／轮 | 与原动作的关系 |
| --- | ---: | --- |
| 段首baseline | 35 | 段初始化完成后、进入动作循环之前 |
| 动作前before | 189 | 原命令开始计时之前 |
| 动作后after | 189 | 原命令结束时间戳之后 |
| 段尾complete | 35 | git状态／补丁导出及清单复制之后 |
| 合计 | 448 | 2×189＋2×35 |

这四个时刻不是同一个采样点。段首与第一次动作前之间还有清单校验；最后一次动作后与段尾之间还有git状态、补丁和清单处理，内存状态可能变化。每次sample_memory还会启动一次date取得墙钟纳秒时间戳、一次awk读取和筛选/proc/meminfo，共896次外部命令；这里统计的是命令调用次数，不是系统调用次数或秒数。

四次保存的实际runner均与当前boundary_runner的对应变换一致；保存的任务执行器和周期采样器源码也与当前版本一致。没有用旧版本或仅凭配置推断当前行为。

## 两套内存记录分别用于什么

### 边界记录保留原任务峰值和阶段状态

[verify_path](../../scripts/run_prettier6604_grpo.py)的195–223行读取全部边界guest-memory.tsv，用`MemTotal−MemFree`计算`guest_observed_peak_bytes`，并在跳过vmstat时要求每段仍为`2＋2×动作数`条。[任务审核](../../scripts/report_prettier6604_grpo.py)的122–138行重新计算并核对该值，还从同一组记录取得峰值处的AnonPages、Cached、Buffers和Shmem。

因此，删掉这些读数会改变已有结果的含义，不能把报告中目前未直接画出的字段当作无用途数据。

[boundary_runner](../../scripts/sandbox_runtime_monitor.py)的193–212行还在sample_memory中写阶段文件：before、after和baseline原名保留；边界表仍写complete，但后台周期采样的阶段改成waiting。每个动作开始前另写running。候选需要保留这些标签、顺序和用途，不把阶段文件当成完整的事件日志。

### 主曲线来自周期采样，不混入边界记录

[样本选择](../../scripts/report_sandbox_runtime.py)的12–41行在存在runtime-monitor.json时直接使用各采样会话的文件；只有历史无会话记录时才回退到执行段边界表。[轻量报告](../../scripts/report_task_overview.py)的100–132行据此生成`MemTotal−MemAvailable`和Cached曲线及观测峰值。

当前后台进程实际执行的是[sandbox_runtime_monitor.collect](../../scripts/sandbox_runtime_monitor.py)，轻量模式每0.1秒读取meminfo；guest_memory_collector模块提供字段和解析函数，不能将其独立旧入口误认为当前进程。

边界的`MemTotal−MemFree`与主曲线的`MemTotal−MemAvailable`不是同一个指标，也都不是host物理占用。不把两个峰值互换或合并。

## 保存数据已经提供了删点会丢峰值的反例

在28个沙箱记录中，离线过滤掉某类行后，重新计算同一边界指标；没有修改原结果或重建替代曲线：

| 离线去掉的行 | 峰值变小的沙箱数／28 | 最大减少／MiB |
| --- | ---: | ---: |
| 段首baseline | 1 | 67.066406 |
| 动作前before | 11 | 127.757813 |
| 动作后after | 6 | 0.972656 |
| 段尾complete | 3 | 44.007813 |
| 同时去掉段首和段尾 | 4 | 67.066406 |

例如第二轮late_3的峰值出现在段首；第四轮backbone的峰值出现在core_patch段尾。它们在仅保留动作前后的计算中分别少67.066406和44.007813 MiB。即使某类读数在其余沙箱中没有改变最大值，也不能据此保证以后都可删除。

另按每个guest自身的边界起止墙钟范围，从周期数据中选取相同范围的记录，比较两者共有的`MemTotal−MemFree`：第二轮early_3的边界观测峰值比周期观测峰值高60.609375 MiB。两边时间记录均无倒退，没有跨guest对齐或插值。这个反例说明0.1秒采样不能保证替代边界峰值；它不是轻量主曲线`MemTotal−MemAvailable`或host current的缺口估计。

[28条逐沙箱核对表](evidence/grpo-boundary-meminfo-roles-20261101.tsv)保存每种删点计算、共有指标的比较和采样范围内点数。

## 最小候选：复用动作计时的Python，不增加采集进程

原执行脚本在每个动作前后各启动一次Python，取得`time.monotonic_ns()`并写入steps.tsv。可研究在这378个现有调用内完成对应的meminfo读取和阶段标记，仍输出原本的单个计时数字；段首和段尾70条维持现有Shell函数。没有必要新建常驻进程、消息队列或文件页归属采集器。

先只考虑已明确跳过边界vmstat的当前轻量GRPO候选：默认保留vmstat时，原meminfo／vmstat／计时的顺序另有约束，不在本次候选内改变。

必须满足：

1. **读数不减，字段不换。** 全部448条边界行、原表头、九个meminfo字段及KiB单位、phase／round／step保持；Active(file)和Inactive(file)仍写成Active_file和Inactive_file。原awk将缺失字段写0，不能顺手改为其他语义。
2. **时钟不混。** 内存行仍用墙钟纳秒时间戳，工具时延仍用单调时钟差。before读取和running标签在开始计时之前；结束时间戳在after读取之前。
3. **辅助导入和采样主体放在捕获时间戳之外。** 尤其结束计时必须先沿原顺序完成`import time`和单调时钟读取，再导入采样辅助代码、解析meminfo和写文件；不能把辅助模块导入放到结束时间戳之前。开始侧则先完成采样、再取开始时间戳。
4. **原动作及检查不变。** 命令、退出码、标准输出／错误、补丁、阶段顺序和complete→waiting映射保持。辅助代码不能覆盖已捕获的原动作退出码或将采样错误当成功。
5. **原入口保持。** 从原冻结runner派生显式候选，不修改冻结原文件；未选候选时保持当前文本及行为。0.1秒后台采样和host采样不改。

这只是源码与调用次数层面的可行设计。每轮从896次相关date／awk调用减到140次的推导成立，需要增加的Python解析工作及最终耗时收益仍须验证，当前没有节省秒数的结果。

## 本段证据与下一步

新增[离线核对工具](../../scripts/assess_grpo_boundary_meminfo.py)及bash入口，只读取保存的脚本、结果和表格。六项合成记录测试验证阶段顺序、删点后的峰值、周期时间范围和相同指标比较，均不触碰真实服务或/proc数据；四次原记录中的全部28个边界峰值与原结果一致。

[脱敏证据](evidence/grpo-boundary-meminfo-offline-20261101.json)保存源码对应、调用次数和删点结果；原精度摘要、初次核对及检查记录在`.artifacts/grpo-boundary-meminfo-offline-20261101/`。没有新沙箱、联网、驻留查询或清理。

复算只读保存数据，输出目录必须不存在：

```bash
bash scripts/assess-grpo-boundary-meminfo.sh \
  --source .artifacts/grpo-local-vmstat-controls-20261101/result.json \
  --output .artifacts/grpo-boundary-meminfo-check-new
```

下一步建议只做两个动作的本地模拟：使用固定meminfo文本及成功／失败各一个空动作，验证复用计时进程后六条边界行、字段、阶段、退出码和计时顺序保持；不接入实跑入口，不用本机运行时间代替guest收益。以保留原行为及精确减少外部命令调用为完成条件，通过后再决定是否值得正式接入。
