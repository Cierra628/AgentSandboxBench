# 三调度终点mincore文件清单

当前推荐组合、指标名称和实测成本见[简明说明](HOST_METRICS_GUIDE.md)：普通文件仍用mincore，块设备显式用cachestat，TMPFS和loop独立列出。本页各日期记录保留当时的状态，最新实跑结果见末节。

## 旧19文件结果保留，新增结果单独输出

Prettier的完整GRPO／BPO／TVCache原先只查询19个初始下层文件。这个结果仍在`schedule-smoke-metrics.json`的`p1`中；新增的`expanded_files`查询其他可枚举的普通文件，不改写旧结果或历史统计。

显式入口（会启动每种调度一次完整任务）：

```bash
python3 -B scripts/run_schedule_smoke.py \
  --output .artifacts/schedule-expanded-files-新的唯一批次/live \
  --expanded-files --unused-network-pools
```

单次原平台入口可显式增加`--schedule-smoke-metrics --schedule-expanded-files`。扩展选项要求七角色完整监控，不与关闭采样对照组合；不带新参数时保持旧查询范围和默认设置。

## 从实际引用和持有者找文件

查询在原动作及下载结束后、官方检查之前执行一次：

1. 从sandbox ID确认七个VMM，以实际Firecracker drive和rootfs配置确认全部活跃下层及upper数据／索引。恢复新增的快照下层和单用户下层也纳入，不再只选共享或初始下层。
2. 枚举本次私有服务env中的普通文件，包括已关闭但仍保留的快照、缓存、依赖和日志；只枚举目录及身份元数据，不读取负载内容。
3. 枚举该服务cgroup进程的打开文件描述符和文件映射，补足env外及已删除但仍打开／映射的普通文件。映射路径的替代查询也必须匹配设备号:inode，不按名字猜测。
4. 普通文件按设备号:inode统一去重。保留全部类别标签、引用角色和路径；互斥主分类用于合计。初始19文件复用刚完成的原P1，不再查一次。
5. 每个新文件先固定描述符并核对身份，再用现成`mincore_file()`查询。扩展的`file_descriptor`参数只复制调用者已核实的描述符，不改变旧路径入口的`O_NOFOLLOW`行为，也不关闭调用者的描述符。

`mmap(PROT_NONE)+mincore`不读取文件数据页，不暂停沙箱，不预热、不清缓存、不锁页。元数据枚举仍有自身CPU和元数据缓存开销，报告单列墙钟、线程CPU和每个文件的查询时刻；不能声称零扰动。普通计数0.1秒、周期PSS1秒不变，无周期mincore或全宿主目录扫描。

## 完整性用状态表达，不用零替代未知

`OBSERVED`仅表示本次登记范围内的普通文件查询完整。目录遍历错误、指向未遍历目录的符号链接、未解析引用、进程消失／身份或cgroup变化、查询失败，以及文件在清单与查询期间的大小／mtime变化，都会记录并使覆盖状态为`PARTIAL`。变化文件保留当时查询值，但不进入稳定对象小计；失败容量为空。

原任务是否通过与扩展覆盖是否完整分开判断。官方结果、动作顺序和退出码不因覆盖缺项而放宽；报告同时显示原任务状态和覆盖状态，不能只看外层PASS。每个进程记录启动身份与cgroup，并复查本服务进程集合。查询是顺序进行，不能保证消除整个观察窗口中的所有瞬时变化。

Linux在调用者没有文件所有权／足够权限时，mincore可能隐藏真实驻留并返回全驻留位图。这个显式实现先核对host root、完整UID映射及CAP_FOWNER；条件不成立时报错，不把全驻留假象当测量结果。

## 普通文件模式的覆盖边界

- 普通文件以VFS设备号:inode去重。不同文件系统别名是否指向同一底层缓存，不由这个身份规则证明。
- shmem／hugetlb分列，容量不混入磁盘普通文件小计；它们需要各自的驻留、换页或大页口径。
- 仅启用普通文件扩展时，ublk／块设备缓存及外层loop后备镜像仍排除；下文的显式四类模式另行查询它们。不同缓存层不能与普通文件驻留相加当物理并集。
- 已关闭、已解除映射又删除的文件已没有可枚举路径或持有者；服务env之外且未被当前进程引用的旧缓存不在清单内。它们不是零。
- guest的Cached通常在host VMM匿名RAM内，不能被这份host普通文件查询替代。

`file`仍作为cgroup文件记账的独立曲线和对账参照。文件全局驻留超过组内file只能提示范围或时差，需要页记账归属证据才能给出实际组外容量；file较大也不能证明清单无遗漏。current、file、PSS和mincore不相加当总物理内存。

## 验证与结果

`tests/test_host_schedule_files.py`验证初始lower复用、快照／upper、inode别名去重、env外文件、目录枚举失败、文件变化、已删除打开文件、只剩映射的文件、shmem分列、权限拒绝及旧路径行为。测试只用临时小文件和模拟proc，不触碰真实服务或设备。

[三组真实结果与分类](../../results/schedule-mincore-coverage-20261107/REPORT.md)已完成，原19文件均5308.066 MiB，扩展稳定小计分别7202.652／8459.371／8535.375 MiB，新增查询墙钟0.059903／0.104144／0.079628秒。原任务和每实例官方0通过2失败保持，成功资源清理完成；批次`.artifacts/schedule-expanded-files-20261107/live/`。

BPO有8个upper文件在清单登记后、单文件查询前变大，查询读数合计24.906 MiB保留并单列，不进入稳定小计。三组另各14个非普通文件符号链接原误列未解析，保存错误已证实它们不是普通文件；离线分类修正不改原PARTIAL，各组剩余未解析普通文件来源均0。各14个shmem／hugetlb仍单列未知。原始状态、计时和小计不改写。

实跑后仅离线修正非普通文件分类、按全部别名统一快照文件分类、区分登记到查询之间与查询内部的变化，并明确复查进程属于本服务cgroup；全部保存进程通过此新增条件。最终35项相关检查通过，最终收尾改动未另起实跑。原始及收尾源码、资源与验收收据均保留。旧19文件及已有九次报告保持。

## 排除项的最新离线核对（2026-11-07）

[三组排除项报告](../../results/schedule-mincore-exclusions-offline-20261107/REPORT.md)确认：每组8个drive块设备之外，BPO／TVCache还各有2个RAM后备设备，其映射与memory/image.json日志已对应；各14个特殊文件均为TMPFS，并非hugetlb。旧清单缺设备容量／驻留及完整maps字段，不能追补旧时刻。

还需注意：平台允许写入env、run、tmp三处，当前保留文件枚举只覆盖env；另外两处只能覆盖仍打开／映射的文件。扩展方案应补本私有挂载的run／tmp固定目录，而不是扩大成全宿主扫描。loop后备文件驻留及有效dio也未采。上述都是独立于“已登记范围未解析来源0”的覆盖限制。

已有设备mincore、RAM映射识别及有界TMPFS整后备查询可复用。建议先离线接线，再按报告条件确认一次BPO完整验证；本次只核对，没有修改采集器或启动查询，原三组结果不改写。

## 显式四类缓存查询（2026-11-07接入）

仅新模式扩展到本私有挂载的env／run／tmp；普通文件沿用同一设备号:inode表去重。原19文件P1与旧`expanded_files`字段保持，四类结果另存`cache_layers`，不相加成物理总量。

```bash
python3 -B scripts/run_schedule_smoke.py \
  --output .artifacts/唯一批次/bpo-once-唯一次名 \
  --schedule bpo --expanded-files --cache-layers --unused-network-pools
```

`--schedule`不填仍按旧行为各跑三调度一次；本轮只选BPO。底层入口对应`--schedule-smoke-metrics --schedule-expanded-files --schedule-cache-layers`，新参数必须显式提供。

块设备来自实际drive及服务进程FD／完整maps，包含不在drive中的恢复RAM后备。按rdev合并，打开并保持各别名描述符、核对节点身份和BLKGETSIZE64容量，随后用原设备mincore接口查一次；查询前后复查源进程及映射。冷起主干无需有RAM设备，不对其强行调用要求RAM存在的接口。

TMPFS仅把已核实的完整已删除映射交给原有整后备cachestat有界工作进程，失败、交换条目及映射变化保留未知。记录调用线程CPU和工作进程CPU，超时只终止并回收自己的查询进程。外层loop通过实际私有挂载、LOOP_GET_STATUS64后备设备号:inode确认准确volume.ext4，保存有效direct-I/O、offset、sizelimit及查询前后状态；与卷内文件和块设备分层。

不暂停、不预读、不清缓存、不锁页、不加压、不联网。本轮以一次完整BPO作真实验证，不增加玩具验证或三调度重复；未引用的旧缓存及guest Cached归属仍不由这些查询证明。

### 本轮实测结果

[单次BPO报告](../../results/schedule-cache-layers-20261107/REPORT.md)及summary.json已交付。原任务和身份链通过，10个块设备、14个TMPFS及准确loop后备均OBSERVED；普通文件269个对象、稳定8450.063 MiB，4个变化upper单列，普通范围PARTIAL。块设备1162.215 MiB（其中RAM1149.637 MiB）、TMPFS32 KiB、loop6492.395 MiB分层呈现，不合成总量。loop已确认有效dio=false，不能再把该层假定为零。

新增三层终点查询墙钟1.498290秒、调用线程CPU1.407765秒；TMPFS工作进程内查询函数CPU6.827毫秒另计，解释器启动、序列化及退出CPU未测，不能把这几个读数称为整进程总CPU。全部终点墙钟1.904358秒，包含七VMM PSS、原19文件、普通扩展和新三层。原19查询CPU及每个新对象的计时也在原始JSON。本轮无关闭组，不推算减速率。

37项相关离线检查及真实七guest检查通过；成功服务／卷和RAM输入已清理、源服务停止。原始批次`.artifacts/schedule-cache-layers-20261107/bpo-once/`，verification.json在其上级。后续只建议离线解释4个变化upper和成本，不自动追加运行。

本轮离线报告的日志关联检查曾错误地把ublk dev_id当成Linux块设备minor；保存身份表已证实两者不同。已按实际映射节点路径、日志path/dev_id及原采集rdev证据修正，1项报告回归通过，业务未重跑；错误原文和处理在本轮报告单列。

## 查询成本与变化文件的离线结论（2026-11-07）

[成本分析报告](../../results/schedule-cache-costs-offline-20261107/REPORT.md)复用了本轮保存数据，未改变采集代码。块设备1.319786秒中，7个驻留0的64 GiB drive对象窗口占1.000209秒（75.79%）；10个设备总逻辑520 GiB、136314880个页位置。对象窗口还包括身份／别名复核，未单独记录系统调用时间，不能把这个占比当作确定可消除的开销。

4个upper实际在登记后、单文件查询前增大，各自查询内部身份和大小稳定。原1.933594 MiB读数及严格小计8450.062500 MiB、PARTIAL保持；新增离线派生的各文件查询窗口小计8451.996094 MiB，用于区分“登记后变化”和“查询自身失败／变化”，不作为原子快照或误差上界，不改原始JSON。

建议下一步新增显式cachestat块设备方法：查询同一固定对象的完整BLKGETSIZE64范围，保留所有身份检查，按缓存索引中的现有条目统计，避免逐页经过空区。nr_cache没有mincore的uptodate条件，因此需要新的cache_index_bytes字段及方法标签，旧mincore结果和默认保持。块设备的nr_evicted是历史回收条目，不是驻留，也不按TMPFS交换条目处理；具体改动和失败条件见分析报告。实际速度、数值差异及新路径仍待后续验证，本次未实现或实跑。

## 显式块设备cachestat与查询窗口小计（2026-11-07离线接入）

[实现及验证报告](../../results/schedule-cachestat-offline-20261107/REPORT.md)已完成，60项回归、三个入口的参数帮助／传参检查及旧BPO数据兼容核对通过。本轮只离线实现，没有启动服务或真实设备查询；实际成本仍待验证。上节“未实现”指此前成本分析结束时的状态。

### 方法必须显式选择，原默认保持mincore

驱动参数为`--block-cache-method {mincore,cachestat,compare}`，底层任务和私有入口对应`--schedule-block-cache-method`。显式方法必须与四类缓存、普通文件扩展和完整监控选项一起使用；缺少依赖时报错，不隐式开启其他采集。不提供新参数仍走原mincore。

以下是后续获准后的一次完整BPO比较用法，本轮未执行：

```bash
python3 -B scripts/run_schedule_smoke.py \
  --output .artifacts/schedule-block-compare-新的唯一批次/bpo-once \
  --schedule bpo --expanded-files --cache-layers \
  --block-cache-method compare --unused-network-pools
```

`mincore`只执行原方法；`cachestat`只执行新方法；`compare`对每个已固定设备先执行mincore、再执行cachestat，各一次，不是周期查询或同时快照。对象仍来自实际drive及服务进程FD/maps，按活跃rdev去重，完整查询`offset=0,length=BLKGETSIZE64`。所有别名均保留并核实，任一别名身份或容量冲突均使该对象未知；查询前后检查和已打开描述符保持。

### 两个数值不互换，未知不写成0

`cache_layers.block_devices`新增：

| 字段 | 含义 |
| --- | --- |
| method | 本次选择的方法 |
| objects[].queries[方法] | 该方法的完整范围、状态、读数、原始计数、错误及阶段成本 |
| objects[].resident_bytes | 仅成功mincore的uptodate页驻留量 |
| objects[].cache_index_bytes | 仅成功cachestat的nr_cache×基本页大小 |
| method_totals[方法].observed_subtotal_bytes | 该方法成功对象小计；未请求为null |
| method_totals[方法].complete_bytes | 全部登记对象成功且无别名错误时的量，否则null |
| method_totals[方法].status | OBSERVED／PARTIAL／NOT_REQUESTED |
| stable_resident_bytes／stable_cache_index_bytes | 各方法的成功对象小计，不能冒充完整总量 |

cachestat统计缓存索引中已有页，不检查内容是否已准备好；mincore要求uptodate。因此新字段不写入旧resident_bytes，也不把两值合成总量。块设备nr_evicted是历史回收条目，不是当前驻留，不套用TMPFS交换条目条件。原TMPFS的全索引0／0调用、超时和交换处理不变。

查询失败或前后身份／容量／映射变化保留null和错误，不回退。compare中某方法失败不抹掉另一方法的有效结果，但整体仍PARTIAL。未请求mincore时，原驻留字段为null，不是0；成功实测0则保留数值0。报告器兼容旧JSON，并按方法分表，不要求两方法相等。

### 分段成本与新的普通文件小计

块设备保存登记／查询两段成本；每对象每方法保存identity_before／core_query／identity_after的单调时钟起止及调用线程CPU，失败前执行的阶段也保留。core_query包含接口包装，不等同于纯内核耗时。该层总成本包括全部方法及身份检查，compare的总成本不能当作cachestat单独成本。

仅显式四类模式新增普通文件`query_window_regular`与登记／查询／进程复查成本：身份相符且查询自身stable=true的磁盘普通文件，每inode一次，原19文件复用P1；登记后变化但查询自身稳定的对象可进入新小计。原严格小计、变化标记及PARTIAL保持，查询中变化或失败仍未知。新小计不是同一时刻快照，也不包含TMPFS或未知旧缓存。

原BPO记录离线复算新小计8451.996094 MiB，其中4个upper为1.933594 MiB；原8450.062500 MiB及PARTIAL不变，未写回旧JSON。TMPFS完整工作进程CPU仍未采到，既有函数CPU不作为完整进程CPU。

下一步仅建议一次完整BPO七沙箱compare，回答同设备完整范围的新方法是否减小成本、读数差异在哪里；以原任务／身份／去重／清理和两方法独立成本完整为完成条件，不自动实跑或追加重复。

## 双方法真实比较（2026-11-07，仅一次BPO）

[本次报告与逐对象数据](../../results/schedule-block-compare-20261107/REPORT.md)已完成。原BPO84实际／189逻辑动作、2保存6恢复，每实例官方0通过2失败，七VMM／PSS、drive与RAM来源登记和清理通过；新增块设备查询状态为PARTIAL，不能把外层任务PASS当作全覆盖。

相同9个成功设备的mincore与cachestat逐项一致，小计432.656250 MiB。核心查询合计1.196996812／0.001611911秒；含前后身份检查的方法窗口合计1.218382915／0.019418547秒，后者少98.41%。这是一次先mincore后cachestat的顺序比较，不是整轮减速率；共用登记0.085202秒另计，不对未查询设备外推。

第10个设备为late三个分支的恢复RAM，节点/dev/ublkb113、rdev=66313、逻辑容量4 GiB。两方法均在查询前的映射复核报`ValueError('source mapping identity changed')`，均未执行核心查询，驻留保持null；两方法的complete_bytes均为null。原清单48个别名含44段映射，没有保存失败时具体段或变化字段，不能离线确定是否拆分／合并或哪一字段变化，不指定DAMON等根因，不从另一别名补值，也不重跑。

普通范围269对象含255个磁盘普通文件、14个TMPFS，未解析来源0；原严格小计8444.265625 MiB及PARTIAL保持。8个upper登记后变化、各自查询稳定，合计18.589844 MiB；新查询窗口小计8462.855469 MiB，255个磁盘普通文件均有效。TMPFS32 KiB、loop6488.148438 MiB分别有效，不把各层合成总量。

终点全部查询1.969820秒，动作及下载39.194秒、工具累计49.413秒、控制程序56.104秒；无关闭组，不推算任务减速。最低可用盘28.147 GiB，源服务停止，本轮成功服务／卷及RAM输入均已收尾。报告成本汇总补充了未执行阶段的对象数，5项报告回归通过，原始采集与旧结果不变。

批次`.artifacts/schedule-block-compare-20261107/bpo-compare-20261107T053608Z/`；原始业务`.artifacts/storage-isolated-service/20261107T053742Z-3733784/prettier6604/`。下一步建议仅离线补充映射验证错误的具体别名、预期段及实际不匹配字段，保持原拒绝条件；以诊断回归明确且不弱化身份检查为完成条件，不自动追加实跑。默认查询方法仍mincore。

## 映射错误诊断已补齐（2026-11-07，仅离线）

[本轮实现与验证](../../results/schedule-mapping-diagnostics-offline-20261107/REPORT.md)完成，51项相关检查通过，没有启动服务或沙箱。上轮实际失败的映射段／变化字段没有保存，仍不追补，原9／10覆盖、432.656250 MiB小计、98.41%成本变化及PARTIAL不变。

块设备每方法查询失败时，`objects[].queries[方法].failed_reference`记录具体别名／角色／进程；`mapping_diagnostic`记录预期映射、实际映射和`changed_fields`。`FIELDS_CHANGED`表示相同地址段的字段变化；`EXACT_RANGE_MISSING`表示原地址段没有找到，并另列同一次maps读取中与原范围重叠的已解析段，不把拆分或合并后的段自动接受为原段。登记错误在errors／reference_errors中保留reference和诊断，报告器展示这些信息。

共用TMPFS查询在各对象保留mapping_diagnostic和原source_region，工作进程JSON传递也已验证。正常结果不新增这些字段；PID、全部映射字段、设备身份、容量及前后来源检查保持，异常类型和原报错原文不变。失败仍为UNKNOWN／null，查询后失败也只保留原始计数，不赋有效容量，不重试或换别名补值。

诊断仅复用原本的一次maps读取，缺少原地址段时再解析已有文本，没有额外maps读取、数据页读取或等待；新增CPU成本未实测。新增9项模拟回归验证字段变化、地址段消失／拆分／合并、查询前后失败及诊断传递；模拟例不是上轮根因。旧保存数据读回一致，无新增字段时新旧报告输出一致，旧结果不改写。

下一步建议仅一次完整BPO七沙箱，显式`--block-cache-method cachestat`，不再重复mincore比较。问题是全部10设备能否完成原身份检查，以及失败时能否给出具体字段；以原任务／身份／去重／清理及逐对象结果或可定位错误完整为完成条件。普通默认仍mincore，此建议不自动授权实跑。

## cachestat单独查询真实验证（2026-11-07，仅一次BPO）

[完整报告](../../results/schedule-block-cachestat-20261107/REPORT.md)已交付。显式`--block-cache-method cachestat`，10／10个终点登记设备按原身份检查和完整容量查询成功，缓存索引量1112.601562 MiB。两个恢复RAM设备分别436.984375／663.097656 MiB，按实际rdev去重。块设备mincore未请求字段为null／NOT_REQUESTED，普通文件和loop仍用mincore，不将两种方法的量互换。

核心查询合计3.343毫秒、前后身份检查在内30.948毫秒；含登记块设备整层94.861毫秒。新增三层共0.296216秒，终点全部查询0.771335秒，动作及下载37.941秒、工具累计48.062秒、控制程序53.452秒。无关闭组，不计算减速率，不拿不同运行数值当同对象比较。未触发映射失败，本次不宣称真实失败诊断已验证，已有51项离线检查复用。

普通269对象含255个磁盘文件和14个TMPFS；255个文件各自查询窗口有效，小计8433.671875 MiB，8个upper登记后变化33.644531 MiB。原严格8400.027344 MiB及PARTIAL不变。TMPFS32 KiB、loop6449.363281 MiB单列，各层不相加。终点清单完整不代表无路径旧缓存、已释放设备或窗口外对象都已覆盖，也不证明guest Cached物理归属。

报告首次因历史RAM日志多于两条失败：实际109／115两个设备之外，还记录了113／114创建后释放。仅离线修报告，保留全部正向创建记录，仍要求逐一匹配实际RAM设备名和dev_id；7项报告回归通过，原错和前后源码保留，业务未重跑。原始批次`.artifacts/schedule-block-cachestat-20261107/`，业务`.artifacts/storage-isolated-service/20261107T064216Z-3750267/prettier6604/`。任务84实际／189逻辑动作、2保存6恢复、每实例官方0通过2失败及清理通过，最低可用盘28.125 GiB。

本轮完成，普通默认保持mincore。下一步仅建议离线统一常用采集配置的量、方法、成本和覆盖说明，不自动追加实跑。

## 三调度指标分表完成（2026-11-07，离线）

[统一表](../../results/schedule-metrics-inventory-20261107/REPORT.md)把旧三调度普通文件扩展批次作为主表，后续BPO分层验证另列。服务组current峰值、PSS/N、普通文件mincore、块设备cachestat和各时间窗口分别命名，实际cgroup与原始文件路径写入JSON；未采集或未请求均为空，不补零、不借数、不重新查询已销毁对象。5项新报告回归通过，本轮没有启动服务或沙箱。剩余组合仅是GRPO／TVCache的显式分层查询，后续建议各一次完整方法验证，确认前不自动启动；整体host物理覆盖的边界保持。

## GRPO／TVCache显式分层补测完成（2026-11-07）

[最新结果与逐对象报告](../../results/schedule-layer-completion-20261107/REPORT.md)完成GRPO和TVCache各一次完整运行，BPO直接复用早先有效的cachestat单独查询，不重跑、不混成正式重复。原任务、每实例官方0通过2失败、七VMM／PSS、实际drive与对象身份、去重和清理通过。普通计数0.1秒、周期PSS1秒、默认回收和普通入口默认不变。

GRPO8／8设备、TVCache10／10设备缓存索引量分别12.562500／1023.859375 MiB；前者全冷起，无恢复RAM，后者有两个恢复RAM后备，每个由三个分支映射。42个drive配置引用中，14个为块设备引用，28个为已进入普通清单的备用文件，不重复计数。块设备mincore未请求为null。普通各文件查询窗口233／255个均有效，小计7200.789062／8492.453125 MiB；TVCache12个upper登记后变化62.476562 MiB，原严格8429.976562 MiB和PARTIAL不变，GRPO严格为OBSERVED。三组TMPFS整后备和准确loop均另列有效，各层不相加。

块设备含登记整层墙钟26.775／137.996毫秒，全部终点0.596772／0.837941秒；无关闭组，不计算减速率。本轮未触发映射错误，真实失败诊断没有新增验证，复用离线证据。报告器仅离线支持三调度的不同对象数量，9项回归通过；汇总检查器曾把42个drive总引用误要求为14，已按块设备与普通文件分开核对，原错保留，业务未重跑。

批次`.artifacts/schedule-layer-completion-20261107/`保留源码、前后版本、查询／任务／资源核对和原始路径。新增两次最低可用盘44.091／28.051 GiB，成功资源已收尾、源服务停止，另一工作区服务保持。本阶段方法可用性完成，不继续相近校准；无路径旧缓存、已释放对象、窗口外变化、实际组外页归属和guest Cached物理并集仍是独立边界，不能由登记对象有效推成全部host cache无遗漏。

## 新版三调度各三次正式对照完成（2026-11-07）

[9次独立报告与曲线](../../results/prettier-schedule-layer-repeats-20261107/REPORT.md)复用上述显式四类查询组合：普通文件mincore、块设备cachestat、TMPFS整后备及准确loop。9次均保持原任务／身份／去重／清理，实际1071动作、逻辑1701动作、63个guest；普通0.1秒、PSS1秒及默认回收保持，没有清缓存或压力。

普通各文件查询窗口9次均OBSERVED；恢复调度登记后upper变化仍保留原严格PARTIAL。r1-tvcache一个恢复RAM映射在查询前消失，实际重叠段已为匿名映射，诊断EXACT_RANGE_MISSING保留；该设备未知，块设备完整量null。r3-grpo的loop配置、后备身份及大小稳定，文件元数据在查询中变化，驻留未知。上述完整量各仅2／3次，统计和图注明有效次数，成功小计与原始读数另存，未重跑或改变接受条件。

新增`scripts/run_schedule_layer_repeats.py`仅用于固定9次显式组合，默认仅保存计划，`--execute`才运行；复用原单次驱动，每次独立准备及收尾，普通入口默认不变。`scripts/report_schedule_layer_repeats.py`只读保存数据，复用原动作／PSS／身份及逐对象检查，给出统计、28张图和逐对象报告。服务组current、VMM PSS与各缓存层不相加；这些对照没有解决全host旧缓存枚举或guest Cached的物理归属。

本批收尾。下一步仅建议从保存的映射及loop记录离线检查两项缺失，以明确读数适用条件和最小修改方向为完成条件，不自动修改检查或再跑沙箱。

## 新版9次的两个缺项已离线核对（2026-11-07）

[原因、源码和最小建议](../../results/schedule-layer-gaps-offline-20261107/REPORT.md)只复用保存记录。TVCache的原4 MiB文件映射已变为匿名映射，非同一文件单纯拆分；核心查询没运行，无法离线补原始容量。GRPO loop准确身份、大小和配置不变，mtime变化触发旧严格拒绝；已有320.664062 MiB位图窗口观测，只作为新口径候选，不加入旧统计。

内核mincore／cachestat按打开对象缓存索引查询，与文件mtime或某段guest地址是否仍存在是不同问题；但设备可查询不等于所有原来源关系仍成立。两次均在官方检查前拒绝，实际重映射调用和mtime写入者未记录，不指定回收机制。

优先建议显式增加loop查询窗口读数和前后元数据，旧严格状态不改；身份／容量／loop配置／查询失败继续未知。RAM先区分固定设备查询与来源映射状态，不能简单删_validate_region或换别名补值。4项纯字段检查通过，采集器和旧数据没有改动，本轮不启动真实验证。

## loop独立查询窗口已接入（2026-11-07，离线）

[实现及验证](../../results/schedule-loop-window-offline-20261107/REPORT.md)完成，普通入口默认和原严格状态保持。显式四类分层路径自动请求`observe_loop(..., include_query_window=True)`；直接调用该函数不传新参数仍按原行为。mincore的`include_metadata=False`也是默认关闭。

`outer_loop.query_window`记录独立status／resident_bytes／完整range；允许mtime变化，但初始固定身份和容量、查询前后身份和大小、准确loop配置及挂载检查必须通过。只有mtime变化时，新窗口可OBSERVED，旧严格值仍null／UNKNOWN，整体严格分层仍PARTIAL。身份、容量、配置、挂载或查询失败保持新值UNKNOWN，不换别名或重试。

`outer_loop.residency.metadata_before/metadata_after/metadata_changes`复用原两次fstat，给出设备号、inode、rdev、大小和mtime纳秒原值及差异。错误保留原文，新窗口增加具体rejected_checks／failure_stage，mtime单独拒绝旧严格值时为strict_rejection_reason=MTIME_CHANGED。新字段不新增mincore或fstat，也不触碰数据页；字段构造和序列化的真实CPU影响尚未实测。

逐对象报告、指标清单和后续9次汇总分别展示新值；旧数据没有字段时不追补。59项相关检查通过，旧9次值、逐次报告及总JSON／TSV／报告与修改前一致，历史320.664062 MiB和2／3统计不改。没有真实服务／沙箱或实验对象查询，模拟示例明确标为非业务数据。

本阶段已完成。下一步仅建议另行确认后一次完整七沙箱GRPO检验字段、成本及清理，不补跑9次，不为触发mtime变化追加操作。RAM映射来源条件保持，其指标拆分不在本次实现范围。

## loop窗口真实正常路径已验证（2026-11-07，仅一次GRPO）

[本次报告与前后元数据](../../results/schedule-loop-window-live-20261107/REPORT.md)完成189实际／逻辑动作、七VMM／PSS、8设备、原官方每实例0通过2失败及清理核对；没有重跑。完整16 GiB loop的原严格值和独立窗口值均OBSERVED、320.695312 MiB，前后设备号／inode／rdev／大小／mtime相同，新字段及报告真实可用。本次mtime未变，只验证正常路径，不为触发变化增加运行；相关变化拒绝仍由此前离线检查支持。

loop整段墙钟／调用线程CPU0.100062／0.094017秒，全部终点0.544865／0.403909秒；没有分离字段构造和序列化CPU，也没有关闭组，不计算减速率。块设备8／8、12.558594 MiB，普通233文件严格／窗口小计7201.089844 MiB，TMPFS独立有效；各层不相加。

最低可用盘43.728 GiB，成功资源已清理，源服务恢复停止，其他工作区服务保留。普通默认与RAM映射条件保持，旧9次及其两项2／3统计不改。本阶段收尾，不追加同类验证；后续RAM设备查询和映射关系状态的拆分先做离线范围，不自动实跑。

## RAM固定设备查询与来源关系已拆分（2026-11-07，离线）

[实现与验证](../../results/schedule-ram-split-offline-20261107/REPORT.md)新增显式`--split-ram-cache`，底层`--schedule-split-ram-cache`，须有扩展文件、四类分层和显式cachestat。原默认关闭，mincore／compare不自动切换，也不增加原组合的查询次数。

新`block_devices.objects[].fixed_ram_device`保存预选组内open FD、独立query和原source_relationships；优先服务进程、按PID／路径固定选择，不按结果换来源。每个RAM设备额外计划一次完整cachestat，先于原严格查询；并非失败重试。`fixed_ram_totals`只汇总新RAM设备口径，不填原严格10设备完整量。

映射变化诊断为CHANGED；原前后全部来源检查通过为OBSERVED；缓存中断导致末端关系未复查等为UNKNOWN。新值依赖选定进程／cgroup、固定设备及容量前后符合；非映射登记错误及真实权限失败不绕过，无RAM时NOT_APPLICABLE且容量null。两次观察窗口不相加，不代表guest缓存物理归属。原严格UNKNOWN／PARTIAL保持。

85项相关检查通过，入口预检查／实跑／终点和报告传参完成；旧9次与loop单次共10份保存结果读回和值／报告不变。实际缓存量及两次额外查询成本尚未实测，模拟输出明确标注非业务数据。本次不启动沙箱，后续仅建议一次完整TVCache核对，不补跑9次或强制触发映射变化。

## RAM拆分真实正常路径完成（2026-11-07，仅一次TVCache）

[本次逐对象与成本](../../results/schedule-ram-split-live-20261107/REPORT.md)完成84实际／189逻辑动作、2保存6恢复、原官方每实例0通过2失败、七VMM／PSS、RAM来源及清理核对。两个预选服务进程FD的4 GiB设备独立查询均OBSERVED，缓存索引量386.140625／601.050781 MiB，完整987.191406 MiB；原10设备严格查询也10／10、999.773438 MiB。独立查询均在同一设备原严格查询之前，不是失败重试或换来源。

本次来源关系均OBSERVED，没有映射变化，仅验证正常路径；变化分离和拒绝仍复用离线模拟，不为触发变化加操作或轮次。两次独立核心墙钟／CPU3.901／3.902毫秒，含准备4.586／4.586毫秒，块设备整层136.030／96.009毫秒，全部终点710.876／545.867毫秒。成本有包含关系，不能相加，不能视为整轮增加量或减速率。

8个upper在登记后、各自查询前变化，原严格PARTIAL及读数保持，窗口小计8448.687500 MiB单列。loop两套值均6505.804688 MiB有效，各缓存层与current／PSS不相加。最低可用盘27.689 GiB，成功资源已收尾、源服务停止，其他工作区服务保持。

本阶段完成，默认新开关仍关闭，不补跑旧9次，不据此保证所有host旧缓存或guest缓存物理归属。后续正式比较应先固定指标范围和最小重复范围，不追加同类方法验证。
