# 边界meminfo与计时进程合并：两个动作的本地原型通过

后续已完成[受限接入与离线回归](GRPO_BOUNDARY_MEMINFO_INTEGRATION.md)，新增显式独立／合并对照模式，尚未实跑。以下保留本地原型阶段的记录。

## 已验证记录保持，外部命令减少

2026-11-01完成固定meminfo文本和两个模拟动作的本地验证：动作001成功、002失败，并各自输出固定标准输出和错误输出。参考方法沿用刚完成GRPO中实际保存的sample_memory函数和原Python计时语句；合并方法将动作前／后采样放进已有Python计时进程，段首／段尾仍使用原函数。

**本地原型可用，值得进行受限接入和离线回归。** 原型尚未连接平台配置、任务执行器或正式报告入口，guest性能未测。本阶段没有启动AgentENV服务或沙箱，没有读取真实/proc内存指标、下载、驻留查询或清理。

| 检查项 | 参考方法 | 合并方法 |
| --- | ---: | ---: |
| 两动作退出码 | 0、1 | 0、1 |
| 边界内存记录 | 6条 | 6条 |
| date调用 | 6次 | 2次 |
| awk调用 | 6次 | 2次 |
| Python计时调用 | 4次 | 4次 |
| 最终阶段文件 | waiting | waiting |

两边原表头、九个meminfo字段及KiB值、sandbox／branch／round／step、六条行的阶段顺序均一致。Active(file)和Inactive(file)对应Active_file和Inactive_file；固定文本中故意缺少Dirty，原awk和Python方法均写0。标准输出／错误和动作文本完全一致；边界MemTotal−MemFree峰值也相等，其3 MiB数值只用于固定样本的算术检查。

六条边界行依次是baseline、before、after、before、after、complete。真实文件中另核对了段首、两次动作后和段尾这四个阶段检查点；before→running的瞬时顺序由辅助函数测试核对：取得内存墙钟时间戳时标签仍是before，函数返回前改成running。边界complete行保持原名，阶段文件改成waiting。

时间戳和动作持续时间是动态值，不要求两次运行相等。核对了墙钟纳秒的格式和非倒退顺序、持续时间为正；没有把它们改成固定值或零值来通过比较。

## 计时顺序保持，解释范围更明确

[原型的开始和结束语句](../../scripts/probe_boundary_meminfo_timer.py)分别按以下顺序执行：

- 开始：import time → 导入辅助函数 → 写before／读取meminfo／记录行／写running → 捕获单调时间戳 → 输出该数字。
- 结束：import time → **先捕获单调时间戳** → 导入辅助函数 → 写after／读取meminfo／记录行 → 输出先前捕获的数字。

[七项测试](../../tests/test_boundary_meminfo_timer_prototype.py)中，确定性的Python执行记录核对了上述顺序及输出值；辅助导入和采样主体都在捕获的单调时间戳之外。读取错误会向上传播，结束侧不会在采样失败后输出一个成功的计时结果。动作原退出码在结束计时调用前已保存，未被辅助函数覆盖。

工具时延仍是原脚本两次单调时钟读数之差。Python启动、参数处理、-c代码解析等仍在这个口径内；合并方法的新参数和代码长度也可能改变这部分成本，本次未单独量化。采样主体的执行顺序已经验证，guest中的实际耗时影响留待接入后的对照，不从本机模拟推算。

这也将上一轮设计中“新增代码不计入原命令时延”的表述明确为“辅助导入和采样主体在捕获的时间戳之外”；计时指标和公式没有改成纯命令CPU时间，也没有扣除未知启动成本。

## 实现和计数怎样验证

- [辅助函数原型](../../scripts/prototype_boundary_memory.py)只解析指定meminfo文件、写边界行和阶段文件。模拟中使用固定文本，没有增加常驻进程、后台采样器或进程间通信。
- [本地验证程序](../../scripts/probe_boundary_meminfo_timer.py)只提取保存runner中的内存函数；将/proc/meminfo和阶段目录改到新建的本地结果目录。原任务命令、仓库访问和git操作没有进入模拟。
- date、awk和python3使用本地计数包装脚本，然后调用原工具。计数对象是模拟执行脚本发起的这些外部命令，不是操作系统全部进程或系统调用；包装脚本属于验证设施，不作性能基准。Python禁写字节码，固定环境不继承代理。
- 参考和合并方法的工具调用日志分别验证了6／6／4与2／2／4。两个动作减少四次date和四次awk，没有增加Python计时进程。若以后完整接入同样方法，189动作对应少756次date／awk调用；该数值仍是外推的调用次数，不是提速比例。

原型没有修改冻结runner，也没有改变现有boundary_runner、采样器、平台配置或报告检查。现有入口不引用这个原型。

初次参考模拟因本地动作模板未把step值传入子Shell，输出成out-而非out-001，检查正确失败；已改为固定动作文本并完成两种方法的验证。该问题只在模拟模板中，原任务未运行，初次日志和输出保留，没有将它计为原型通过结果。

## 证据与下一步

[脱敏结果](evidence/grpo-meminfo-timer-prototype-20261101.json)保存实际调用数、检查范围和七项测试结果。原始证据在`.artifacts/grpo-meminfo-timer-prototype-20261101/`：成功验证为`two-actions-fixed/`，包括两份脚本、固定输入、每次工具调用日志、六行内存表、两个动作输出及阶段文件；初次失败记录另存。

协作复现只需本地bash、awk、date和Python，不需要服务或网络；runner路径应指向已保存的跳过vmstat版本，输出目录必须不存在：

```bash
bash scripts/probe-boundary-meminfo-timer.sh \
  --runner .artifacts/storage-isolated-service/20261101T112655Z-3051179/prettier6604/frozen-input/guest-runner.sh \
  --output .artifacts/meminfo-timer-prototype-new
```

下一步建议把候选受限接入当前显式跳过vmstat的轻量七沙箱GRPO，配套处理参数、辅助代码的冻结与上传、执行脚本及报告校验；以旧入口原行为保持、新候选448条边界行／计时顺序／错误传播通过离线回归为完成条件。先不启动实跑，通过后再确定最小真实对照范围。
