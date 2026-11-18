# 边界meminfo合并计时：受限接入与离线回归完成

后续已完成[正式两对对照](GRPO_BOUNDARY_MEMINFO_VALIDATION.md)：功能通过，但本批没有提速，原默认保持。下文保留离线接入阶段记录。

## 已可显式选择，真实收益留待对照

当前本地无代理、逐guest准备、完整七沙箱轻量GRPO已支持`light_merge_boundary_meminfo`。它要求同时启用`light_skip_boundary_vmstat`，仅合并动作前后的读取，不减少边界记录，不改变原调度、默认回收或0.1秒host／guest采样。

候选配置：[platform-prettier6604-grpo-host-current-merged-meminfo.json](../../configs/platform-prettier6604-grpo-host-current-merged-meminfo.json)。相对原跳过vmstat配置，只增加`light_merge_boundary_meminfo: true`。未选择该项时，原执行脚本文本和行为保持。

本段完成代码接入及离线验证，没有创建真实服务或沙箱，没有下载、驻留查询、磁盘清理或真实任务重放。实际guest功能和耗时影响尚待验证。

## 从配置到报告已接通

- 平台、普通输入和原生入口只允许当前完整轻量GRPO组合。关闭采样、保留边界vmstat、旧的先全部创建再准备、单角色、其他任务、BPO／恢复、共享页诊断及回收干预等不放行。平台检查、原生预检查和实际执行都传递`--light-merge-boundary-meminfo`。
- [执行脚本变换](../../scripts/sandbox_runtime_monitor.py)复用[已验证的辅助函数及计时语句](../../scripts/prototype_boundary_memory.py)。段首／段尾仍调用原Shell函数；动作前／后转入原有Python计时调用，使用`-B`禁止辅助模块写字节码。
- 执行器将辅助文件复制进本轮`frozen-input`，沿原整目录上传到guest的`/workspace/mixfs-workload`；在`code`中另留源码，纳入既有源码记录。本次只核对复制和上传参数，没有进行真实上传。冻结原输入不改。
- 请求、有效配置、脚本变体和结果均记录新选项。任务结果及轻量／独立审核报告保留`boundary_meminfo`，标记`MERGED_WITH_ACTION_TIMERS`；报告核对冻结辅助文件与保存源码、重建执行文本、检查边界表头／字段／顺序。辅助文件缺失或变化、结束时间戳被改、内存缺行等仍失败。
- 每轮189动作、35段对应448条边界内存、378次计时内读取和70次段首／段尾读取。少756次date／awk是相对“已跳过vmstat但内存仍分别读取”的源码调用次数推导，元数据明确不将它写成实测CPU或秒数。

0.1秒周期采样、host current统计和主曲线定义不改。边界峰值仍是MemTotal−MemFree，轻量主曲线仍为周期MemTotal−MemAvailable和Cached；没有用删点、换公式或扣除未知时间来改善数字。

## 新对照只比较内存读取方式

新增显式`--comparison boundary-meminfo`：固定**独立→合并、合并→独立**两对四次。这里独立读取指单独调用date＋awk，合并读取指复用动作计时的Python；两组都开启0.1秒采样、都跳过边界vmstat，均保留448条内存记录。不同组不是共享文件／独立副本对照。

下一阶段可使用以下入口，本段未执行；输出目录必须不存在：

```bash
bash scripts/run-grpo-local-no-proxy-controls.sh \
  --comparison boundary-meminfo \
  --output .artifacts/grpo-meminfo-controls-new
```

原默认`sampling`仍比较整套采样关闭／开启，原`boundary-vmstat`仍只比较保留／跳过vmstat；这两种模式拒绝混入新合并选项。新的结果用`boundary_meminfo_mode=separate/merged`区分，`mode`四次都是on，不将合并写成关闭采样。

四次完成后，原报告入口自动根据保存的比较类型生成“合并减独立”的表格、成本记录和13张图：

```bash
python3 -B scripts/report_grpo_local_no_proxy_controls.py \
  .artifacts/grpo-meminfo-controls-new \
  results/grpo-meminfo-controls-new
```

也可只运行一轮候选，但当前计划使用上面的两对入口，将首个合并组兼作新路径验证，不增加单独短测。单轮入口同样尚未执行：

```bash
bash scripts/run-host-file-monitor.sh \
  configs/platform-prettier6604-grpo-host-current-merged-meminfo.json \
  --persistent-input --local-no-proxy
```

## 离线验证覆盖了什么

**83项相关测试通过**：新接入12项、原型7项、原vmstat10项、原本地GRPO9项、普通输入15项、普通任务输入16项、平台14项。新增比较模式后，原四次控制流的同一项测试又单独通过，不重复计入83项。

其中关键证据是：

- 使用当前`boundary_runner`真正生成并执行两个本地动作的脚本；固定meminfo，退出0／1、六条边界行、输出及waiting阶段保持，date／awk各6→2，Python仍4次。没有执行原任务命令或读取真实/proc内存数据。
- 合成七角色／35段／189动作数据通过新检查，保留448条边界行；错误表头、源码变化／缺失、请求或元数据不一致、脚本篡改和不支持组合被拒绝。
- 真实冻结输入的候选`--check-only`通过，七角色均被识别；实际执行命令未运行。完整候选脚本`bash -n`通过。
- 四份既有保留／跳过vmstat脚本与接入前变换逐字相同；完整保留和跳过组各189动作重新离线审核通过，原每实例官方0通过2失败保持。
- 原整套采样与vmstat两种报告重建后，`summary.json`、`runs.tsv`、`pairs.tsv`及`REPORT.md`共八份文件与既有交付完全相同。
- 新对照的四次顺序、参数选择、差值正负号、首个失败即停止及报告标签通过模拟检查。13张模拟报告图只用于字段／布局检查，新计时图已查看；文件和图明确标为SYNTHETIC，不是性能结果。

原命令计时仍使用两次单调时钟差：开始侧在采样后捕获，结束侧在新增辅助导入／采样前捕获。Python启动、参数及-c代码解析仍属于原计时口径，实际成本变化留到真实对照中报告。

[脱敏实现证据](evidence/grpo-meminfo-integration-20261101.json)和`.artifacts/grpo-meminfo-integration-20261101/`保存修改前源码、候选脚本、测试日志、旧结果核对和模拟布局。原型及前序实跑结果按各自版本保留，本段没有新的真实耗时或内存数据。

## 下一步：固定两对，首个合并组验证新路径

启动前重新检查实际负载、磁盘严格>54 GiB、MemAvailable>64 GiB；来源释放后分配16 GiB运行卷前严格>42 GiB，原运行中保护和失败现场保留规则不变。复用已验证本地输入，不使用17891，不预先删除资料。

按独立→合并、合并→独立完成四次，共28个新guest、756个原动作。完成条件是原命令／退出／关键输出／七角色补丁及独立检查、每实例官方原0通过2失败保持；周期采样与448条边界完整，候选辅助代码及标记／计时顺序正确，105输入查询及自有清理通过；逐对工具累计、动作／下载窗口和整轮差值可复算。首个候选组兼作新路径验证，通过后完成剩余两次，不追加轮数或单独短测。检查器错误先用原日志修复，任务／平台失败保留首个现场并停止。

该计划供检查，本段不自动启动。
