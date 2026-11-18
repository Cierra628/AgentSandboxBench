# 新增轻量测量任务选定：Prettier #14400（2026-10-28）

**已离线确认Prettier #14400的完整26步冻结输入、固定镜像和官方检查可复用。下一项选它，不再重复CSV，也不需要重新生成模型轨迹。** 本阶段完成输入核对；后续[轻量适配与一轮完整验证](HOST_PRETTIER14400_LIGHT.md)已完成，原26步、独立／官方检查、采样、自动报告和最终卷检查均通过。

此前“本地只有三个标准代码任务”的清单只覆盖新版`platform_tasks.py`，漏掉了旧Exp1入口。这项任务已有旧方案的功能结果，现在新增的是current主指标、guest轻量采样的接入，不是声称它从未运行过。

## 已固定的输入与结果条件

| 项目 | 选定内容 |
| --- | --- |
| 任务 | `prettier__prettier-14400`，修复SVG `<script>`内部JavaScript格式化 |
| 初始代码版本 | `6905b39ec7bc6ed38732e26ceced5b57677e2600` |
| 固定镜像 | `ghcr.io/timesler/swe-polybench.eval.x86_64.prettier__prettier-14400@sha256:e625c9b9776870e2cc87172e886bbc90cfc3d6ce1521492f113a46b3f6dfcf44` |
| 动作 | 原`actions/001.sh`至`026.sh`，顺序和内容均由既有冻结清单校验 |
| 执行方式 | 2 CPU／4096 MiB；每次动作在`/testbed`中新开bash；每动作超时60秒 |
| 原预期失败 | 第008步`rg`缺失、第016步`applypatch`缺失，退出码均127；其余24步为0 |
| 核心修改 | `src/language-html/utils/index.js`；最终patch保持既有固定值，不修饰原补丁格式 |
| 第024步关键输出 | `document.addEventListener`与`const node = document.getElementById("lastStroke")`按原缩进分行 |
| 独立功能检查 | SVG、嵌套SVG、普通HTML三种输入，JavaScript已格式化且再次格式化结果不变 |
| 官方检查 | 固定SWE-PolyBench测试补丁、测试命令和上游解析器；原参考为4条通过断言、0失败 |

数据集的F2P（原先失败、修复后应通过的测试）清单有1个格式化测试条目，P2P为空；官方Jest运行产生4条断言，不能把这两个计数混用。冻结动作、关键输出、patch和独立检查都需要保留，不以整段shell退出0代替任务正确性。

任务动作使用镜像原有Node环境；官方命令按固定输入另用`nvm use 20.16.0`。不能为了统一版本而改写原26个命令。第025／026步的Git暂存仅属于未来guest中的原动作，本次没有在host执行动作脚本或暂存项目文件。

## 核对了哪些原始材料

- 冻结动作：`IncrementalDAX_moti/motivation/experiments/exp1-single-app-smoke/workloads/prettier-14400/`。
- 镜像定义和AgentENV原runner：同一实验的`systems/agentenv/config.env`与`guest-runner.sh`。
- 固定任务、测试补丁和解析器：`.artifacts/v1-closeout-inputs/official-task/`。
- 既有校验器：`scripts/exp1_inputs.py`、`exp1_official_task.py`、`exp1_physical_workload.py::verify_replay`。
- 只复核一份旧功能参考：`.artifacts/agentenv-whole-cache/20261007T033805Z-3397540/`。26步、预期退出码、关键输出及patch通过既有检查，三种独立输入通过；后续`official-before.json`记录4通过／0失败，且任务清单与当前固定官方输入一致。

该参考使用旧采集方案，本次只复用任务定义和功能判断，不复用它的内存曲线、时延或采样开销作为新方案结果。当前转换缓存是否可供新私有服务直接使用，留到执行准备时检查；本次没有读取镜像内容或启动服务来判断。

## 可以复用什么，还需接入什么

推荐复用**现有隔离Exp1分支**，显式增加轻量模式；不把#14400改名为#6604或CSV来绕过入口限制。新版v2任务表目前仅支持原三任务，CSV入口也不接受这项任务，因此本次没有给出一个会误跑旧采样器的“新模式运行命令”。

最小改动范围：

1. 给隔离Exp1控制程序和外层入口传递显式轻量选项。沿用完整26步、原结果检查和私有服务生命周期；不运行checkpoint，不加并发或多轮。
2. guest只保留一个0.1秒meminfo采样进程。执行原runner时设`GUEST_MEMORY_SAMPLING=0`关闭旧逐动作采样，`DROP_GUEST_CACHES=0`保留默认回收；复用已验证的上传、就绪和回收流程。旧入口不带新选项时原行为不变。
3. 复用原`steps.tsv`布局和动作结果，工具耗时采用单调时钟；原runner目前用`date +%s%N`，不能直接把它标为新版单调计时。为Exp1结果适配工具／guest报告，不沿用CSV固定调用数或Sphinx分段布局。
4. 准备前建立新cgroup，私有服务和新VMM从创建时就在组内。host每0.1秒记录current与anon/file/kernel，保存创建前基线、观测峰值及增量、采集成本和缺口；PSS和mincore关闭，不同时启动旧host监控。任务完成后仍实际执行最终卷边界、可写文件清单和清理检查。

初始代码以固定镜像和HEAD约束，保留镜像已有的非任务改动，以原runner的baseline-tree形成任务patch；不要在host或guest里先做整体Git清理。默认DAMON不改，不带入旧实验的关闭回收、锁频或强制驻留选项。

## 本阶段输出与复核方法

输入状态为`INPUTS_VALIDATED_ADAPTER_REQUIRED`，表示输入已核对、适配待实现；不是新任务执行通过。

```bash
bash scripts/check-prettier14400-light-inputs.sh \
  --output .artifacts/prettier14400-input-check-new
```

此入口只读本地冻结输入和上述一份功能参考，不调用模型、Docker、服务、沙箱或探针。输出目录必须不存在，生成`summary.json`和`legacy-full-replay-inputs.json`。后者只供旧输入校验器验证完整重放条件，不会启用新轻量测量。

本次实际结果：`.artifacts/host-prettier14400-offline-20261028/verified/`；检查日志在上一级。可共享的输入与完成条件摘录见[证据JSON](evidence/host-prettier14400-inputs-20261028.json)。未改旧任务、旧入口或采样设置。

## 输入核对后的实施范围（现已完成）

以下为输入核对时确定的范围；2026-10-28已按此范围完成一轮，见[真实结果与图表](HOST_PRETTIER14400_LIGHT.md)。下述“不启动”仅描述当时输入核对阶段，不代表当前还未实跑。

接入上述最小改动并做离线回归，然后只运行一轮26步完整任务，首轮兼作新路径验证，不另加相近短组。完成条件是：原26动作／退出码／关键输出／patch、三种独立输入和官方4条断言保持；guest与host的0.1秒数据、current创建前基线和成本有效；修补后的自动报告、最终可写文件检查与自有清理全部实际执行。

沿用现有Exp1的20 GiB隔离卷时，启动前可用磁盘须严格超过46 GiB；实际预算若需要24 GiB，则仍须严格超过50 GiB。执行时重新检查缓存、负载和预算，两份CSV失败卷仍须保留，未获得新的删除授权。本次不启动这一轮，也不把CSV未执行的尾部检查补成PASS。
