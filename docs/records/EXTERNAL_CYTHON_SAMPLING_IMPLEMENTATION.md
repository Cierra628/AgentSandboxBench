# Cython两端采样开关：离线实现完成

后续[四次真实对照已完成](EXTERNAL_CYTHON_SAMPLING_CONTROLS.md)，原任务／开关／覆盖和清理均通过。以下记录实现阶段及当时的验证范围。

## 现有顺序驱动已支持Cython

2026-10-31完成[原离线计划](EXTERNAL_CYTHON_SAMPLING_PLAN.md)的实现阶段。新增显式`--task cython`，顺序仍为关闭→开启、开启→关闭，每次原83步，共332步。默认开启、C4默认任务及Django入口保持原行为；两个旧批次的保存汇总用新代码重算，与原JSON逐字段一致。

本次只修改入口分派、对照验收及报告，没有启动服务、沙箱、代理或业务，也没有改变回收设置、频率或实际超时。四次真实对照尚未运行，因此没有新增减速率或内存曲线。

## 关闭的是内存采样，任务与网络准备保留

`run_external_workload.py`、`run_host_external_workload.py`的两个入口及隔离验证现在放行固定Cython关闭模式。沿用原关闭主体，不建host采样线程，不上传／启动guest采样；工具计时、原83步和资源保护保持。关闭组内存及采样CPU标为未测量，不填零。

Cython代理、HEAD检查、nft窄范围允许规则及命中计数仍在两组执行，失败和超时仍精确清理自有资源。模拟检查覆盖关闭不构造采样器、不生成内存报告，以及网络失败、计数不增加、任务失败、错误退出／时钟／测试／扩展、原生超时和代理清理失败。

## 验收使用Cython原证据，不套用其他任务

顺序驱动核对105份业务输入、原runner、83步时间与参考退出；原NumPy 2.3.0示例、两种目录各18 passed和三个`.so`扩展检查均保留。Cython语法检查仍标为未请求，官方隐藏测试未执行；不要求Django语法验收或C4的9898文件结果。还核对HEAD在业务／guest采样前完成、规则命中增加、代理及地址／规则清理收据。

固定镜像复用已有成功记录：linux/amd64、7层、压缩303,214,315字节，无新注册表请求。原单轮实际版本输出和扩展验证已用新解析器读取通过，没有改写原始结果或补造开关字段。

新Cython记录另列HEAD用时及原输出中的源码提交、gcc、Cython／sympy／pytest、vispy、networkx、peewee、appdirs、requests、tqdm、matplotlib、初次planarity和setuptools版本。每对已记录字段不一致时，保留两次原用时和差异字段，变化百分比为`null`；仅匹配的配对参与汇总，全部不匹配则汇总也为`null`。`comparable_pairs`说明有效配对数，不自动替补。未记录的依赖与历史仓库变化仍是对照的不确定性。

报告标题正确使用Cython和83步，保留两对正负变化、逐工具时间、开启组两端曲线、直接CPU和阻塞管理成本；新增字段不改变C4／Django保存汇总的原含义。

## 离线验证结果

- 48项相关测试通过，全部真实CLI、服务、网络及采样变更均被替换为模拟调用；临时Shell测试只操作临时样本。
- 新检查器接受开／关两种模拟结果；拒绝缺失read输入、修改payload、错误测试输出、缺失提交、扩展失败、关闭残留采样和未删除规则。
- 四次顺序分派、332动作计划、现有每次1500秒顺序驱动上限，以及关闭选项贯穿外层／guest的检查通过。
- 五个运行脚本语法检查、两个帮助入口及旧C4／Django保存汇总核对通过。

[脱敏证据](evidence/external-cython-sampling-implementation-20261031.json)；本地记录在`.artifacts/external-cython-sampling-implementation-20261031/`，含修改前副本、测试日志、帮助和离线验收。首次测试发现新测试样本漏写报告输入文件，补齐该临时样本后48项通过；没有因此重跑真实任务。

## 下一步：确认后执行固定四次，不增加组合

待用户确认实跑范围后，可使用以下入口；输出目录必须是新目录：

```bash
out="$PWD/.artifacts/external-cython-sampling-controls-$(date -u +%Y%m%dT%H%M%SZ)"
bash scripts/run-external-sampling-controls.sh \
  --task cython \
  --upstream "$PWD/.artifacts/workload-and-upper-20261030/upstream" \
  --output "$out"
```

每次一个新统计组／服务、8 GiB／2 vCPU guest、32 GiB卷，串行执行。启动前重新确认磁盘严格>58 GiB、MemAvailable>64 GiB、源身份与实际负载；成功清理才进入下一次，失败保留并定位，不自动删除或补轮。默认回收、原动态依赖及原命令不变。

原隔离shell仍在900秒触发TERM、120秒强杀宽限，顺序驱动每次1500秒；声明的较长预算不代表实际可用时间，详见[预算核对](EXTERNAL_CYTHON_SAMPLING_PLAN.md#资源与实际生效的超时)。不在本阶段扩大超时。

第一个正式关闭组作为新路径验证，通过后完成余下三次，不另加短测。完成条件是原业务／退出／版本可比性、真实开关、计时／采样成本与覆盖、卷边界及全部自有清理均有结果；随后用`scripts/report_c4_sampling_controls.py`发布`results/`图及摘要并实际查看。不可比配对保留原因，不拿旧单轮替代，也不为降低百分比追加轮次。
