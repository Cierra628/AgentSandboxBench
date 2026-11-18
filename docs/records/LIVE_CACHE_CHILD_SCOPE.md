# 不暂停页级采集：适配VMM独立子组

2026-11-15：单沙箱校准后，继续完成收尾离线核对，并只运行一次双沙箱验证。两个VMM身份及独立采集通过、完整父子周期闭合通过；“初始文件页一直保留到显式回收”的校准假定被数据否定，原入口FAIL保留，没有重试。

随后离线补齐指定文件动态页查询接口、DAMON运行状态采集与模拟回归，见`DYNAMIC_GUEST_FILE_CACHE.md`。离线阶段仅完成guest内核对象编译，当时没有启动新内核或沙箱。旧数据没有新增文件身份及运行期统计，仍记未知，原七条失败保留。

接着已完成一次新版本单沙箱校准：16 MiB写入、定向回收后0、重读重新得到16 MiB，4096个偏移均获得新生命周期；新文件对应、原入口及完整周期闭合均PASS。写入阶段一对观察有3072页未确认，未补值，原因与边界见`results/dynamic-file-calibration-20261115T132550Z/final/REPORT.md`。没有重跑旧双沙箱，没有运行原29动作，旧七条失败不改。

## 最新：新协议双沙箱动态查询通过

`results/dynamic-two-child-cache-20261115T141807Z/final-reviewed/REPORT.md`记录了一次新协议验证。两份16 MiB文件写入、定向回收、重读均分别完成，重读后各4096个偏移全部获得新生命周期。两guest的身份及host页对应通过；五阶段各2—3对完整文件观察均无未确认页，全guest仍各有1—26、1—32页未确认。

A后期自行从15.261719 MiB降至0，没有误算为B的缓存，也没有因另一边缓存变化判失败。完整周期1213点、28点偏离、最长0.100100958秒，全部恢复；末点及五阶段file差均0，segments_v3通过。旧协议FAIL及失败卷不动，普通默认不改。身份、成本、覆盖及收尾详见`DYNAMIC_GUEST_FILE_CACHE.md`顶部。指定文件校准不等于完整逐沙箱host内存测量。

## 离线分解已完成

`results/private-cache-breakdown-20261115T143147Z/REPORT.md`从36点二进制分出普通文件/shmem的host确认驻留及未知原因，逐点与原值一致，并给出10个沙箱×阶段的PSS、子组四项与25行父子组分解。创建VMM前父组file已达21352.734375 MiB，写入阶段主要记在控制程序组；不把这部分均分给沙箱。指定文件缓存只是普通文件缓存的一部分，不能再次相加。

未保存的全RAM逐页证据、host后备对象驻留和共享成本归属已明确列为缺项，未用PSS或cgroup数值补足。无业务运行，无历史数据修改；下一步建议仅离线接入最小补采，后续单次双沙箱验证另经确认。

## 双沙箱结果：能分别采集，固定初始页清单不足以跟踪重读后的文件

报告：`results/two-child-cache-20261115T122637Z/REPORT.md`。原始：`.artifacts/storage-isolated-service/20261115T122748Z-3506342/guest-cache/`。

使用同一Prettier #6604镜像，两个4 GiB诊断guest独立创建、同时存活、分别采集，没有运行原29动作，没有暂停、保存或恢复。两份注册捕获均在两个guest创建前启动；沙箱ID、PID、启动时间、出生子组及注册ID逐一对应，采集器均正常结束。写入阶段两边各4096个缓存页，分别对应16 MiB稳定host物理页。

失败发生在保存后的目标页检查。A最初登记的4096个生命周期在显式回收派发前0.924930秒已全部消失，B提前6.376429秒消失。因此无法通过“只回收A时B的初始页仍保留16 MiB”的预定检查，原FAIL及七条偏离错误保留。这个结果没有证明沙箱混绑：全guest缓存仍逐点从当时元数据计算，不使用固定目标清单。

实际两份guest串口`firecracker-stdout.log`第70行确认启动参数包含`damon_reclaim.enabled=Y`、`damon_reclaim.min_age=100000`等。DAMON后台回收是页提前移除的优先解释；本轮没有运行期回收统计和逐页原因，不能确定唯一原因。不能关闭回收、锁目标页或缩短窗口让校准凑通过。

新发现的范围边界：`target_cache_after_bytes`等原字段仅表示written时登记的guest PFN和生命周期，不是页重新装入后的当前文件总缓存。内核表能跟踪全guest缓存加入/移出，但没有保存文件设备号、inode、页偏移到新guest PFN的对应。初始生命周期归零不等于当前文件缓存为零；报告已把这个含义写清，重读后当前文件缓存记UNAVAILABLE，不回写原值。两份全guest确认驻留仍可按原限定范围引用，变化未知页数分别为0—366、0—1297，不补零。

旧单沙箱收尾离线核对见`results/live-child-tail-audit-20261115T121916Z/REPORT.md`：旧FAIL不改。新双沙箱入口固定从资源清理完成边界再采1.2秒，不等待“采到通过”，也不改segments_v3。实测1121点、39点偏离（3.479037%）、最长0.100104566秒、全部恢复；四阶段终点file差均0，完整周期PASS。它与目标校准FAIL分开。

新增`run_guest_cache_two_children.py`、`report_two_child_cache.py`；显式入口`run_live_child_calibration.py --guests 2`用16 GiB卷、严格>42 GiB门槛，默认单沙箱仍12 GiB、>38 GiB。`LiveTaskCache`新增两个private会话名，不启用恢复后备查询；普通业务默认和旧恢复路径不改。14项离线回归通过，日志`results/two-child-cache-preflight-20261115T122541Z/tests.log`。离线复算与原七条失败完全一致。

失败卷`.artifacts/storage-isolated-service/20261115T122748Z-3506342/volume.ext4`（16 GiB）保留，未清理。自有容器、VMM、探针、挂载和loop已退出，源服务恢复停止；核查可用盘69.999142 GiB。清理检查见本次报告目录的`post-run.json`。

上述离线修补及扩展内核单沙箱验证现已完成，接口与结果见`DYNAMIC_GUEST_FILE_CACHE.md`。这项双沙箱动态文件页验证现已完成，见顶部；没有重跑旧协议。跨沙箱共享页去重仍需有实际共享页的独立对照。

## 单沙箱在线校准结果

报告：`results/live-child-calibration-final-20261115T120737Z/REPORT.md`。原始外层：`.artifacts/live-child-calibration-20261115T120737Z/`；隔离记录：`.artifacts/storage-isolated-service/20261115T120849Z-3495823/guest-cache/`。

使用Prettier #6604固定本地镜像、4 GiB诊断guest内核，没有重放原29动作。VMM通过clone3出生在专属父组的一个直接子组；MMDS沙箱ID、PID、启动ticks、出生记录、KVM注册时子组ID全部一致，实际内核探针注册记录返回0、stderr为空。

16 MiB普通文件写读后，4096个guest缓存页对应16 MiB稳定host物理页，六个阶段内观察点均无目标未知页；只对该文件执行POSIX_FADV_DONTNEED后，4096个旧缓存生命周期全部移除，三个观察点的目标缓存为0。目标页没有锁定，没有暂停guest，也没有清全机或全guest缓存。锁定的是采集器自己的16.007812 MiB匿名导出缓冲区。

这是“缓存页当前对应哪些host驻留页”的证据，不是host释放16 MiB RAM：guest缓存回收后VMM PSS没有相应下降。全guest采样保留变化未知项：初始1170页未确认，读阶段84／2／0页未确认，不补成已确认值。10点采集的host线程CPU为0.677968秒，每点墙钟中位数0.084713秒，最大0.120455秒；未做开关成本对照。

三个阶段的父子file差额均0。准备至收尾1069个周期点中35点偏离（3.274088%），最长已恢复段0.100096052秒，但最后一点差2032.843750 MiB、缺后续周期恢复点，原segments_v3仍FAIL。另存after_cleanup在末点后0.067805694秒开始读，差额为0；这项独立观察不追加到周期序列、不改判，也不证明具体内核刷新原因。页级校准及原入口资源清理PASS与完整周期FAIL并存。

本次新卷12 GiB，启动门槛严格>38 GiB，实测启动前86.054977 GiB；成功卷、容器、挂载、loop与VMM按原入口清理，收尾可用86.038681 GiB。已有失败卷未删除。七实例并发、恢复后备层、实际共享PFN去重和完整host开销分解尚未验证，不能据此宣布完整逐沙箱测量完成。

显式入口为`python3 -B scripts/run_live_child_calibration.py --output .artifacts/<新目录>`；仅新增`--guest-cache-child-calibration`校准分支，普通默认不变。新七项模拟回归和逐页报告见`tests/test_live_child_calibration.py`、`scripts/report_live_child_calibration.py`。

## 修复的是哪两处遗漏

原采集只捕获Docker scope中的KVM内存注册，并从该组的`cgroup.procs`寻找VMM。显式父子组验证中，七VMM出生在Docker的兄弟子组，因此原选择范围会漏掉它们。

仅当服务已有`asb.vmm-child-cgroup=on`标签时，`TaskPhysicalCache`才启用新范围：

1. 复用`host_sandbox_monitor.Binding`核对服务、专属父组和运行标签。要求实际Docker组位于声明的`asbvmm….slice`内；声明不符直接失败，不回退到根组或Docker组。
2. 探针按当前任务在指定层级的祖先cgroup ID过滤，覆盖该父组内后来创建的子组，不冻结一份启动时的子组清单。必须在创建VMM、注册RAM之前启动捕获。
3. 每次注册另记PID、当时实际cgroup ID及启动ticks。原六列RAM注册记录仍保留，旧解析器跳过新增的`IDENTITY`行。
4. 按MMDS的沙箱ID找到目标VMM，再核对实际子组、启动时间、子组唯一PID及clone3出生记录。注册时身份必须与当前身份一致；缺记录、旧PID、错组均报错，不猜测或迁移进程。
5. `bind_vm`使用VMM实际子组路径，而不是用搜索父组路径代替成员路径；仍需guest标记及实际RAM范围校验。

`LiveTaskCache.start()`与原边界接口共同调用`TaskPhysicalCache.bind()`。不暂停的Live接口没有新增pause调用；原暂停边界接口保持原用途，不作为本阶段验证路径。

无显式标签时保持原Docker精确范围，不新增MMDS、出生记录、IDENTITY行或BTF要求。普通入口默认开关、调度、采样周期、240秒捕获时限及已有等待上限不变。

## 新证据与代码

| 文件或字段 | 含义 |
| --- | --- |
| `capture-scope.json` | 声明的专属父组、ID、祖先层级、BTF偏移、启动ticks频率 |
| `kvm-slots.tsv`中的`IDENTITY`行 | 注册时PID、实际子组ID及启动ticks；不是内存容量 |
| `vmm-child-binding.json` | MMDS绑定、出生记录、注册身份与最终实际子组对应 |
| 原`vmm-identity.json` | 成员路径现在是实际VMM子组；旧模式仍是Docker组 |
| 原`ram-attestation.json` | 原RAM范围和guest标记证明，保留原结构，不重写 |

代码：`scripts/task_physical_cache.py`、`scripts/live_task_cache.py`、`scripts/agentenv_whole_cache.py`。后者仅为离线测试增加可注入的proc/cgroup根路径，默认路径及原位置参数不变。

当前新探针复用`host_existing_cache_owner.layout()`读取运行内核BTF（内核结构布局），沿用该模块的Linux 6.19.9+版本限制。bpftrace 0.20无法直接解析本机`kernfs_node.id`，因此从BTF取得该字段偏移；本机读到96字节，但运行代码不写死96。缺BTF或版本不符报错，无静默替代。旧Docker精确范围不依赖此模块。

## 离线证据和剩余范围

报告：`results/live-cache-child-scope-offline-20261115T113908Z/REPORT.md`。新增9项模拟回归、25项相关回归通过；新旧两种探针生成代码的`bpftrace -d`仅编译检查均通过，没有加载、挂接探针或触发真实设备调用。

复现模拟回归：

```bash
python3 -B -m unittest discover -s tests -p test_live_cache_child_scope.py -v
PYTHONPATH=scripts:tests python3 -B -m unittest \
  test_live_cache_vm_binding test_live_task_cache \
  test_kernel_cache_metadata test_snapshot_cache_residency -v
```

上述离线修复最初只涉及捕获范围与身份绑定；随后已通过顶部单沙箱在线校准接通诊断内核、元数据导出、创建前捕获及采集器收尾。没有把页级采样接入189动作入口，没有放宽现有会话及内核检查。共享后台、组外缓存、全部后备层的逐页对应仍单列。

上述收尾核对、旧协议双沙箱验证、文件动态查询的离线实现及新协议单沙箱验证现已完成，结果与新发现见顶部。新协议双沙箱对应也已完成。离线分解现已完成，见顶部报告；后续先离线接入已列明的补采项，不先重跑七实例完整业务。
