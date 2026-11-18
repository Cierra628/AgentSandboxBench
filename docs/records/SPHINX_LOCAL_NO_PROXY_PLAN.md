# Sphinx本地无代理接入：实现与完整验证

后续已完成[同条件整套采样两对四次](../../results/sphinx-local-no-proxy-sampling-controls-20261031/REPORT.md)，工具增加1.18%／0.71%，控制程序增加2.56%／5.67%；真实关闭、原政策／评测、共同查询及清理通过。本报告下面的原单轮数据保留，不插入新配对。

## 一轮完整任务已验证，曲线和成本已交付

2026-10-31用户批准删除指定的重复Prettier展开输入后，空间恢复60.983 GiB，执行了一轮Sphinx完整无代理轻量测量。[结果、三张图和数据](../../results/sphinx-local-no-proxy-20261031/REPORT.md)已放在results，图已实际查看。原35步、七例独立检查、最终源码／补丁和自有清理通过；原第35步25项通过，额外官方24通过／1失败保持，失败仍为`test_expressions`。诊断PASS与历史严格重放FAIL分开保留。

实际缓存／13个本地lower和regctl.real版本检查通过，真实regctl仅`version`、`manifest`，服务／guest代理检查通过，无镜像／依赖下载日志或17891回退；默认DNS例外保留，未测网络请求量。工具累计19.428秒、任务控制程序31.920秒、组内流程67.593秒，组外来源准备115.558秒另列。

host／guest各660／300个0.1秒点，直接CPU约单核0.302%／0.776%，五次阻塞管理调用0.534秒；本轮无关闭组，不计算减速。current创建前基线9115.898 MiB、峰9602.852 MiB、增量486.953 MiB，仍含服务与控制程序变化；guest内存／Cached峰291.832／120.039 MiB。结束后116个唯一输入驻留8501.008 MiB、组file8504.523 MiB，容量不足为0，查询0.180秒，逐页归属未查。

实际写文件边界、只读根、固定8 GiB卷和全部清理通过，源恢复停止，无活动Docker／Firecracker或新失败卷，收尾60.975 GiB。只删除获准的旧展开输入和原成功流程的本轮临时资源，备份／清单／日志保留。本阶段完成，不追加仅创建或同类轮次；下一步建议仅离线核对Xarray本地材料与接入差异。

- [完整脱敏证据](evidence/sphinx-local-no-proxy-full-20261031.json)
- 授权清理、驱动及离线复算：`.artifacts/sphinx-local-no-proxy-full-20261031/`

## 显式入口的离线实现与回归

2026-10-31已完成下面方案的离线实现。新增的Sphinx路径复用现有持久化输入、私有服务、冷创建、轻量采样及清理流程；只有显式启用`--persistent-input --local-no-proxy`才生效。Prettier原调用方式和未启用新选项时的行为保持；随后[Xarray也已补齐独立显式模式并完成一轮完整验证](XARRAY_LOCAL_NO_PROXY_PLAN.md)。`--local-no-proxy-check`仍仅允许Prettier，Sphinx在服务准备前拒绝该选项。

Sphinx只导出固定原始清单，不调用Docker取得缺失的原配置对象。恢复后检查固定缓存配置、元数据、13个不同的本地lower和tools；缺文件、远程引用或已声明的文件大小不符时停止。安装私有入口时只执行`regctl.real`的精确版本查询，要求原ELF版本为`v0.11.5`；旧包装保存但不执行。新包装仅允许精确版本和固定清单查询，其余请求退出78，没有旧入口或网络回退。

外层、systemd任务、私有服务及任务子进程清除六种代理变量；新4 GiB／2 vCPU请求清空guest代理并设置`allowInternetAccess=false`。guest登记到清理清单后、原工具及采样启动前，使用原testbed Python检查代理环境。检查结果和实际regctl命令类别传到私有服务收据、流程及报告；缺少通过证据则失败，不跳过检查释放输入。AgentENV默认DNS例外仍保留，该选项不表示完全断网。

离线验证共82项通过：

| 范围 | 项数 |
| --- | ---: |
| Sphinx新模式、真实本地Shell分派、模拟缓存／创建／代理检查及失败处理 | 11 |
| Prettier既有无代理路径 | 17 |
| 持久化输入、三任务分派、查询／报告／释放及失败保留 | 31 |
| Sphinx原退出政策与评测检查 | 14 |
| Xarray原输入与任务政策 | 9 |

另有9个Python文件语法、两个帮助入口通过。新实现实际导出了本机3045字节固定清单，OCI摘要和13层检查通过；没有导出原配置或调用Docker。模拟结果验证了传参和顺序，不替代恢复后的真实缓存、`regctl.real`版本、服务／guest或完整任务验证。本段没有恢复、挂载、联网、构建、安装或清理，也没有新增曲线及性能数据。

- [本次脱敏证据与各项日志路径](evidence/sphinx-no-proxy-implementation-20261031.json)
- 代码前后副本、差异、原始测试日志和导出清单：`.artifacts/sphinx-local-no-proxy-implementation-20261031/`

完整验证使用以下入口；离线实现阶段未执行，后续已按上节完成一轮：

```bash
export ASB_AGENTENV_SOURCE_SERVICE=asb-source-rebuilt-20261018T160925Z-2726362
bash scripts/run-host-file-monitor.sh \
  configs/platform-sphinx7590-host-current.json \
  --persistent-input --local-no-proxy
```

## 推荐复用转换缓存，不补下载原始镜像配置

2026-10-31完成本地材料、源码和原35步的离线核对。推荐沿用现有持久化输入流程，仅为固定Sphinx镜像提供本地`version`和`manifest`查询；其他镜像请求失败即停止，不执行旧regctl包装或联网回退。

先前材料核对没有修改运行代码或启动服务；随后离线实现与小型模拟已完成，见上节。下一阶段建议一轮原任务，不额外增加仅创建验证。

## 本地已有材料及剩余条件

| 对象 | 核对结果 |
| --- | --- |
| 固定镜像 | `ghcr.io/epoch-research/swe-bench.eval.x86_64.sphinx-doc__sphinx-7590@sha256:577ebfcce825e4c9621e066dc1c8375443be94bbc6de30b9368e422b8f123731` |
| 原始清单 | `prepare-20261009T041720Z/image-manifest.json`，3045字节；小型OCI摘要核对与固定镜像一致，13层 |
| 原始配置对象 | 清单指定8123字节、`sha256:78cd6b990e7cd091922255ebf5fd9eb110ed407198bb9e80cf4df2cda482c5d6`；在已查Docker配置位置不存在，固定Docker镜像也不存在 |
| 备份 | `storage-isolated-service/20261009T050703Z-3934019/volume.ext4.gz`，2,862,171,516字节，恢复20 GiB；原完整性收据PASS |
| 备份内保存清单 | 116份输入，逻辑8.301 GiB，包括13份依赖、10份镜像配置、36份索引、36份转换文件和21份远程块记录 |
| 固定镜像的缓存记录 | 转换配置3566字节、元数据1781字节，均在保存清单中；之前成功导入已释放，当前没有展开的Sphinx输入 |
| regctl | 入口是1028字节旧包装；另有12,255,358字节`regctl.real`，保存摘要与已验证Prettier原始ELF二进制（Linux可执行文件）相同 |

只核对了文件元数据、小JSON和既有收据，没有读取归档或镜像层正文，也没有重新校验大文件。后续仍执行原备份恢复、完整性校验和116份新文件导入，不把旧收据代替未来恢复后的检查。

原始配置对象缺失不必成为下载理由。源码`AgentENV/src/image/oci_image.rs:855–918`先读取原始清单，按其字节摘要选择转换缓存；本镜像是普通OCI容器镜像格式，不带额外仓库范围。`image/resolver.rs:315–388`在转换配置可用且本地元数据存在时直接返回，只有元数据缺失才读取原始配置对象。当前固定私有服务的附加镜像发现（referrer）列表为空，不增加其他镜像查询。

恢复后的配置正文尚未读取。执行前必须确认固定配置和元数据能解析、13个不同的本地lower（只读数据层文件）齐全、没有远程引用，tools及真实regctl也齐全。源码按每个OCI层生成一个lower；缓存不可用、缺元数据或尝试`blob get`／`image copy`时，明确失败，不下载缺失对象。

## 原35步没有显式下载或安装

原采样命令与实际执行清单逐条一致，分为：

| 类别 | 步数 | 内容 |
| --- | ---: | --- |
| 本地读取／检索 | 19 | ls、grep、sed等 |
| 本地Python解析 | 9 | 加载已有Sphinx源码及解析器 |
| 本地修改 | 4 | 原补丁及相应本地探测 |
| 环境列举 | 2 | 第11步找Python和docutils；第12步conda env list、pip list |
| 本地pytest | 1 | 第35步用已有testbed环境执行C++测试 |

`pip list`不是依赖安装，`conda env list`不是创建环境。包管理器内部、导入代码和测试是否有额外请求未在本段验证；未来保留出网限制和默认DNS例外，缺依赖即失败，不自动补装或加离线参数改写原命令。

必须保留原Python路径和冻结Shell环境。第9、10、12、13、34步的预期退出1，以及第1、14步的原141，都属于原任务记录。继续使用`configs/platform-sphinx7590-host-current.json`的`sphinx-pipeline-v2`：只有原两个管道动作在命令、输出和相应补丁满足已有条件时允许141／0，不能给其他动作新增退出例外。

独立检查是七个原始C++用户自定义字面量（UDL）输入的解析、输出回转和ID生成。原第35步输出为25项通过；之后应用官方测试补丁的25个选定用例，参考结果是24通过、1失败，失败项为`tests/test_domain_cpp.py::test_expressions`。后续保持这两个阶段分开，并保留历史严格重放FAIL及现有诊断重放政策。

## 最小改动只扩展显式新模式

1. 在现有本地辅助模块中增加固定Sphinx资料，使用3045字节原始清单；不导出缺失的Docker原配置，不新建通用镜像下载器。保留Prettier默认参数、固定资料和已验证行为。
2. Sphinx的regctl仅支持精确`version`和固定镜像的`manifest get --format raw-body`。安装前只对恢复后的`regctl.real`做ELF／实际版本检查，返回其原文；旧包装保存但不执行。未知命令、其他镜像、配置对象及镜像复制退出78。
3. 将Sphinx显式模式接到现有外层准备、平台分派、私有服务和冷创建请求。继续清除六种代理变量；创建请求仍为4 GiB／2 vCPU、`allowInternetAccess=false`，记录默认DNS例外。
4. 在`run_xarray6992.py`的Sphinx新模式中加入工具执行前的guest代理检查，将结果传至私有服务、流程和报告；原35步、两处管道政策、背景文件与测试选择不变。Xarray和普通旧入口不变。

`--local-no-proxy-check`仍限Prettier，不能在Sphinx配置下误入硬编码Prettier镜像的分支。后续正式验证使用Sphinx完整任务，不再单独复制一套短驱动。

## 离线完成条件与后续验证记录

本轮已完成上述显式模式及以下离线检查：

- 精确版本／清单查询成功，配置对象、镜像复制、未知请求拒绝，无旧包装回退；
- 配置／元数据／lower／真实二进制缺失时在服务启动前停止；
- Sphinx新创建请求和代理检查正确，原Prettier和未启用选项的请求保持；
- 原35步、`sphinx-pipeline-v2`、官方25项选择、状态区分和清理顺序保持。

离线实现通过后，后续一轮原35步、七例独立检查和25项评测已完成，见本文开头。工具时延、host current和guest内存／Cached的0.1秒曲线、采样成本、最终输入mincore、写文件边界和自有清理均有结果。116份输入全部保留在测量范围内，没有只导入13个任务lower来缩小基线。原第35步25通过、额外官方24通过／1失败及历史严格重放FAIL分别核对。

离线实现收尾时可用磁盘约55.922 GiB，未达Sphinx准备前严格>56 GiB。以后实跑前仍需>64 GiB可用内存；原20 GiB来源、10 GiB输入预算、8 GiB运行卷及动态保护不变。实际需要清理时另提具体对象并获得授权，本段未申请或执行删除。

- [脱敏证据](evidence/sphinx-no-proxy-offline-20261031.json)
- 原始命令与核对脚本：`.artifacts/sphinx-no-proxy-offline-20261031/`
- [已完成的Prettier无代理测量](../../results/prettier-local-no-proxy-20261031/REPORT.md)
