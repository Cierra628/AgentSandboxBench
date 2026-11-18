# C4轻量入口：离线接入及验证范围

后续进展：[一轮真实36步验证已通过](EXTERNAL_C4_VALIDATION.md)，时延、曲线及往返结果已交付。本页记录此前的离线实现。

## 已能按原36步生成完整输入和执行程序

C4重分片任务已接入现有轻量入口，配置为[`configs/external-c4-light.json`](../../configs/external-c4-light.json)。固定同伴仓库提交、镜像、36步及2 vCPU／8 GiB设置沿用[离线核对](EXTERNAL_C4_OFFLINE.md)。本轮完成代码与本地验证，没有启动服务／guest、访问网络、执行真实任务或采集新曲线。

改动只涉及4个运行脚本：

- `external_workload.py`：除原Django外，仅接收已核对的C4任务；识别9步write及随附内容，缺失即拒绝。按原路径生成36份脚本和9份payload。
- `run_external_workload.py`：上传完整输入，C4使用`/probe/input`，每步从`/app`启动独立`/bin/bash`，保持`umask 077`、默认管道退出语义和原脚本。前置检查只确认样本目录和Python／uv工具，不预读样本；计时程序仍先设置执行权限并试读。
- `run_host_external_workload.py`：按配置验收36步，使用C4图标题，继续复用host总记账报告。
- `validate_isolated_agentenv.py`：按所选任务核对动作数、身份及结果；C4还必须具备往返验证PASS，原卷边界和清理检查不变。

采样保持host／guest每0.1秒、`memory.current`主指标及原分项、guest meminfo；不新增PSS、周期mincore、冻结、全根扫描或回收设置。host范围仍包括准备、控制程序、私有服务与guest，不改成任务进程独占量。

## C4通过条件包含原预期失败和往返证据

36步退出码与所选主记录的`steps.csv`比较：第3步=2，11／12／16／18步=1，其他为0；不提前修好失败的业务代码。所有时间戳及顺序、持续时间差值必须有效，缺失时间不会记作0。

在退出码一致后，读取本轮已下载的工具日志：

- 第33步必须包含`CONSTRAINT VIOLATIONS: NONE`和`FINAL VERDICT: BYTE-EXACT ROUNDTRIP ON c4_sample`。
- 第34步必须包含`9898 files, structure + content`。
- 原结尾检查仍要求两个脚本存在且`python3 -m py_compile`成功。

第35步原业务会删除临时输出；验收使用已保存的第33／34步结果，不要求清理后的目录仍在，也不额外重做一次全量数据读取。收据中`roundtrip_validation=PASS`表示固定样本的往返、结构和约束证据通过，`official_tests=NOT_RUN_ORIGINAL_SAMPLE_ROUNDTRIP_ONLY`说明未执行官方隐藏测试。

Django保留原上传路径、持续Shell、源码／Conda检查和语法验证。生成的2步测试脚本及实际28步脚本均与修改前逐字相同，原配置未变。

## 本地验证范围与结果

13项外部任务检查和27项原隔离存储检查通过。新增检查覆盖：完整payload上传、缺失拒绝、独立Shell工作目录／变量不跨步、文件权限、预期非零退出、计时失败、缺失往返证据、任务名称／动作数及C4图标题。

控制程序使用替代的CLI和采样器，分别验证Django／C4通过路径，以及C4输出缺失、退出码不符和时间戳缺失的失败路径；这些失败均仍执行自有guest删除和列表检查，未调用真实服务。本地Shell行为用临时目录中的小脚本验证，不运行C4业务。

另外，使用固定公开主记录实际加载并生成45份C4输入，逐字核对脚本／payload，再用`bash -n`检查36份脚本及生成程序；Django的28份输入及生成程序也通过。没有执行这些公开脚本。它们证明输入和调度代码已接入，不替代新镜像下载、guest执行和真实采样的验证。

证据与代码位置：

- [脱敏验收记录](evidence/external-c4-implementation-20261030.json)
- 本地快照、仅本轮差异、测试日志与输入核对：`.artifacts/external-c4-implementation-20261030/`
- 测试：`tests/test_external_workload.py`及原`tests/test_isolated_storage.py`

## 首轮真实验证的范围（已完成）

建议下一次正式验证先取固定镜像清单、确认linux/amd64，再分配32 GiB私有卷；启动前重查磁盘严格>58 GiB、MemAvailable>64 GiB及实际负载。失败卷保留及定向删除规则不变，不自动清理或降低门槛。

以下命令会真实创建服务及沙箱，不是离线检查。固定源服务须先按既有流程就绪，上游目录须在固定提交：

```bash
export ASB_AGENTENV_SOURCE_SERVICE=asb-source-rebuilt-20261018T160925Z-2726362
bash scripts/run-external-workload-light.sh \
  configs/external-c4-light.json \
  .artifacts/workload-and-upper-20261030/upstream
```

完成条件为原36步退出与往返证据、语法检查、有效时延、两端0.1秒曲线及采集成本、报告、32 GiB边界和全部自有清理通过。将冷创建与业务耗时分开，图复制到`results/`并查看；一轮通过即结束，不追加相近短测或多沙箱共享率／采样关闭对照。这一步已完成，结果见[真实验证报告](EXTERNAL_C4_VALIDATION.md)。
