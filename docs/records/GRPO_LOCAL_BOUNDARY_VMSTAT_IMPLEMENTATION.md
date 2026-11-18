# 当前GRPO跳过边界vmstat：离线接入完成（2026-11-01）

更新：[正式两对已完成](GRPO_LOCAL_BOUNDARY_VMSTAT_VALIDATION.md)，功能与采样通过，未显示稳定加速，原默认保持。以下保留离线实现阶段的记录。

## 新入口已经接通，性能留到实跑验证

已在当前Prettier普通输入／无代理／逐guest准备的完整七沙箱GRPO中接入`light_skip_boundary_vmstat`。复用原脚本变换、采样器和调度，不带新选项时仍保留边界vmstat；旧整套采样关／开入口默认行为保持。

候选配置为[platform-prettier6604-grpo-host-current-skip-vmstat.json](../../configs/platform-prettier6604-grpo-host-current-skip-vmstat.json)，相对原开启配置只增加`light_skip_boundary_vmstat: true`。仍保留0.1秒host／guest周期采样、448条边界meminfo和阶段写入；vmstat只留原表头，用`NOT_MEASURED`表示未采集，不表示故障或I/O为零。

本段完成实现和离线验证，没有启动真实服务或沙箱，没有下载、驻留查询或清理。候选实际任务功能、采样间隔和耗时收益尚待正式四次对照。

## 比较的是这一项，不再关闭整套采样

已增加显式比较类型`boundary-vmstat`，固定顺序为**保留→跳过、跳过→保留**。四次都为`mode=on`，另外用`boundary_vmstat_mode=keep/skip`记录区别。两组都保留原189动作、七个4 GiB／2 vCPU guest、默认回收、逐guest准备和并发调度、评测、105输入查询及资源保护。

以下是下一阶段正式入口，本段未执行；输出目录必须不存在：

```bash
bash scripts/run-grpo-local-no-proxy-controls.sh \
  --comparison boundary-vmstat \
  --output .artifacts/grpo-local-vmstat-controls-new
```

参数省略或为`--comparison sampling`时，仍是原关→开、开→关四次。不能把旧的关闭采样数据直接放入新的保留／跳过配对。

完成正式任务后，原报告入口按保存的比较类型自动选择报告：

```bash
python3 -B scripts/report_grpo_local_no_proxy_controls.py \
  .artifacts/grpo-local-vmstat-controls-new \
  results/grpo-local-vmstat-controls-new
```

新报告使用“保留／跳过”和“跳过减保留”，四组都有内存曲线、CPU及实际边界行数。它不把跳过vmstat写成关闭采样，也不把缺失数据写成零值。时间窗口、current范围及输入驻留含义不变。

## 范围识别和结果检查一起完成

平台配置、普通输入识别、原生参数和任务执行器只对当前显式本地完整GRPO放行新候选；关闭采样、单角色、BPO／恢复、前缀、共享页诊断和回收干预等组合继续拒绝。旧先全部创建再准备的候选保持原入口。

任务及共享配对检查器使用保存的七guest无代理证明、逐guest准备状态和host计数上下文识别新分支，不依赖离线报告进程的环境变量。候选选项必须在请求、有效配置、实际脚本、结果及报告中一致；内存缺行、vmstat表头损坏／出现数据或元数据不一致仍失败。原命令、退出码、关键输出、补丁和官方原失败规则保持。

原平台构造、原生预检查／实际执行两处参数转交、`verify_path`表头和行数检查直接复用。共配套修改8个运行／检查／报告脚本，另更新原离线核对工具以反映当前支持状态。采样器及原`do_grpo`调度不改。

现有逐路径分析器仍按两种边界都有数据的模式工作，会拒绝本候选；本阶段不扩展它。首次收益比较使用已保存的工具、动作窗口、控制程序等计时，需要细分阶段时可复用不依赖vmstat的阶段函数。

## 离线验证结果

- **149项相关检查通过**：无代理76项、旧vmstat10项、新接入9项、普通输入31项、GRPO9项、平台配置14项。包含原两动作模拟：退出0／1、内存记录和阶段文件保持，候选不读vmstat；错误表头、额外数据、内存缺行及错误退出码仍拒绝。
- **20项当前核对通过**：真实候选配置／原生解析只多一个跳过选项，实际GRPO输入检查命令`--check-only`通过；没有执行live命令。
- 原保留vmstat的完整开启／关闭记录各189动作重新离线审核通过。旧四次报告重建后，`summary.json`、`runs.tsv`、`pairs.tsv`和`REPORT.md`与已有交付内容完全一致。
- 用显式模拟数据验证新四次调度、两种顺序、差值符号、两边均采样、缺失／错误候选状态和首个失败即停止；原官方0通过2失败的状态检查没有改变。模拟报告仅用于布局和字段检查，已标记SYNTHETIC，不是候选实测数据。

初轮一个旧测试仍预期当前开启配置拒绝新选项，已将该断言限定为关闭采样，新增受限候选及不兼容范围检查。保留初轮日志，没有为了测试修改原任务结果或放宽失败规则。

[脱敏实现证据](evidence/grpo-local-vmstat-implementation-20261101.json)及`.artifacts/grpo-local-vmstat-implementation-20261101/`保留检查、旧结果复核、代码快照和模拟报告。原接入前的[20项拒绝位置记录](GRPO_LOCAL_BOUNDARY_VMSTAT_OFFLINE.md)按当时版本保留；当前核对工具已更新，不再复现旧拒绝。

## 下一步：固定两对正式验证

先重新检查实际负载、可用磁盘严格>54 GiB及MemAvailable>64 GiB；来源释放后、分配16 GiB运行卷前仍严格>42 GiB，原运行中保护和失败现场保留规则不变，不预先删除其他资料。

按上述入口完成四次：首个正式候选组兼作新路径验证，不额外短测。以原任务及评测保持、两组周期采样完整、448条边界内存／候选vmstat只留表头、一次105输入查询和清理通过、差值可复算为完成条件；保留正负结果，不为更好百分比追加轮数。该计划供检查，本段不自动启动。
