# Xarray本地无代理接入：实现与完整验证

后续已完成[同条件整套采样两对四次](../../results/xarray-local-no-proxy-sampling-controls-20261101/REPORT.md)，工具变化−4.44%／＋2.60%，控制程序变化−1.76%／＋1.74%；真实关闭、原政策／评测、共同查询及清理通过。四次均仅出现原先允许的同一步0→141，诊断PASS与严格／实际退出匹配FAIL分别保留。下面原单轮数据不改写，也不插入新配对。

## 一轮完整任务已验证，三张曲线和成本已交付

2026-10-31按用户明确授权删除唯一旧页级归档`old-page-metadata.tar.gz`后，空间恢复62.320 GiB，完成一轮Xarray #6992完整无代理轻量测量。[结果、三张图及数据](../../results/xarray-local-no-proxy-20261031/REPORT.md)已放在results，图已实际查看。原35步、关键输出、最终补丁、修复前负向对照与修复后不变量检查通过；实际退出检查PASS，SIGPIPE差异0项。额外官方仍945通过／12失败／0跳过，失败项与原参考完全一致；诊断PASS与历史严格FAIL分开保留。

实际缓存／13个本地lower及regctl.real版本检查通过；真实regctl仅`version`、`manifest`，服务／guest代理检查通过，没有镜像／依赖下载日志或17891回退。默认DNS例外保留，未测流量。工具累计35.335秒、冻结重放38.056秒、任务控制程序88.208秒、组内流程131.686秒；组外来源准备151.288秒和组内导入31.607秒分别列出，不重复相加。

host／guest各1300／862个0.1秒点，直接CPU约单核0.297%／0.478%，五次同步采样管理调用0.630秒；本轮无关闭组，不计算减速。current创建前基线11059.578 MiB、峰12198.191 MiB、增量1138.613 MiB，包含服务／控制程序变化；guest内存／Cached峰718.691／309.168 MiB。结束后116个唯一输入驻留10388.641 MiB、组file10393.613 MiB、容量不足0，查询0.191秒，逐页归属未查。

实际写文件边界、只读根、固定8 GiB卷及全部自有清理通过，源服务恢复停止，无活动Docker／Firecracker或新失败卷，收尾62.313 GiB。获准旧归档的恢复能力已明确撤销，清单、报告、日志与近期数据保留；三任务必需备份和另一份旧pair归档不受影响。[删除收据](evidence/old-page-archive-deletion-20261031.json)及[旧恢复说明](STORAGE_CLEANUP_20261029.md)已更新。

本阶段完成，不追加仅创建、同类实跑或四次对照。下一步建议仅离线汇总三任务无代理结果的同口径指标和图表入口，不采新数据。

- [完整脱敏证据](evidence/xarray-local-no-proxy-full-20261031.json)
- 授权清理、驱动、离线复算和交付检查：`.artifacts/xarray-local-no-proxy-full-20261031/`

## 显式入口的离线实现与回归

2026-10-31已完成固定Xarray的`--persistent-input --local-no-proxy`实现，复用原恢复／导入、私有服务、轻量采样和清理流程。Xarray按自己的镜像与任务标识分派，要求原`xarray-approved-sigpipe`，不借用Sphinx标记；原35步、冻结base／testbed解释器分工、独立检查及957项评测选择不变。

新资料只导出3043字节固定清单，不读取缺失的原配置对象或调用Docker。恢复后检查固定缓存配置、JSON元数据、13个不同的本地lower及声明大小、tools，安装时只对`regctl.real`核对ELF和实际版本；旧包装保存但不执行。私有入口仅接受精确版本和固定清单，其余命令退出78，没有旧入口或网络回退。

沿用原新模式的六种代理变量清理和4 GiB／2 vCPU请求；`allowInternetAccess=false`仍保留默认DNS例外。guest已登记清理后、采样和原工具前检查代理环境，结果沿任务、私有服务、流程和报告传递；失败输入保留。Prettier默认接口、Sphinx和未启用选项的旧行为保持，短检查仍仅Prettier。

| 离线检查 | 项数 | 结果 |
| --- | ---: | --- |
| Xarray新资料、真实本地Shell分派、模拟缓存／版本／API／三任务参数分派／代理检查 | 9 | 通过 |
| Prettier原无代理路径 | 17 | 通过 |
| Sphinx原无代理路径 | 11 | 通过 |
| 持久化输入／三任务流程、查询／报告／释放与失败保留 | 31 | 通过，扩展同一检查覆盖Xarray的无代理结果 |
| Xarray原退出及评测政策 | 9 | 通过 |
| Sphinx原任务政策 | 14 | 通过 |

共91项通过，另有8个Python文件语法、两个帮助入口通过。新实现实际导出了3043字节固定清单，镜像摘要与13层检查通过，目录只含清单。正常／失败路径使用玩具文件、模拟API／服务和本地Shell，不触碰真实服务或设备。

离线实现阶段没有恢复、挂载、联网、安装、删除或创建guest，也没有新增性能／内存数据。随后完整任务已验证实际缓存和真实二进制版本，见本文开头；这两项依据实跑，而非小型模拟。

- [实现脱敏证据与日志路径](evidence/xarray-no-proxy-implementation-20261031.json)
- 代码前后副本、差异、检查日志和导出清单：`.artifacts/xarray-no-proxy-implementation-20261031/`

完整验证使用以下入口；离线实现阶段未执行，随后已完成一轮：

```bash
export ASB_AGENTENV_SOURCE_SERVICE=asb-source-rebuilt-20261018T160925Z-2726362
bash scripts/run-host-file-monitor.sh \
  configs/platform-xarray6992-host-current.json \
  --persistent-input --local-no-proxy
```

## 推荐复用已有转换缓存，不下载原配置对象

2026-10-31先完成固定材料、镜像解析源码、原35步及已有结果的离线核对。当时无代理入口明确拒绝Xarray；随后按下述方案补齐显式支持和离线检查，并完成本文开头的一轮正式验证。使用“本地固定清单＋完整转换缓存”方式，没有为缺少原始配置对象补下载。

此前材料核对只读小JSON、源码、命令和文件元数据，并查询本地Docker元数据，没有修改运行代码。后续离线实现已修改上述入口；两段均未恢复归档、读取镜像层或压缩归档正文、联网、安装、启动服务／guest或删除实验资料。

## 本地材料能覆盖什么

| 对象 | 核对结果 |
| --- | --- |
| 固定镜像 | `ghcr.io/epoch-research/swe-bench.eval.x86_64.pydata__xarray-6992@sha256:c05101ef7105599eca1b7d15279a2928ac5453876cd2b83008deb9165d752655` |
| 固定原始清单 | `.artifacts/python-backbone/20261008T153524Z/registry-manifest.json`，3043字节，13层；小清单摘要与固定镜像一致，字节内容与最近成功保存的清单一致 |
| 原始配置对象 | 清单指定8085字节、`sha256:1b79fce561cc98c742d259c9fcf3ec7989749cd1c41ddcd5be8265aa02cebce6`；已查的Docker配置位置不存在，固定镜像本地查询未成功 |
| 原备份 | `.artifacts/storage-isolated-service/20261008T165515Z-3834076/volume.ext4.gz`，3,644,879,815字节，恢复为24 GiB；沿用原完整性通过收据 |
| 保存的输入清单 | 116文件、逻辑10.145 GiB：13份依赖、10份镜像配置、36份索引、36份转换文件、21份远程块记录；转换块目录为空 |
| 固定镜像缓存记录 | 转换配置3566字节、元数据1765字节，在保存清单内；这份旧导入已释放，不是当前现成的展开缓存 |
| regctl入口 | 保存记录为1020字节旧包装，真实可执行文件为12,255,358字节的`regctl.real`；保存的真实文件身份与已验证Prettier原始二进制相同 |

该镜像是Docker schema2格式的普通容器清单，不是多架构索引。`AgentENV/src/image/oci_image.rs:855–918`与后续注释说明后端支持这种格式，先取得原始清单，再按其字节摘要查找转换缓存。当前固定源配置的附加镜像发现列表`try_referrers_overlaybd_prefixes`为空，没有由此增加的查询。

`AgentENV/src/image/resolver.rs:330–388`明确区分两种情况：转换配置可用且元数据存在时直接返回；缺元数据或缓存不可用才继续获取原始配置对象。`image/cache/service.rs:999–1024`和`source_config.rs:96–107、158–205`给出元数据解析及本地层文件／大小检查条件。因此缺少原始8085字节配置对象不必补下载，推荐只提供固定原始清单，缺转换缓存就停止。

本轮没有打开归档检查配置正文或执行真实版本查询。后续仍须在原恢复／导入完成后检查：固定配置及元数据可解析、13个不同的本地lower（只读层文件）和声明的大小正确、没有远程引用、tools完整；仅对恢复后的`regctl.real`核对ELF格式和实际`v0.11.5`版本。旧包装保存但不执行，不作回退。

## 原35步没有显式下载，但退出政策与Sphinx不同

已逐条对照原采样命令和执行清单，多行命令只解码查看、未执行，35步完全对应：

| 类别 | 步数 | 说明 |
| --- | ---: | --- |
| 本地Shell／Git读取 | 10 | grep、sed、源码及本地git diff等 |
| 本地Python探测 | 14 | 导入已有numpy／xarray／pandas，检查数据结构 |
| 环境查找／列举 | 2 | 第3步查解释器和旧环境目录；第7步conda env list及本地find |
| 本地修改 | 3 | 第16步改源码，第29、33步改测试 |
| 本地pytest | 6 | 第18、19、28、31、34、35步，使用已有testbed环境 |

没有显式镜像拉取、Git clone／fetch／pull、curl／wget或pip／conda安装。`conda env list`是列举，不是创建环境。独立检查及官方评测也只操作本地源码、测试补丁和pytest；原957项选择与测试ID补全检查保持，不安装依赖。导入包、测试辅助代码及包管理器的间接请求未逐项展开；未来仍清除代理并请求默认拒绝出网，保留DNS例外，缺依赖就失败，不改命令补装。

Xarray必须继续用`xarray-approved-sigpipe`，不是Sphinx的`pipeline-v2`：只有全局第8步`trace_index_state/002`在原命令及955字节标准输出身份完全匹配时，才接受原有0／141差异。不能将此例外扩到其他动作。原退出1为第1、5、6、10、21、23、24、26、27、31、34、35步；第3步退出127，均保留。

原命令的冻结Shell将PATH设为`/opt/miniconda3/bin:…`并清除三个Conda激活变量；原命令中明确指定testbed解释器的地方不变。独立负向对照和修复后检查使用`/opt/miniconda3/envs/testbed/bin/python`。不能为了无代理而统一解释器、修复原base环境导入失败或升级依赖。

最近同口径完整任务的诊断状态PASS，35步退出检查PASS、获准差异0项；`strict_replay_status=FAIL`是该诊断模式保留的历史状态。额外官方957项仍为945通过／12失败／0跳过，与固定参考完全一致。独立检查是`reset_index`修复前负向对照和修复后不变量检查，不是Sphinx的七例C++检查。未来应分别报告这些结果，不能以流程通过替代任务通过。

## 最小改动：扩展现有入口，不新建执行框架

1. `prettier_no_proxy.py`增加固定Xarray资料：独立镜像摘要与3043字节清单、13层、只提供清单、版本源为`regctl.real`。包装只接受精确`version`和该固定镜像的`manifest get --format raw-body`；配置对象、镜像复制、其他镜像和未知命令退出78，不执行旧包装或联网回退。Prettier默认参数和Sphinx行为保持。
2. 放行显式的Xarray完整持久化输入／current／轻量模式，要求原`xarray-approved-sigpipe`。私有服务根据实际`python_task_profile=xarray6992`及`python_diagnostic_continuation`选择Xarray，不能落入现有Prettier默认分支，也不能借用Sphinx标记。
3. 冷创建辅助函数仅增加固定Xarray的4 GiB请求，沿用2 vCPU、六种代理变量置空和`allowInternetAccess=false`。未启用选项的原请求不变。
4. Python执行器的新模式检查必须允许Xarray原有`diagnostic-continuation`，同时要求完整、持久采样、轻量、无checkpoint／缓存干预／仅评测。现有Sphinx条件不变。复用任务及采样前的guest代理检查，并将结果沿任务、私有服务、流程和报告传递；失败仍按原顺序清理并保留输入。
5. 更新帮助与支持范围；`--local-no-proxy-check`仍仅Prettier。原35步、两个Python环境的职责、精确退出政策、957项选择及原清理保持。

两任务虽然都是13层，但固定摘要、元数据和任务政策不同。只改层数或将Xarray标成Sphinx会走错检查，不能作为接入方法。

## 离线完成条件与后续验证记录

上述离线修改和必要小型回归已完成：

- 精确版本／固定清单成功，blob／copy／其他镜像等退出78，无网络回退；缺元数据、层文件或真实二进制在服务前停止。
- 使用真实平台分派与原生参数解析器检查Xarray、Sphinx、Prettier；错误任务、短检查、checkpoint、关闭采样等模式继续拒绝。
- 创建请求与代理环境正确，guest已登记清理后、采样／工具前检查；坏检查结果不能继续报告成功或释放失败输入。
- 原35步、单一SIGPIPE条件、base／testbed解释器分工、独立负向／不变量检查和957项评测保持，未启用新选项的旧调用保持。

上述离线检查通过后，一轮4 GiB／2 vCPU的原35步及957项评测已完成，第一轮正式任务验证新路径，没有增加仅创建或四次对照。0.1秒host current、guest内存／Cached曲线、工具时延、采样成本、结束后全部116输入mincore、写文件边界和自有清理均有结果；原官方945通过／12失败及历史严格FAIL分别保留。本轮不计算共享收益或采样减速率。

离线实现收尾时约60.980 GiB可用磁盘，未达准备前严格>62 GiB；随后用户明确批准上述归档删除，启动前实际62.320 GiB，收尾62.313 GiB。24 GiB来源、12 GiB输入预算、8 GiB运行卷、MemAvailable>64 GiB和动态保护保持，未降低门槛或删除其他未批准备份。

- [本次脱敏证据](evidence/xarray-no-proxy-offline-20261031.json)
- 原始核对脚本、完整35步与小清单：`.artifacts/xarray-no-proxy-offline-20261031/`
- [已有Xarray同口径完整结果](HOST_PERSISTENT_XARRAY_VALIDATION.md)
- [已验证的Sphinx无代理路径](SPHINX_LOCAL_NO_PROXY_PLAN.md)
