# Cython轻量入口：离线接入记录

最新结果：两份失败卷均已获准定向删除，卷外记录保留；nft修正版已完成原83步、测试、两端曲线及全部清理，见[完整测量报告](EXTERNAL_CYTHON_LIGHT.md)。以下保留本阶段的过程与当时结论。

后续首轮已尝试：镜像与创建通过，止于guest出网检查；原83步未开始。原方案漏看host INPUT的访问拒绝，见[首轮记录及调整](EXTERNAL_CYTHON_VALIDATION.md)和[实际legacy／nft差异修补](EXTERNAL_CYTHON_RELAY_FIX.md)。下文保留离线阶段的实现与验证范围。

## 已接入原83步、105份输入和专属验收

已新增[`configs/external-cython-light.json`](../../configs/external-cython-light.json)，固定同伴仓库提交、原83步、镜像及2 vCPU／8192 MiB配置。修改4个现有运行脚本，并增加一个代理生命周期辅助模块；采样器和采样频率不改。

真实公开输入已加载核对：83份Shell、20份读取辅助Python、2份payload，共105份，全部按原内容和`/probe/input`引用保存。独立Bash每步从`/app`开始、umask 077，保持原命令、默认管道语义及预期失败，不提前克隆、导入NumPy或安装依赖。

Cython通过需要以下证据同时满足：

- 83步顺序、参考退出及有效时间戳保持。预期41／57／68／72步=1、78步=129，其他为0。
- 第76步原NumPy 2.3.0及安装包示例断言成功；第83步最终版本仍为2.3.0。
- 第82步的源码目录和安装包目录各有一份18 passed，保留两个原上下文标题，没有失败或错误堆栈；不能只看管道退出0。
- 原附加验证命令逐字保持，从`/tmp`导入三个扩展，实际`__file__`均为`.so`，记录源码提交；Python备用实现不会通过。

新任务收据使用`functional_validation/test_validation/extension_validation=PASS`。它没有额外执行语法编译，故`syntax_validation=NOT_REQUESTED_ORIGINAL_EXTENSION_CHECK`；官方测试字段明确为原两轮18项测试及扩展检查，而不是隐藏测试。原Django／C4的字段含义不改。

## guest代理复用同伴实现，外层负责清理

主机现有HTTP代理只监听loopback。已保存的实际私有服务配置还禁止guest访问`172.16.0.0/12`等私网，AgentENV静态拒绝规则先于用户允许规则。因此不能直接用`docker0`地址，也不放宽这些规则。

新[`external_guest_proxy.py`](../../scripts/external_guest_proxy.py)复用固定仓库的`agentenv_workload_proxy.py`作为TCP转发程序。真实运行时，从同伴原方案的三个文档测试地址中选择未被当前地址／路由占用的一份，临时添加`/32`到`lo`，只在该地址监听随机端口，转发至现成host loopback代理。不修改原服务、永久网络设置或egress禁用列表；转发程序保留同伴的来源网段限制，不记录请求内容。

代理由**host外层工作流程**持有，日志和地址收据在运行卷外。内层控制程序失败或超时，外层仍在`finally`终止自己启动的进程，再删除仅本次成功添加的地址；不删除预先占用的地址，不泛化杀进程。地址添加失败时没有删除权，代理启动中途失败也清理本次地址。清理失败将整轮判失败，卷外`guest-proxy-address.json`和日志用于定位与恢复。

Cython的guest环境文件只设置本次代理、原Git HTTP/1.1及原线程／输出环境；不写全局Git配置。上传后先做GitHub仓库页面和PyPI索引的HEAD检查，只查连通性，不获取源码／包或预热任务数据，然后才启动采样和业务计时。实际guest出网尚未实跑验证；准备时间及代理内存属于整组记账，不宣称零开销。

Django和C4不启动该代理，不增加地址或网络检查，生成程序与修改前逐字一致。

## 超时只对Cython增加，保护条件不变

| 层次 | Django／C4原值 | Cython新值 |
| --- | ---: | ---: |
| 整段工具调用 | 240秒 | 900秒 |
| 含冷创建的控制程序 | 720秒 | 1800秒 |
| 隔离运行等待 | 1050秒 | 2100秒 |
| 外层systemd运行等待 | 1200秒 | 2400秒 |

实际调用点已统一使用任务对应的预算，不只改单个timeout。未来单轮本地驱动使用2700秒；它是截止上限，不是预计耗时或独占窗口。现有guest采样器1800秒生命周期、0.1秒两端采样及默认回收保持；没有PSS、冻结、根扫描或周期mincore。

服务卷仍为32 GiB，分配前磁盘严格>58 GiB、MemAvailable>64 GiB及原失败保留／定向删除规则不变。未锁定的Git分支和pip依赖不被静默改写；真实运行如有漂移，按首个错误区分平台、依赖和业务问题，不放宽结果要求。

## 51项本地检查通过，真实网络与任务仍待下一轮

- Cython新增11项检查：105输入完整性、读取顺序及独立Shell、管道伪成功／版本漂移、原`.so`断言、实际调用点预算与成功条件、代理环境、地址与进程生命周期、启动失败、内层控制程序各类失败、外层超时与清理失败。
- 旧外部任务13项和原隔离存储27项检查通过。
- 固定公开105份Cython输入逐字及Shell语法核对通过；附加扩展命令与公开原文一致。旧Django28份、C4 45份输入也通过，两个旧任务的生成程序与修改前逐字相同。

代理和CLI测试均使用替代对象；本地Shell测试只运行临时小脚本，扩展检查用替代模块验证`.py`会失败。本轮没有启动服务／guest或代理进程，没有添加实际地址、访问网络、安装依赖、执行真实83步，也没有新曲线。没有把本地通过记作Cython任务已通过。

- [脱敏证据](evidence/external-cython-implementation-20261030.json)
- 源码前后快照、仅本轮差异、测试及公开输入核对：`.artifacts/external-cython-implementation-20261030/`
- 测试：`tests/test_external_cython.py`、`tests/test_external_workload.py`、`tests/test_isolated_storage.py`
- 近期有效实跑：[Django、C4和Prettier upper汇总](RECENT_LIGHT_RESULTS_20261030.md)

## 下一步：一轮83步真实验证

建议在源服务、磁盘、内存及实际负载复核后，先验证固定镜像清单和linux/amd64，再运行一个新guest。该轮会临时创建上述自有代理地址，并在结束时清理；guest出网检查通过才执行原83步。

源服务按既有流程就绪后，调用：

```bash
export ASB_AGENTENV_SOURCE_SERVICE=asb-source-rebuilt-20261018T160925Z-2726362
bash scripts/run-external-workload-light.sh \
  configs/external-cython-light.json \
  .artifacts/workload-and-upper-20261030/upstream
```

完成条件为原83步退出和有效计时、第76／82／83步及三个编译扩展证据、两端0.1秒曲线和采样成本、报告、32 GiB边界以及guest／服务／代理进程／临时地址全部清理通过。冷创建与工具用时分开，曲线复制到`results/`并查看。一轮后结束，不追加相近短测，不重跑已完成的Django、C4或Prettier；本轮离线接入后不自动实跑。
