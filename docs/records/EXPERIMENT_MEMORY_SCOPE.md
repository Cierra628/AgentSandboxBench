# 实验组内存范围

## 2026-11-18 provider首点成本实跑定位

仅一次同配置双实例校准，22点重算一致。首个provider前快照194.935 ms，其中记录构造193.097 ms，线程CPU 192.961 ms；其余点不能统一归因于此。10次前后快照及收尾快照分项合计通过。七实例常规采集不建议逐轮复制全量查询。报告：`results/provider-stage-timing-live-20261118/REPORT.md`。

## 2026-11-18 provider内部计时离线补齐

快照新增stage_timing_version=1及7类累计墙钟/线程CPU分项，first/last与final原样保存；读取顺序和归属规则不变。34项模拟回归通过，无业务或重新采集。旧首点具体慢因仍未知，不能离线补计时。报告：`results/provider-stage-timing-offline-20261118/REPORT.md`。下一步仅一次同配置源＋恢复实例验证分项、物理重算及收尾；慢点未复现不追加轮次。

## 2026-11-18 provider驻留与首点离线分解

5点4040 KiB整页驻留六类分解闭合，主要为1492 KiB完整未使用缓冲及2056—2440 KiB来源前后不在guest缓存表的内容；剩余未知56—412 KiB，原四来源并集不变。慢首点第一次快照192.736 ms中线程CPU191.668 ms，具体内部原因缺分项证据。报告：`results/provider-breakdown-20261118/REPORT.md`。本轮无实跑、无采集修改。下一步先离线增加快照内部成本分项与模拟验证，不重复追求未知量归零。

## 2026-11-18 provider兄弟子组一次校准通过

源＋恢复实例一次PASS，三会话22点原始重算一致，恢复5点provider新增物理副本0—48 KiB；16槽释放后全部失效。局部查询中位7.600 ms，首点195.602 ms保留；未归属provider驻留3.887—3.938 MiB仍待分解。第20—23轮完整，第24轮收尾单实例保留INCOMPLETE，非严格同刻。私有资源正常退出，可用158.645 GiB，旧失败卷未动。报告：`results/cache-provider-sibling-calibration-20261118/REPORT.md`。下一步仅离线分解未归属驻留与慢首点，不追加运行。

## 2026-11-17 provider兄弟子组离线适配完成

provider已复用lower的本批服务与设备身份，核对槽位进程/映射/完整性，并接入默认关闭的校准开关。65项模拟回归通过，未启动服务或采集。旧同组接口保留，新独立子组路径尚待一次最小源＋恢复实例实跑；未知量及非原子读取窗口仍保留。报告：`results/provider-sibling-offline-20261117/REPORT.md`。下一步按原24 GiB卷和>50 GiB门槛检查资源后，验证四来源原始重算、开销及收尾，不扩大七沙箱。

## 2026-11-17 provider接口离线检查完成

旧分段槽位接口可复用，旧双实例27点确认额外物理副本最大68／20 KiB；provider查询中位3.847／4.067 ms，仅代表旧配置局部成本。当前独立VMM子组仍被旧同组检查拒绝，会话入口未接provider；当前22点没有槽位原始数据，不能补值。报告：`results/provider-current-scope-audit-20261117/REPORT.md`。本轮无业务、无采集代码改动。下一步先离线适配服务与设备身份及默认关闭接线，补模拟回归，再决定一次最小实跑，不扩展七沙箱。

## 2026-11-17 三层驻留表离线整理完成

已整理同一校准批次全部22点，逐点核对三层物理并集算式，并保留未确认容量、旧副本分项和真实读取窗口。源实例仅VMM映射，恢复实例含VMM、直接后备与lower；不跨范围比较节省量。共同第19—22轮完整，第23轮缺源实例，仍标INCOMPLETE。报告及CSV：`results/cache-three-level-table-20261117/REPORT.md`；生成脚本：`scripts/report_cache_session_table.py`。本轮仅离线读取，无业务运行或重新采集。provider临时缓冲区与其他共享成本尚未覆盖，窗口最长约0.506秒，不是严格同刻总量。下一步先离线评估provider槽位的身份、内容有效期与采集成本，不追加同类校准。

## 2026-11-17 lower最小校准通过

一次源＋恢复实例校准PASS，三会话22点原始重算一致。私有服务provider实际FD与VMM兄弟组归属、raw LSMT索引及驻留查询均通过；恢复三层物理并集比两层多9.887—66.141 MiB，5点中3点未知容量为0，余两点3.8125／0.207031 MiB仍保留。物理副本可多于guest页容量，不混算。共同轮19—22完整，收尾恢复多出的23轮明确INCOMPLETE。报告：`results/cache-lower-fixed-calibration-20261117/REPORT.md`。旧失败卷不动；下一步仅离线整理三层物理量与未知项，不重复压低未知比例。

## 2026-11-17 lower身份字段离线修正完成

显式从已验证RAM身份和当前会话取得sandbox_id，与出生绑定PID/启动ticks/实际子组核对；不修改历史收据。新增真实字段形状及实际调用链模拟回归，38项通过，原失败批次收据离线重放成功。无实跑、无清理；lower在线验证仍待完成。报告：`results/lower-identity-fix-offline-20261117/REPORT.md`。下一步经确认后只做同配置一次lower校准。

## 2026-11-17 lower首次实跑：恢复绑定字段错误

一次尝试在bind-restored失败：vmm-child-binding的vmm_binding没有sandbox_id，新增lower适配误取该字段，KeyError。源前后12点、保存与恢复CLI成功，但lower归属/格式/驻留未验证，未重试。两VMM及私有资源已退出，24 GiB失败卷保留，可用约159.127 GiB。报告：`results/cache-lower-calibration-20261117/REPORT.md`。下一步离线从已验证RAM身份显式组装字段，并用实际收据形状补接线回归，不放宽身份检查。

## 2026-11-17 lower归属离线适配

独立VMM子组路径改为只检查本批私有服务成员，以恢复沙箱＋ublk设备匹配配置，再核对provider前后身份与实际FD设备号/inode；旧同组调用保留。38项模拟及相关回归通过，无实跑。provider槽位新范围暂未适配，显式拒绝而非静默放宽。报告：`results/snapshot-lower-owner-offline-20261117/REPORT.md`。下一步仅接显式lower开关并做一次源＋恢复实例最小验证，不扩大七沙箱。

## 2026-11-17 后备层对象链离线核对

恢复5点41661条guest→直接后备偏移复算一致，链路停在/dev/ublkb3；配置内容、lower索引/驻留及provider槽位证据未保存，不能离线补值。旧lower/provider要求与VMM同cgroup，与独立子组不兼容，需先按本批父slice＋私有服务＋实际FD对象适配归属，不能扩大扫描全机。报告：`results/cache-backing-chain-audit-20261117/REPORT.md`。无新业务运行，下一步先离线适配lower对象归属及模拟测试，不追加时间对齐校准。

## 2026-11-17 统一轮次最小实跑通过

一次源＋恢复实例校准PASS，三会话21点原始重算一致，225轮轻量曲线；共同第18—22轮起点差0.032—0.237 ms、查询窗口均重叠，无跳过或跨1秒周期。最慢联合窗口357.497 ms保留，不称原子同刻。恢复首点7.171875 MiB未确认仍保留并分解，底层未覆盖。资源正常收尾，旧失败卷未动。报告：`results/cache-unified-calibration-20261117/REPORT.md`。统一轮次功能验证到此完成；下一步仅离线核对lower/provider对象与偏移链，不追加同类运行。

## 2026-11-17 统一页级轮次离线接入

可选共享host单调时钟网格已接到采样循环，源新会话与恢复分支共用轮次；迟到、跳过、超周期及缺会话显式记录，不补零、不称原子同刻。34项模拟及相关回归通过，未实跑；默认关闭。原5点缺口另表区分直接未驻留与生命周期变化，lower/provider仍标未采集，历史数值不动。报告：`results/cache-unified-rounds-offline-20261117/REPORT.md`。下一步仅同配置一次源＋恢复实例验证共同轮次，不扩展七沙箱。

## 2026-11-17 恢复缓存缺口离线分解

5点未知量复算一致：共同150页shmem在VMM映射和直接后备均未驻留，内核查询无丢失；第0点另77页直接未驻留，第3点另83页生命周期变化。底层后备未覆盖，不能将其改成全host零占用。两个实例查询窗口均不重叠，最近相隔241—252 ms，不能给同刻去重总量。报告：`results/rollout-cache-gap-analysis-20261117/REPORT.md`。本轮无业务运行。下一步先离线分列缺口含义并接统一轮次触发，保留真实窗口，不重复追逐这150页。

## 2026-11-17 缓存会话最小校准通过

修复#6604固定镜像后，仅一次源＋恢复实例校准PASS：主干保存前后同进程、恢复分支独立身份，三会话22点原始二进制重算一致，16 MiB文件内容与收尾通过。页级查询中位约89／72／173 ms；保存采集缺口3.289291秒保留。恢复分支仍有0.586—0.910 MiB未确认容量，不补零，不称同刻双实例总量。旧FAIL及24 GiB失败卷保留。报告：`results/rollout-cache-calibration-retry-20261117/REPORT.md`。下一步只离线分解恢复未知项及查询窗口，不先扩展七沙箱。

## 2026-11-17 缓存会话首次校准：创建前配置失败

一次最小源＋恢复实例尝试，在 create-source 的本地镜像校验失败：新 runner 误读 #14400 旧校准配置，而本次准备 #6604，禁代理白名单拒绝。0 个 VMM、0 次保存恢复，无新增缓存证据，未重试。采集准备资源、容器、挂载、loop、slice 已退出，24 GiB 失败卷保留，收尾可用约183.820 GiB。39 项相关离线测试未覆盖这项镜像接线。报告：`results/rollout-cache-calibration-20261117/REPORT.md`。下一步先离线修复固定镜像来源及回归，再确认最小重跑。

## 2026-11-17 缓存会话离线接入

已分离角色名与原实例／恢复实例类型，接入创建前捕获、保存前停采、同主干新会话与分支独立绑定；新会话目录不复用旧导出，缺注册／身份冲突／停采失败报错。36 项模拟及相关回归通过，无业务运行。接口显式可选，普通入口仍关闭；诊断内核下一次源＋恢复实例验证尚未进行。报告：`results/rollout-cache-offline-integration-20261117/REPORT.md`。现有逐 VMM PSS 与父组曲线可用，七角色完整缓存对应与同刻去重尚未交付。

## 2026-11-17 七角色缓存身份离线核对

七角色身份收据、保存恢复事件与 3237 份 PSS 逐份一致。但本次轻量 BPO 没有 RAM 注册、guest 缓存元数据和 host 逐页证据，七份缓存对应量均为 UNAVAILABLE，不由 PSS 补算。现有页级接口仍是固定会话和双会话恢复索引，不能直接接入主干继续加六分支；下一步先离线分离角色与会话类型，并补生命周期测试。报告：`results/light-cache-identity-audit-20261117/REPORT.md`。本轮无业务运行、无历史数据修改。

## 2026-11-17 BPO 曲线离线对齐完成

已将 726 轮采样与创建、两次保存、六次恢复及动作结束对齐，交付总览、七 VMM PSS 曲线、最慢轮读取窗口三图和四份 CSV。421 轮七份完整；保存期间 48 轮明确留空。最后一轮联合读取 148.158551 ms、整轮 167.500425 ms，保留原值，不称原子同刻。逐 VMM PSS 与整批 file 可展示，逐沙箱完整缓存归属仍需独立元数据表。仅离线处理，无新业务运行。报告：`results/light-bpo-alignment-20261117-final/REPORT.md`。下一步先离线核对元数据表与恢复身份的关联，不追加相近业务验证。

## 2026-11-17 Prettier BPO 原业务轻量接入通过

仅一次：84 实际／189 逻辑动作，原第11、24步后两次保存、六次恢复、七实例官方各0通过／2失败与收尾均通过。726 轮父组/PSS采样中421轮七份完整；创建期按实际分母记录，两次保存共48轮明确留空主干。逐轮身份与保存读取真实VMM PID均核对通过。父组准备至清理1794点的44个闭合异常仅诊断，不再用它追加测试。

报告、原始路径及逐轮CSV：`results/light-prettier-bpo-20261117T114006Z/REPORT.md`。七份联合读取窗口中位数40.813307 ms，最后一轮最大148.158551 ms；不是原子同刻。保留原监控并新增轻量线程，未做开销对照。当前交付逐VMM PSS与整批current/file曲线，未将整批file均摊冒充逐沙箱完整缓存。下一步只需离线对齐与展示，不追加相近业务验证。

## 2026-11-17 七沙箱保存恢复通过：快照读取真实 VMM

PID 修复经 3 项 Rust 回归及一次实跑验证通过：保存读取真实 VMM 247，包装器为 246；原实例删除、新实例恢复、身份重新绑定与 16 MiB 内容检查通过。515 轮中 500 轮七份 PSS 完整，15 轮过渡总量留空；父组持续记录。完整轮联合读取窗口中位数 32.381033 ms、最大 41.102593 ms，不是原子同一时刻。采样 CPU 约单核 35.71%。私有 debug 服务端仅用于本次功能验证，未替换源服务。资源收尾通过，旧失败卷保留。

报告及逐轮 CSV：`results/light-restore-resume-20261117T105335Z/REPORT.md`。该成果是七 VMM PSS 与父组内存/文件类缓存曲线跨一次恢复可用，不代表逐沙箱完整 page cache 已被分配。下一步建议接入一次 Prettier BPO 原业务轨迹，不再追加同类合成校准。以下暂停与失败记录为历史。

## 2026-11-17 用户暂停：PID 修复待 Rust 验证

已修改快照内存读取 PID 选择及显式私有服务二进制入口，43 项 Python 检查和 Rust 文件格式检查通过。Rust 构建依赖尚未补齐，模拟进程回归、clippy 与实跑未完成；按用户要求停止本批构建，等待手动继续。未启动沙箱或替换源服务，未删卷。停点与续做顺序：`results/light-restore-pid-fix-20261117T083141Z/REPORT.md`。

## 2026-11-17 保存失败离线定位：读取了包装进程

七份出生收据与绑定原件核对一致。快照报错读容器 PID 240，但该 PID 是 launcher；真实 VMM 为容器 PID 241、host PID 115396。旧报告把 240 对应到该 host PID 的说明有误，原文保留，新报告明确更正。采样器绑定的 PSS 仍来自真实 VMM；保存路径把启动命令 PID 当成 VMM PID，遇到 clone3 包装器后不再成立。需分开管理进程与内存读取进程身份，不通过重试或改超时处理。本轮未运行业务、未改后端、未删卷。证据与修复建议：`results/light-restore-pid-audit-20261117T082638Z/REPORT.md`。

## 2026-11-17 生命周期接入：首次保存失败，恢复尚未实跑

报告：`results/light-restore-20261117T081048Z/REPORT.md`。默认关闭的lifecycle清单支持过渡状态、代次及新身份核查，53项相关离线测试通过。一次七沙箱路径实跑止于snapshot create：后端compact memory layer的process_vm_readv返回EFAULT，未进入恢复，整体FAIL且未重试。252轮中251轮N=7，一轮SAVING仅N=6，七实例总量明确留空，采样器正常退出。资源清理完成，40 GiB失败卷保留。下一步先离线定位保存内存区间与映射问题，不再重复静态七VMM验证。

## 2026-11-17 七VMM整轮轻量验证通过

报告：`results/light-seven-vmm-20261117T080327Z/REPORT.md`。显式7客体/24 GiB卷，一次本地合成运行通过、退出0；471轮每轮7份PSS和父组计数完整，联合读取窗口中位33.308毫秒、最大53.572毫秒，整轮最大57.368毫秒，周期约100毫秒。采样线程CPU16.495秒/47.077秒，约35.04%单核，记录2.170496 MiB。可用于保留实际窗口的近同时曲线，不能称严格原子或忽略开销；未做checkpoint身份切换。资源退出、本次成功卷按原入口移除，旧失败保留。下一步离线接入rollout身份更新，不再追加同类短测。

## 2026-11-17 双沙箱轻量采样完整通过

报告：`results/light-two-vmm-20261117T075720Z/REPORT.md`。补齐新入口目录准备后仅运行一次，两root VMM身份、16 MiB写读、定向驱逐请求与清理通过，148轮完整读数，退出0。联合读取窗口中位10.468毫秒、最大14.073毫秒，含身份检查整轮最大16.182毫秒；实际采样间隔最大100.219毫秒。统一采样线程CPU1.737秒/14.726秒，原始序列304,270字节，不称性能零开销或严格原子同时。旧FAIL和卷保留，本次成功卷按原入口清理。下一步验证七VMM时间窗口和rollout身份切换，不再重复双沙箱校准；不将父组记账量称为全部使用页或guest内部缓存。

## 2026-11-17 双VMM统一轻量采样首次实跑

报告：`results/light-two-vmm-20261117T074721Z/REPORT.md`。显式轻量入口不采逐页证据；两个root VMM身份绑定及22轮PSS/父组读取成功，联合读取窗口中位10.355毫秒、最大14.873毫秒，整轮最大16.675毫秒。首次写入因新入口未创建`/workspace`而失败，整体FAIL保留、未重试；数据仅约2.12秒初始观察，不能代表完整负载。51项相关离线测试通过但未覆盖该目录准备遗漏。资源已退出，16 GiB失败卷保留。下一步先修目录准备测试，再另行完成一次双VMM负载验证。

## 2026-11-17 轻量曲线的时间与范围离线核对

报告：`results/light-memory-scope-offline-20261117T073001Z/REPORT.md`。最近单沙箱2565点中，父组stat/current读取窗口中位0.238毫秒、最大0.708毫秒，采样间隔最大100.337毫秒；现有数据已支持父组记账曲线的轻量读取。三个PSS点缺准确读取起止时间，未伪对齐。导出CSV和事件前后采样距离，4项离线测试通过，未运行新实验。

新持续主指标称“实验组host记账内存/文件缓存记账量”，包含准备、控制、服务、VMM与采样开销，不称业务净占用或所有使用页总量。file包含shmem；guest内部缓存的host驻留通常属于VMM RAM，不由host file曲线替代。下一步补统一轮次的PSS时间窗口，再进行最小多VMM轻量验证；不为父子闭合追加运行。下方旧报告保留其当时验收与边界。

## 2026-11-15双沙箱：周期闭合通过，文件初始页保留检查失败

一次双沙箱校准中，两个VMM出生、注册身份及采集器收尾通过；各自写入后16 MiB guest缓存均对应16 MiB稳定host物理页。新显式入口从资源清理结束固定再采1.2秒，完整1121点中39点短偏离、最长0.100104566秒、全部恢复，segments_v3通过；四个观察终点file差均0。没有改阈值、拼接旧周期点或改写此前FAIL。

本次原入口仍FAIL：目标检查预期初始页一直保留到显式回收，但两份初始生命周期更早移除。实际guest启动参数启用了DAMON后台回收，这是优先解释，未采运行期原因统计。初始PFN清单不能代表重新装入后的当前文件缓存；全guest元数据采集与指定文件动态归属分开报告，不把初始清单归零解释为文件当前无缓存。16 GiB失败卷保留，未重试。详见`results/two-child-cache-20261115T122637Z/REPORT.md`及[LIVE_CACHE_CHILD_SCOPE.md](LIVE_CACHE_CHILD_SCOPE.md)。

## 2026-11-15单沙箱页级校准：缓存对应已实测，完整周期收尾仍FAIL

显式新入口使用Prettier #6604本地镜像、诊断guest内核，仅运行一次16 MiB文件写读和定向回收，没有跑189动作。单VMM出生子组、内存注册身份及页生命周期核对通过：4096个缓存页对应16 MiB稳定host物理页，回收后这些旧生命周期全部移除。guest缓存移除不等于host释放RAM，VMM PSS未相应下降。页级确认值与未知项单列，不用VMM子组file替代guest缓存。

三个观察终点父子file差均0；完整1069周期点的最后一点缺后续周期恢复，segments_v3仍FAIL。末点后0.067805694秒开始的独立after_cleanup查询已闭合，不追加到周期序列改判。校准和资源清理PASS与完整周期FAIL并列。报告：`results/live-child-calibration-final-20261115T120737Z/REPORT.md`；代码、范围与剩余限制见[LIVE_CACHE_CHILD_SCOPE.md](LIVE_CACHE_CHILD_SCOPE.md)。下方离线和完整GRPO段落保留各自当时的结论。

## 2026-11-15页级采集离线修复：覆盖VMM兄弟子组

已修复旧页级接口只捕获Docker组、只从Docker组寻找VMM的问题。显式父子组标签模式改为按专属父组祖先ID捕获，记录注册时PID／子组ID／启动ticks，并用沙箱身份及出生记录核对实际VMM子组；无标签的普通调用保持原范围。新增9项模拟回归及25项相关回归通过，新旧探针仅编译检查通过，未启动业务或加载探针。尚未接入七实例业务采样，也未取得本路径在线物理量。代码、字段、适用内核及下一步见[LIVE_CACHE_CHILD_SCOPE.md](LIVE_CACHE_CHILD_SCOPE.md)。

收尾差额的离线解释也已细化：父组仍包含已删除组的记账，不等于页owner已迁到父组；本轮缺dying组及逐页owner证据，不能确定具体归属。原完整序列FAIL保留。详见`results/per-sandbox-physical-audit-20261115T112631Z/REPORT.md`。

## 2026-11-15最新：Prettier运行终点闭合，完整收尾仍FAIL

修复唯一目录后只重跑一次GRPO：189动作、七实例官方各0通过／2失败、七VMM出生及io控制器检查通过。父组file为26585.312500 MiB，与9个直接子组之和精确相等；从私有准备到终点查询结束的1178点中28点短偏离、最长0.100097135秒，segments_v3通过。原先业务期间约1070 MiB持续差额本次未出现。

本轮入口仍FAIL：删卷前门禁错误地读取尚未从挂载卷复制出来的out/prettier6604/schedule-smoke-metrics.json。已离线修为读取mount/controller下的实际文件，新增回归覆盖“输出副本尚不存在时读取原文件”和“闭合失败仍报错”，相关5项通过；未为检查器错误重跑业务，原FAIL保留。

完整准备至收尾1555点另行复算仍FAIL：44点偏离（2.829582%），末段1539—1554无恢复点；16点按标称间隔换算1.6秒，实际首末点跨度1.501377433秒。末段起点恰是Docker子组消失的首点，差额约81.8 MiB；退出残留缓存记账归父组是有依据的解释，尚无逐页owner证明。不能去掉末段或改阈值宣布整体通过。下一步先离线明确子组退出后的检查边界，不直接再跑189动作或开展guest页级测量。

新16 GiB失败卷保留，自有容器／VMM／挂载／loop均已收尾；收尾可用盘86.067276 GiB。报告与原始证据：`results/prettier-memory-scope-io-on-20261115T111135Z/REPORT.md`。以下各节保留此前进展，不代表本次全流程验收已通过。

## 2026-11-15：Prettier显式父子组入口已接入，闭合验收未通过

新增显式入口 `python3 -B scripts/run_prettier_memory_scope.py --output .artifacts/<新目录>`，只执行一次Prettier GRPO。控制程序从systemd专属slice启动，一个外层采样器在私有缓存／卷准备前开始，Docker服务位于同slice，七VMM复用已有clone3_CLONE_INTO_CGROUP启动器，出生即进入七个独立直接子组。普通入口默认不变，未推广至Sphinx／Xarray或其他调度。

一次运行业务189动作及七份官方0通过／2失败检查通过；父组AVAILABLE、七份出生身份通过，但终点file差1069.917969 MiB，周期501/1560点偏离、最长46.646110215秒，违反segments_v3，范围验收FAIL。原入口先按业务成功清掉新卷，日志与序列保留；发现后已将显式模式的同一闭合验收接到success与删卷之前，离线回归通过，未重跑。详见 `results/prettier-memory-scope-20261115T100942Z/REPORT.md`。下面关于旧入口和2026-11-10未实施改造的说明按当时状态保留，不表示本次已验收成功。

同日离线分解：35段中34段仅约0.1—0.2秒，另1段466点持续46.65秒；持续段内子组集合大部分时间稳定。loop后备文件驻留1083.320312 MiB，与终点file差相差13.402344 MiB，不能据接近就认定归属。本地loop源码有按bio的blkcg选择有效memcg的路径，父组自身记账是优先核查候选；尚缺本次实际I/O记账上下文及后备页owner。原FAIL和阈值不变，建议先独立loop短诊断而非重跑189动作。证据与最小补采方案见 `results/prettier-memory-scope-decomposition-20261115T102857Z/REPORT.md`。

随后独立loop小验证（无guest）确认了该机制可实际发生：临时service仅启用MemoryAccounting、未启用IOAccounting，loop工作批次使用父组blkcg/memcg；准确后备文件的16252页、63.484375 MiB直接记在父组，与三个时点父子file差精确一致。另261页、1.019531 MiB记在组外systemd-udevd.service。父组没有用户进程不保证父组自身记账为零；这项证据不能回填Prettier历史页owner。原规则及FAIL仍不改，推荐下一步只在临时service开启IOAccounting做一次同负载对照。报告：`results/loop-memcg-diagnostic-20261115T103706Z/REPORT.md`。

同日IOAccounting=yes的单次小loop对照完成：父组向service启用io控制器后，63.484375 MiB目标文件页改记控制子组，目标loop的64个工作批次也选择控制组blkcg/memcg。写入后三个时点父子file差均0字节，34个周期点全在阈值内；目标文件总驻留64.503906 MiB及组外udev 1.019531 MiB不变。这是记账路由修正，不是内存减少；尚未接入并实跑Prettier，历史FAIL不变。报告：`results/loop-memcg-io-on-20261115T104849Z/REPORT.md`。

同日接入Prettier显式入口后，准备前IOAccounting及io控制器核对通过，但唯一一次尝试止于旧缓存目录`schedule-smoke-cache-run`冲突，0业务动作、无新业务卷，未验证七VMM。已将内层运行目录改为带完整批次名的唯一名称，并增加旧缓存不进入释放流程的检查及回归，未自动重跑。旧收尾逻辑本次曾重写已released的旧缓存资源收据，mtime变化如实记录；未改历史业务数值。详见`results/prettier-memory-scope-io-on-20261115T110245Z/REPORT.md`。当前只确认小loop对照，完整Prettier I/O修正尚待验证。

## 总量读本次独占 slice，不读 Docker 子组

今后“实验组内存”定义为：**本次运行专属 systemd slice 的包含子组的内存记账量**。`host_scope=experiment_slice_cgroup`。主值直接读取该父组的 `memory.current`，分项读取其 `memory.stat` 的 `file/anon/kernel`。不把父组与子组相加。

标准路径是 `/sys/fs/cgroup/<实际祖先>/<本次专属名称>.slice`，每次运行名称不同。不是固定的 `/system.slice`，也不是整机根组。实际路径必须由启动记录和控制程序的 `/proc/self/cgroup` 确定，再核对私有服务的成员路径，不能只猜 Docker 路径的上一级。

常规独立 slice 结构如下；systemd 根据名称中的连字符可能生成额外的祖先，例如 `asbhost.slice`，因此不能手拼一个固定层数的路径。

```text
本次独占 .slice                     ← 实验总量
├── 本次控制程序 .service           ← 私有输入准备、复制、控制、下载与检查
└── docker-<id>.scope               ← 私有 AgentENV 服务、块设备服务及其 VMM 后代
```

父子组专门验证采用另一种结构，范围定义相同：

```text
/sys/fs/cgroup/asbvmm20261110t072553z.slice
├── asbvmmcheck20261110t072553z.service
├── docker-98011920…f1c02f3.scope
├── vmm-291
├── vmm-361
├── vmm-371
├── vmm-381
├── vmm-383
├── vmm-390
└── vmm-395
```

VMM 不必拆成直接子组才能测总量；普通运行让它们留在 Docker 子组中即可。采集器保存**全部直接子组**，包括未预先命名的子组，不再递归相加孙组。

这仍是限定范围的 cgroup 物理内存记账，不是整机全部物理占用，也不是 guest 自己的逻辑 Cached。独立 slice 外的源服务、全局 Docker 进程、其他用户工作负载、之前已记在别组的缓存不自动包含。将进程迁入新组不会把旧文件缓存记账搬过来。`file` 包含 shmem；它与 `anon/kernel` 分列，不另加 shmem，也不与 mincore 相加。

## 现有八任务流程不是同一种结构

只读核对72份原始终点记录及外层收据，清单见[本次报告](../../results/experiment-memory-scope-20261110/REPORT.md)。

| 入口／任务 | 已保存的结构证据 | 能否直接取得标准范围 |
| --- | --- | --- |
| Prettier、Sphinx、Xarray，27份 | 服务均在 `/sys/fs/cgroup/system.slice/docker-<id>.scope`；没有专属父组创建收据 | 旧入口不够，需要先改创建方式 |
| Django、C4、Matplotlib、文本编辑、Cython，45份 | 外层 `host_file_creation.cgroup` 已记录本次 `asbhost-….slice`；Docker 在其直接子组；批次入口通过 `systemd-run --slice` 启动控制程序 | 后续同入口运行中可直接取得已有父组，无须再建第二层 slice |
| 父子组专门验证，1份 | 父组、控制程序组、Docker组、七VMM组均保存了周期读数和VMM出生记录 | 原始数据可离线重放；原组已经清理，不能重新读取历史时刻 |

普通 Prettier 由 `run_schedule_layer_repeats.py → run_schedule_smoke.py → validate_isolated_agentenv.py → run_prettier6604_grpo.py` 启动；Sphinx／Xarray 由 `run_python_schedule_matrix.py` 启动。它们不主动建专属 slice，控制程序继承启动者的组；没有 `--cgroup-parent` 时 Docker 使用其默认位置。原记录未保存控制程序的 `/proc` 成员路径，不能补出历史确切会话号。共享的根组或 `system.slice` 含无关进程，不符合实验独占要求。

后五项共用 `run_django_schedule_matrix.py` 的专属 slice 启动路径；它在调用私有环境准备之前执行 `systemd-run --slice`，并传入 `ASB_HOST_FILE_SLICE`。`validate_isolated_agentenv.py` 的 `measurement_group()` 核对控制程序所属祖先，然后用 `docker create --cgroup-parent` 放置私有服务。其内层 `run_python_schedule.py` 原来仍只采 Docker 组，这正是“已经有父组，却没有用它作该表总量”的问题。72份终点记录均未保存控制程序成员路径；后五项的结构由启动代码与父组创建收据支持，不冒充历史 `/proc` 读数。

## 新字段与旧值分开

`scripts/experiment_memory.py` 只解析、读数和检查，不创建 cgroup、不迁移进程。它支持已有启动变量 `ASB_HOST_FILE_SLICE` 和 `ASB_VMM_CGROUP_PARENT`；核对控制程序与观测服务均在专属父组内。没有明确专属父组、声明冲突或成员不符时，记录 `UNAVAILABLE` 和原因，父组值为空；绝不回退到根组或把 Docker 数值当父组值。

新字段只表示新范围。原 `cgroup`、`p1.counters_before/after`、旧 `memory.tsv` 和 Prettier 的 `host/memory-samples.tsv` 保持原范围及含义。新增 `legacy_host_scope/legacy_cgroup` 标记旧值。特殊验证中 `Binding.cgroup` 原本就指父组，这个行为也不变；新增 `Binding.private_service_cgroup` 单独保留实际 Docker 路径。

| 位置／字段 | 内容 |
| --- | --- |
| 新运行元数据 `host_scope` | `experiment_slice_cgroup`；只对应新增的实验组字段，不把旧列改名当总量 |
| `experiment_memory_context` | 可取得／不可取得、父组路径、控制程序路径、旧观测路径及原因 |
| Python／外部任务 `host-file/experiment-memory.jsonl` | 0.1秒父组和全部直接子组读数，与原TSV并列保存 |
| Prettier 调度测量 `experiment-memory/memory.jsonl` | 已有专属父组时，0.1秒采集；无父组只记录不可取得，不启动额外采样器 |
| 专门父子组验证 `experiment_memory_external_series` | 复用外层 `vmm-cgroup-samples/memory.jsonl`，不对同一父组启动第二个周期采样器 |
| 终点 `experiment_memory_p1` | P1之后独立父子组查询及其时间，不能声称与mincore或旧file同时读取 |
| 每行 `groups` | 原始各路径计数，含其他已有统计分项 |
| 每行 `experiment_memory.parent_counters` | 父组 `current/file/anon/kernel`，单位字节 |
| `experiment_memory.direct_children` | 按完整路径保存的全部直接子组同名四项 |
| `experiment_memory.direct_children_sum` | 只相加直接子组；仅作分解，不取代父组总量 |
| `experiment_memory.file_closure` | 有符号差额、固定阈值与检查结果 |
| 周期收据 `experiment_memory_status` | `OBSERVED/ANOMALY/UNAVAILABLE/NOT_OBSERVED`；异常次数另存，不能只看旧采集器的I/O成功状态 |

新行同时记录起点时间、读取耗时和线程CPU时间；HostFileMonitor收据另存新增读数总耗时。没有查询时不补0。旧汇总和绘图函数继续解释旧TSV；后续使用新实验总量必须显式选择上述新文件／新字段，不从旧72份列推算。

### file 闭合检查的固定规则

计算 `父组 file − Σ全部直接子组 file`，保留正负号。**绝对值大于1 MiB（1,048,576字节）即记录 `MISMATCH`**；不改动任何读数。阈值在重放前固定，没有按结果调整。字段缺失、读取失败、枚举前后子组变化记录 `INCOMPLETE`；缺失子组保留空值，完整合计也置空，不用0补齐。

1 MiB是报警阈值，不是测量误差保证。各组顺序读取，父组自身的记账、子组退出和采样时间差都可能产生差额，需结合原始行解释。仅对file做本次闭合断言，不要求current或kernel严格相等；也不靠总量闭合证明某一个文件的页一定记在此组。

## 需要另行授权的创建流程改造

Prettier／Sphinx／Xarray 下一阶段应复用已有 `run_host_file_workflow.py`／后五任务的启动方式：

1. 每次运行先生成唯一 slice／service 名，控制程序通过 `systemd-run --slice` 出生在组内。
2. 私有输入复制、同步、卷准备均从这个控制程序开始后执行。全局源缓存的准备如仍在组外，保持单列范围，不声称它已被覆盖。
3. 给私有 Docker 服务传相同 `--cgroup-parent`，在创建guest前保存控制程序／服务的实际成员路径与父组范围检查。无需更改VMM二进制或强制分组。
4. 父组采样从私有准备前开始，保留执行阶段边界，持续至私有环境收尾；正常退出、异常退出均只清理本次组与进程。

影响点是上述三个任务的外层启动和收尾，以及准备前的采样接入；不是调度算法或189动作的修改。已有后五任务只需接通标准父组采样与收据。**本次没有实施这些创建流程或启动时点改造**：新增内层采集覆盖自身启动后的窗口，不冒充已经覆盖准备前峰值。先确认下一阶段范围，再做最小验证。

## 2026-11-11历史规则：逐点后两点恢复

本节保存历史方法与当时结果；当前规则见后文“2026-11-12当前规则：按持续时间检查”。当时的新显式模式保持1 MiB阈值和全部原始读数，`MISMATCH`行不修改。`report_experiment_schedule.py`离线解析与私有卷删除前的验收共用相同代码：

- 终点file绝对差≤1 MiB，否则FAIL。
- 周期对每一个偏离点分别检查后第1、第2点是否恢复≤1 MiB；从该点计数，不从整段末点计数，不用终点查询补周期恢复点。缺项或结尾无恢复证据FAIL。记录偏离点数、最大正负偏差、最长连续点数、各点恢复索引及未恢复索引。
- current/file的采集窗口、调度窗口峰值分别检查。原峰值落在偏离段时，窗口延伸至紧邻的前一个闭合点与后一个恢复点；只采用阶段内实际保存的闭合边界值替代该段，再与阶段其他闭合点取最大值。不插值、不平均父子值、不手工补值。无合格边界或无恢复证据则峰值不可用、验收FAIL。保存原峰值、边界替代值、最终值与点索引。这里的闭合指file，不要求current/kernel相等。

新增`periodic_file_closure`、`endpoint_file_closure`、`peak_closure_checks`及父组file两种窗口峰值；旧Docker字段不变。原采集器`ANOMALY`仍表示出现过原始偏离，最终验收依据以上三类规则。无专属父组仍记UNAVAILABLE，不回退。

用户确认逐点计数后，原Django GRPO的93—95三连偏离中，第93点至96恢复需要3步，故新规则仍FAIL；其余三个偏离点在两点内恢复，终点与四个峰值检查通过。历史批次及原值不改，不作为新范围成功重复。

`run_django_schedule_matrix.py --schedules bpo tvcache --rounds 1 --experiment-memory-standard`显式补两项；普通默认仍三轮九次。仅新模式传给原私有入口同名验收参数，在卸载、loop解绑和保存输出后、删除本次成功卷前检查；失败保留本次卷，不保留运行中的沙箱，不改变systemd/Docker创建方式。

## 2026-11-11历史规则：按偏离段点数检查

用户指定规则版本`segments_v2`，只改验收，不修改原始采样器或读数，也不启动业务：

- 终点：父组file减全部直接子组file，绝对差≤1 MiB，否则FAIL。常量`FILE_CLOSURE_THRESHOLD_BYTES=1048576`不变。
- 周期：每段必须在周期序列结束前恢复≤1 MiB；最长连续偏离≤3点（`MAX_CONSECUTIVE_DEVIATIONS=3`）；偏离点数／总周期点数≤5%（`MAX_DEVIATION_FRACTION=0.05`）。三条分别记录实测值、上限与结果，全部满足才PASS；空序列、缺项和末尾缺恢复证据FAIL，不用终点查询补周期恢复。
- 峰值：若current/file的采集窗口或调度窗口原峰值在偏离段内，用该段前后两个闭合边界的实测值替代该段，再与其余闭合点取最大；不插值、不平均，缺任一阶段内边界则不可用并FAIL。原始值、边界值和最终值均保存，不改原周期读数。

3点仅是0.1秒采样下约300毫秒量级的验收上限，不是内核统计必定在此时间内刷新的保证。偏离方向和恢复现象支持统计刷新不同步的解释，未取得逐CPU运行时证据，原因没有被证明。

`report_experiment_schedule.py`自动输出方法、三条实测值／上限／判定和四类峰值原值／替代值；`historical_periodic_file_closure`继续保留历史逐点规则的计算。`replay_django_closure.py`只读实际报告的原始路径，结果另存，`historical`保留原PASS／FAIL，`recomputed`保存新检查；不覆盖旧报告或原数据。

[三份Django离线复算](../../results/django-closure-segments-v2-20261111/REPORT.md)：GRPO为4／485（0.824742%）、最长3点；BPO为4／571（0.700525%）、最长1点；TVCache为5／400（1.250000%）、最长2点。三份所有偏离段均恢复，终点差均0，四类峰值均不受影响，新规则全部PASS；原GRPO的逐点规则FAIL仍保留。

回归数据`tests/fixtures/django-file-closure-20261111.json`只保存真实三份周期闭合状态与有符号差额的无损连续段编码；不包含虚构内存计数。测试覆盖已知三份结果、恰好3点／5%、超过点数／占比、末尾未恢复、缺项、严格终点及峰值边界替代。

离线复算调用（输出必须是新目录）：

```bash
python3 -B scripts/replay_django_closure.py \
  --source-summary results/django-slice-stage1-combined-20261111T063634Z/summary.json \
  --output "results/django-closure-replay-$(date -u +%Y%m%dT%H%M%SZ)"
```

本次只完成新规则离线验证，不启动第二批36次。普通入口默认轮数、systemd/Docker创建方式和原始采集均未改。

## 2026-11-12当前规则：按持续时间检查

`segments_v3`仅替换周期段长度的判定，不改采样器、创建流程或历史读数：

- 终点file父组减全部直接子组的绝对差≤1 MiB，阈值不变。
- 周期每段必须在序列结束前恢复，偏离点占比≤5%，不变；单段改为≤1秒（`MAX_SEGMENT_SECONDS=1.0`）。
- 持续时间按首个偏离点至首个恢复点的`monotonic_ns`之差计算，段内与恢复点时间须有效且递增；不能确定时，用偏离点数×0.1秒（`NOMINAL_INTERVAL_SECONDS=0.1`）换算。逐段保存秒数、时间来源、回退原因；末尾无恢复仍FAIL，不能用换算代替恢复证据。
- 峰值算法不变：偏离段前后两个闭合边界实测值替代，保留原值、边界和最终值；不插值、不平均。

代码在`scripts/report_experiment_schedule.py`的`periodic_closure()`；原3点规则保留为`periodic_closure_v2()`及新增`historical_periodic_file_closure_v2`，更早的逐点规则仍保留。`longest_consecutive_points`只作诊断；v3判定看`longest_segment_seconds`和逐段`duration_seconds/duration_basis/duration_fallback_reason`。报告同时显示时间实测值／1秒、回退使用情况及v2判定。

[13份离线复算](../../results/closure-segments-v3-20261112T051632Z/REPORT.md)全部PASS，无时间戳回退，终点均0，52个峰值未受影响。Matplotlib最长0.600397290秒，原v2 FAIL保持；新判定不修改原运行状态，也不证明内核刷新原因。6项v3回归及13项相邻测试通过。

复算脚本`scripts/replay_closure_segments.py`只读指定原报告路径，输出必须为新目录；本批来源为Django的v2复算摘要、C4九次摘要和Matplotlib失败摘要。回归夹具`tests/fixtures/closure-segments-v3-20261112.json`保存13份真实闭合状态／差额的连续编码与每段至恢复点的完整原时间戳，不包含虚构内存值。

本次不启动业务。指定Matplotlib失败卷按用户单独授权删除，其他原始记录与历史报告不改；清单、SHA256和前后空间见上述新结果目录的CLEANUP.md。
