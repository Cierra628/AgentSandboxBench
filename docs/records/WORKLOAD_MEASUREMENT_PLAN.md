# 多任务测量：已完成范围、分工和待采集数据

## 当前范围（2026-11-10）

[三调度对照图](../../results/eight-task-schedule-plots-20261110/REPORT.md)已按统一索引生成：4张八任务总览和8张单任务图，均有PNG／SVG。展示三次中位数、最小～最大及原始点，全窗口与调度峰值分列，缓存范围和原异常保持。

八种任务均已有GRPO／BPO／TVCache各3次，共72份七沙箱测量记录；71次全流程PASS，另1次C4首轮GRPO为用户批准的清理超时测量复用，原FAIL和143.440秒异常整轮保留。正常整轮统计排除该份，其他获准指标仍按三次统计，不写成72次全流程全部通过。

统一入口为[八任务三调度索引](../../results/eight-task-schedules-20261110/REPORT.md)：[逐次数据](../../results/eight-task-schedules-20261110/runs.tsv)、[描述统计](../../results/eight-task-schedules-20261110/statistics.tsv)及[完整来源和状态](../../results/eight-task-schedules-20261110/summary.json)。只读核对72个不同原始目录、504个最终实例、13617实际／24237逻辑动作，身份、原结果检查状态、采样收据、内存与时间数据和曲线路径可追溯。未重新采样、联网、清理或启动沙箱。

| 任务 | 三调度次数G／B／T | 负载和实际动作／次G／B／T | 结果及原始报告 |
| --- | --- | --- | --- |
| Prettier #6604 | 3／3／3 | 原七角色；189／84／84 | [新版分层九次](../../results/prettier-schedule-layer-repeats-20261107/REPORT.md)；每实例官方0通过／2失败 |
| Sphinx #7590 | 3／3／3 | 原七角色；230／104／104 | [18份Python任务](../../results/python-schedule-matrix-final-20261108T160912Z/OUTCOME.md)；24通过／1失败，严格重放FAIL、原诊断验收通过 |
| Xarray #6992 | 3／3／3 | 原七角色；230／104／104 | 同上；主干／early／late_1为945通过／12失败，late_2／late_3为946通过／11失败；严格重放FAIL |
| Django #16819 | 3／3／3 | 同原28步，18／26步保存；196／64／64 | [完成报告](../../results/django-schedule-matrix-resume-20261109T061236Z/OUTCOME.md)；原补丁和语法，未跑完整官方测试 |
| C4 | 3／3／3 | 同原36步，24／29步保存；252／93／93 | [完成报告](../../results/c4-schedule-matrix-final-20261109T095839Z/OUTCOME.md)；9898文件往返及约束，1份清理超时例外 |
| Cython | 3／3／3 | 同原83步，60／75步保存；581／176／176 | [完成报告](../../results/cython-schedule-matrix-retry-20261109T160946Z-475206/OUTCOME.md)；两种目录各18 passed、版本、三个扩展及最终补丁 |
| Matplotlib #24177 | 3／3／3 | 同原95步，73／87步保存；665／185／185 | [完成报告](../../results/matplotlib-schedule-matrix-resume-20261109T111200Z/OUTCOME.md)；原两轮各60 passed、导入和完整补丁 |
| 大规模文本编辑 | 3／3／3 | 同原50步，33／46步保存；350／113／113 | [完成报告](../../results/text-editing-schedule-matrix-observefix-20261109T125928Z/OUTCOME.md)；原完整输出逐字节比较和两种solve |

后五项是七实例执行相同路径的受控前缀复用，不称原生七分支；三调度实际动作数不同，逻辑动作保持。BPO／TVCache每次2保存6恢复。当前TVCache是本平台确定性前缀树逐子树实现，不含动态容量／淘汰。

主要四类数据为PSS/N、明确对象的host文件缓存观测、任务运行时间、实际工具耗时；同时保留私有服务current／file等曲线和guest meminfo计数。普通0.1秒／周期PSS1秒，默认回收，终点查询一次。PSS是按映射比例分摊的VMM物理驻留；current是组内记账，不自动覆盖组外缓存；mincore只覆盖明确对象；guest Cached不是host物理对应页。逻辑动作和配置容量另列，不当物理占用。

统一主表使用全采集窗口current峰值，调度窗口峰值另列，不能混用；Xarray全窗口峰值在后续独立pytest阶段。Prettier旧表没有同格式单调边界，不估算其调度峰值。Prettier P1为原19文件，Sphinx／Xarray恢复组为26个活跃文件，其余任务按自身层数和活跃副本查询，不拼接跨批字段。

附加缓存严格状态仍有50份普通文件PARTIAL、8份块设备PARTIAL、11份loop UNKNOWN；普通文件自身窗口有66份OBSERVED、6份PARTIAL。逐次已确认小计、未知对象和错误见索引，未知不补零、不同层不相加。Prettier没有后续新增的独立RAM／loop窗口字段，保留未记录；主要指标齐全不等于全部host缓存覆盖。

Django／C4首轮GRPO跨宿主重启复用，缓存起点未统一；Cython三处TLS异常、原管道退出语义及旧失败现场保留。不剔除慢组，不用这72份直接推出采样减速或部署密度因果结论。当前八种任务原九次范围均结束，不追加运行；上游另五项仍由同伴负责。以下单guest记录按各自历史范围引用，不与本节七沙箱测量混算。

## 此前单路径与采集记录

C4已完成[七实例三调度各3次](../../results/c4-schedule-matrix-final-20261109T095839Z/OUTCOME.md)：原第24／29步保存、实际动作252／93／93、逻辑均252。8次新增全流程PASS，另1份首轮GRPO为用户批准的清理超时测量复用，原FAIL与异常整轮单列。周期PSS、current、九层P1、原任务检查及新增运行清理通过；扩展普通文件／块设备的严格未知保留。它是七实例同路径受控负载，不是原生七分支，也不与旧单guest重复或旧密度对照混算。

C4新版轻量采集的[三对交替开关对照](../../results/c4-new-sampling-repeats-20261105/REPORT.md)已完成，逐对工具累计变化中位数+0.32%、任务窗口+0.20%；原值和慢组保留。它不与Prettier七guest完整采集的单对百分比混算。

2026-11-05，Matplotlib和文本编辑各一次完整运行及采集已通过，见[汇总报告](../../results/external-matplotlib-text-20261105/REPORT.md)和[实现记录](EXTERNAL_MATPLOTLIB_TEXT_IMPLEMENTATION.md)。当时只验证可用性；后续原路径重复和Prettier新版九次已完成，当前范围见上节，不将单路径数据当作三调度结果。

指标是否可取、曲线位置、代表性原始目录及采集组合已离线核对，见[8种任务指标索引](../../results/workload-metrics-index-20261105/REPORT.md)；可直接读取[任务表](../../results/workload-metrics-index-20261105/tasks.tsv)或[完整来源JSON](../../results/workload-metrics-index-20261105/summary.json)。所选9份单guest记录中，旧7份未请求终点PSS和初始下层P1；不把其他批次数据拼入这一轮。

## 新版采集已补齐五项

[五项原路径验证](../../results/task-metrics-upgrade-20261105/REPORT.md)已完成217动作：Django、C4、Sphinx、Xarray、Cython各一份完整记录；PSS/N、各任务实际初始下层P1及精确任务窗口均可取。原索引选定的旧记录保留不变，新增数据另节呈现。Sphinx／Xarray仍按原诊断验收，不能记为全部官方测试通过。

## 已实跑8种任务，具体题目为9项

按任务类型计，现有Prettier、Sphinx、Xarray、Django、C4、Cython、Matplotlib、大规模文本编辑共8种；Prettier包含两个题号，因此按具体题目计为9项。

| 任务类型 | 已实跑的具体任务和单路径动作数 | 已有结果检查范围 |
| --- | --- | --- |
| Prettier | #6604：29步；#14400：26步 | #6604保留官方0通过／2失败；#14400为4通过／0失败；另有#6604七沙箱调度验证 |
| Sphinx | #7590：35步 | 按原诊断规则通过，保留严格重放失败及24通过／1失败，不改称全部测试通过 |
| Xarray | #6992：35步 | 按原诊断规则通过，保留严格重放失败及945通过／12失败，不改称全部测试通过 |
| Django | #16819：28步 | 原退出序列、源码和语法检查；未运行完整Django测试套件 |
| C4 | 36步 | 原退出序列、9898文件内容和结构往返、分片约束检查 |
| Cython | 83步 | 原退出序列、两种目录各18项测试、NumPy版本和编译扩展检查 |
| Matplotlib | #24177：95步 | 原退出序列、最终两轮各60 passed、导入及完整补丁一致 |
| 大规模文本编辑 | 50步 | 原退出序列、两种solve方式、完整输出逐字节比较 |

这8种任务都有各自版本下的工具计时与guest／host内存曲线。新版PSS/N、指定下层文件驻留、任务窗口和工具累计的单guest验证及后续八任务七沙箱三调度记录分开保存，当前完成范围见本页开头；旧曲线仍按原采集组合引用，不替换成新版数据。新两项分别使用10／8个实际初始下层，不套19文件。

本机证据入口：`results/platform-acceptance-20261105/REPORT.md`、`docs/records/HOST_CURRENT_TASK_INVENTORY.md`、`docs/records/RECENT_LIGHT_RESULTS_20261030.md`。这些路径记录已有本机结果；本次文档提交不打包运行时镜像和原始日志。

## 上游10个任务的分工

上游仓库为[AgentSandboxWorkloadResults](https://github.com/RyanShen30/AgentSandboxWorkloadResults/tree/cbe767b2aa23ceb59875b1cb0a7a6141c749312a)，固定版本为`cbe767b2aa23ceb59875b1cb0a7a6141c749312a`。以该版本的`provenance/agentenv-runs.json`中`primary`为主记录，不混用旧批次和诊断记录。

| 任务 | 上游task_id | 动作数 | 负责方及当前状态 |
| --- | --- | ---: | --- |
| Django #16819 | `django__django-16819` | 28 | 本项目已接入并实跑 |
| C4 | `reshard-c4-data` | 36 | 本项目已接入并实跑 |
| Cython | `build-cython-ext` | 83 | 本项目已接入并实跑 |
| Matplotlib #24177 | `matplotlib__matplotlib-24177` | 95 | 本项目单guest及七实例三调度各3次完成 |
| 大规模文本编辑 | `large-scale-text-editing` | 50 | 本项目单guest及七实例三调度各3次完成 |
| HF模型推理 | `hf-model-inference` | 11 | 交由同伴适配，尚无本项目实跑结果 |
| PDF.js #20957 | `mozilla__pdf.js-20957` | 5 | 交由同伴适配，尚无本项目实跑结果 |
| Log4j2 | `apache__logging-log4j2` | 10 | 交由同伴适配，尚无本项目实跑结果 |
| 数据集token计数 | `count-dataset-tokens` | 16 | 交由同伴适配，尚无本项目实跑结果 |
| CompCert编译 | `compile-compcert` | 318 | 交由同伴适配，尚无本项目实跑结果 |

原始固定输入位于上游`results/agentenv/<task_id>/runs/<run_id>/`；run_id从上述清单读取。复用`run.json`的固定镜像及资源配置、`replay-inputs.json`的实际脚本和辅助文件、`steps.csv`的参考退出序列、`validation.json`的原检查。上游使用过冻结业务进程和根目录扫描，本项目不搬入这套采集方式。这里的动作数是固定轨迹重放次数，不是新调用模型生成轨迹。

## 每次新任务要采集的数据

以下是Matplotlib／文本编辑两项已实跑验证的单guest采集要求；每项一个新guest、原单路径各一次。Sphinx／Xarray新七角色采用私有服务cgroup，另见PYTHON_SCHEDULE_MATRIX.md，不与本节旧外层实验组的绝对current混算。不自动扩成七沙箱或三调度。普通guest／host计数周期0.1秒，PSS周期1秒，并在任务和结果下载完成后、原结果检查及删除guest之前记录一次终点PSS与文件驻留。周期采样的峰值称为“观测峰值”，不保证捕获所有瞬时峰值。

| 数据 | 如何取得及统计 | 单位与用途 |
| --- | --- | --- |
| PSS/N | 由sandbox ID绑定本次实际Firecracker进程，读取`/proc/PID/smaps_rollup`的Pss；终点同一短窗口求和，再除以实际guest数N。单guest时N=1 | MiB/guest；PSS是按共享比例分摊的进程驻留内存，包含guest RAM和VMM自身映射，不包含全部服务与未映射文件缓存 |
| guest内存曲线 | `/proc/meminfo`的`MemTotal - MemAvailable`，保留组成字段 | MiB；整个guest的非可用内存估计，不是任务进程独占量 |
| guest page cache曲线 | `/proc/meminfo`的`Cached`原值，另存`Shmem`用于解释；保留Buffers等原字段 | MiB；guest层缓存，Cached包含共享内存相关重叠，不与Shmem重复相加 |
| host组内存曲线 | 本次实验cgroup的`memory.current`，并记录创建前基线、全流程和执行阶段观测峰值及中位数 | MiB；部署密度分析的组内总记账指标，必须同时说明cgroup覆盖了哪些准备、控制和服务进程 |
| host文件记账及组成 | 同组`memory.stat`的`file`、`anon`、`kernel`、`shmem`，同时保留`file_dirty`和`file_writeback` | MiB；file是组内文件类记账，包含shmem，不是guest Cached，也不等于某个镜像文件的全局驻留 |
| host指定文件驻留P1 | 通过实际VMM的只读GET `/vm/config`及rootfs配置确认本任务初始下层文件；按设备号:inode去重，终点一次mincore查询，不读取文件内容 | MiB；只覆盖实际清单，不硬套Prettier的19个文件，不当作新读入量或整个沙箱文件缓存 |
| 任务运行时间 | host单调时钟：冷创建请求前开始，到原路径执行及结果下载返回结束；同步保存动作及下载窗口。此前的计时器编译属于控制程序整轮，不属于任务窗口 | 秒；包含创建、准备、调度等，不能与纯工具执行时间混称 |
| 工具调用时间 | 每个实际动作的guest单调计时起止、差值、退出码；保留逐动作值和累计 | 秒；预期非零退出仍计入；并发累计不能直接除以整轮当时间占比 |
| 其他时间 | 冷创建、保存／恢复事件、控制程序整轮及外层调用分别记录，写明边界；无保存恢复则明确记未发生 | 秒；整轮包含检查和清理，冷创建另说明是否含镜像获取与转换 |
| 采集成本 | host／guest采样器CPU、观测时长、有效点数和最大采样间隔；终点PSS／mincore分别记录墙钟和CPU，采样管理调用单列 | 秒及点数；CPU占比和查询时长不当作任务减速率；本阶段不新增采样开关对照 |

MiB为字节除以2²⁰；proc中kB按KiB处理。guest与host时钟分别保存，不直接相减。PSS/N、current、file、mincore和guest Cached范围不同，不加减构造“总物理内存”；current减file也不称为匿名内存，匿名分项直接读anon。

P1前后保存同组file及current读数。若指定文件驻留明显大于file，记录覆盖不足及原始读数，说明可能存在组外记账；两者净差不是已确认的组外容量，不能加回current或当作共享节省。没有净差也不证明全部页均记在组内。upper、额外快照、VM内存后备等未查询对象保持未知，不记零。

本阶段不新增P0冷起点要求、不清缓存、不关闭默认回收、不暂停业务或沙箱、不做周期mincore和全目录扫描，也不额外预读或强制驻留。若新任务需要重新接线PSS或文件身份查询，应先完成接线再判断采集完整，不用旧任务数据补缺。

## 两个本项目新任务如何验收

两项均按上游2 vCPU／8 GiB、单guest固定轨迹准备，实际启动前核对现有磁盘、内存门槛及本地镜像。当前入口已显式接入新两项的执行、结果检查及测量，完整验证通过；旧Django／C4／Cython的默认行为不改。本机镜像和单guest通过不代表新主机环境或七沙箱调度已验证。

| 任务 | 必须保留的执行行为 | 结果检查及完成条件 |
| --- | --- | --- |
| Matplotlib #24177 | 95步、原`/testbed`和Python环境；逐步独立执行，不能将含`exec bash`的脚本直接source进总控制Shell | 全部动作顺序及参考退出码一致；保留中间预期失败；核对最终两轮各60 passed、导入检查及源码补丁 |
| 大规模文本编辑 | 50步，包括42个bash、2个read和6个write；原`/app`目录及完整辅助文件 | 全部动作顺序及参考退出码一致；`/app/output.csv`与`/app/expected.csv`逐字节相同；保留原判题范围 |

每项都须有上述采集数据、实际镜像和VMM身份、清理回执，任务结果与采集是否完整分别判定。任何异常保留原始目录与错误，不因某项慢而剔除，不为凑数据自动重跑。上游已有有效证据先离线使用；第一次完整运行兼作新路径验证，不额外增加短测或重复轮次。

## 同伴五个任务的交接要点

- **HF模型推理：**保留固定模型版本、辅助文件和后台HTTP服务的生命周期，验证正负样例；原任务可用CPU执行。
- **PDF.js：**保留`npm ci`和两次CLI测试，除原目录检查外核对测试输出；5步不代表运行很短，超时按实际任务设置。
- **Log4j2：**原Maven编译成功、命令跳过测试，但原独立检查器退出141。重放、编译产物和检查器状态分别报告，不静默改成全部通过。
- **token计数：**保留依赖约束和辅助输入；原答案79586、实际计数26行，不改称完整大数据集计数。
- **CompCert：**原32 GiB配置超出现有8 GiB创建限制；需要明确的受控扩展、长超时及9处后台完成等待，保留最终编译器编译运行检查，不能用发出后台命令代替任务完成。

镜像与在线依赖优先复用固定本地输入；需要联网时禁用代理作有限直连检查，失败即停止受影响步骤，不使用17891，不改变全局代理。镜像、依赖是否齐全及空间预算仍需各任务执行前检查，不能用上游耗时预测本机耗时。

## 输出和下一步

每次结果放新建的`results/`子目录：简短报告、摘要、逐工具表、guest内存／Cached曲线、host current／file曲线，以及PSS／指定文件P1的对象范围和查询成本。纵轴写明具体物理量及单位；保存配置、源码版本、实际镜像、cgroup路径和成员范围、计时边界、身份链、失败与清理记录的本机证据路径。原始日志、镜像、凭据和状态数据库不上传Git。

单路径适配、八任务原路径重复与Prettier新版九次已分别完成，按各自范围引用。上游另五项由同伴负责，不在本项目重复开发或运行。本项目已完成[Sphinx／Xarray原七角色三调度各3次](PYTHON_SCHEDULE_MATRIX.md)、[Django受控相同路径三调度各3次](DJANGO_SCHEDULE_MATRIX.md)、[C4受控相同路径三调度各3次](C4_SCHEDULE_MATRIX.md)及[Matplotlib受控相同路径三调度各3次](MATPLOTLIB_SCHEDULE_MATRIX.md)；C4批准清理例外及各批缓存未知保持。[Cython](CYTHON_SCHEDULE_MATRIX.md)和[文本编辑](TEXT_EDITING_SCHEDULE_MATRIX.md)也已完成经确认的同路径前缀复用三调度各3次，八任务统一数据入口见本页开头。不把单路径成功当作三调度均已支持，不自动暂存或提交新改动。
