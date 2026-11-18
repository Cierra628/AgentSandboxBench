# host特殊映射：真实补采通过，命令缓冲与映射PSS分列（2026-10-21）

**一次真实单／双沙箱验证已完成30个新后缀动作，7次ublk查询全部成功，命令缓冲分配合计12／16 KiB；特殊映射的41／74段smaps均已采到。** 原任务结果和清理通过。已删除后备对象在所属进程中没有匹配描述符，整对象元数据及驻留仍未知；映射PSS已有实测，补采完整状态保持INCOMPLETE。

[上一轮实跑与原始路径](HOST_MEMORY_COMPACT.md) · [脱敏分类与源码摘录](evidence/host-special-mappings-20261021.json) · [当前验收范围](HOST_MEASUREMENT_ACCEPTANCE.md)

## 直接查看或离线生成汇总报告

现成结果：[最新一轮内存分项报告](../../.artifacts/host-memory-report-20261021/latest/MEMORY_DETAIL.md)；[缺少新字段的旧轮次报告](../../.artifacts/host-memory-report-20261021/legacy/MEMORY_DETAIL.md)。同目录`summary.json`供程序读取，`report-N1/`和`report-N2/`保留原曲线及分组JSON。

报告自动列出指定设备与额外文件驻留、全组匿名PSS、特殊映射PSS、源码计算的命令缓冲、缺失描述符和查询耗时；原官方0通过／2失败、file记账不足及整体NOT_MEASURED同时保留。PSS、缓存和命令缓冲不合成总量，原始Pss为0不推断为整对象零驻留。旧数据未采的新字段显示“未测”或null，不追补成本或容量。

要另存一份报告，指定一个尚不存在的目录：

```bash
bash scripts/report-host-independent-pair.sh \
  .artifacts/host-file-independent-pair/20261021T080638Z-4169682 \
  --output .artifacts/host-memory-report-copy
```

该命令只读已保存的JSON、采样记录和前缀元数据，不启动服务、创建卷或查询设备，不需要读取镜像／归档内容。输出必须是原工作流程和各组原数据目录以外的新目录；已有目录会拒绝，不覆盖旧报告。正常独立单／双沙箱流程也会自动生成`MEMORY_DETAIL.md`，原运行参数保持。

实现集中在纯离线的`host_memory_detail_report.py`，两层现有报告共用同一份汇总；`run_host_independent_pair.py --report-only … --output …`提供离线调用。34项相关检查通过，包括8项新增回归；最新、旧两轮实际复算与此前独立分析相符，原JSON的全部既有字段值保持。旧结果仍把新映射PSS及命令缓冲列为未测。新生成曲线已检查，并修正独立组长标题被截断的问题，曲线数据不变。

本段只有报告改动、离线检查和复算，没有新增沙箱动作或设备查询。实现前版本、日志和交付记录位于`.artifacts/host-memory-report-20261021/`；[脱敏核对记录](evidence/host-memory-report-20261021.json)。

## 最新实跑回答了什么

复用原第11步快照，两组独立私有服务／可写卷，单沙箱10步、双沙箱各10步，共30个新动作；原前缀没有重跑。恢复、进程身份、原动作、补丁、独立检查及清理通过，每实例原官方0通过／2失败保持。此前48项原有及9项新增离线回归继续复用，运行代码没有新改动。

### ublkc命令缓冲已取得实际配置，不再作为这一分项的未知量

| 查询结果 | 单沙箱组 | 双沙箱组 |
| --- | ---: | ---: |
| 列出的ublkc设备／成功GET_DEV_INFO次数 | 3／3 | 4／4 |
| 每设备实际队列数 | 1 | 1 |
| 每队列实际深度 | 16 | 16 |
| 每设备命令缓冲分配，按源码计算 | 4 KiB | 4 KiB |
| 设备去重后的命令缓冲分配合计 | 12 KiB | 16 KiB |
| 这些PFN映射的原始Pss合计 | 0 | 0 |

设备身份、活跃状态、提供程序身份、映射偏移／长度及PFN标志均相符。**这里的零PSS是统计跳过，不是零分配。** 每个设备按实际1队列、深度16、24字节描述符和4 KiB页计算，得到一页命令缓冲；这是源码计算的内核分配量，不是page cache，不再加到PSS、kernel或设备mincore上。

### 现在能直接报告特殊映射的PSS

| 各类映射Pss之和，KiB | 单沙箱组 | 双沙箱组 |
| --- | ---: | ---: |
| io_uring | 436 | 524 |
| KVM vCPU | 12 | 28 |
| memfd:iov_deque | 4 | 8 |
| 已删除/dev/zero | 0 | 0 |

这些是依次读取的映射PSS，不是各类完整后备对象的物理并集。KVM虽然每组仍只有一个inode汇总键，现在已经保留vcpu:0和vcpu:1各自的映射名字及地址，不再用该键推断共享。

本轮已删除memfd和/dev/zero映射均为共享读写、4 KiB页，无ht标志，Anonymous及hugetlb字段为0。ht是内核输出的hugetlb映射标志（`fs/proc/task_mmu.c:1173`）；结合下文memfd及共享/dev/zero源码路径，这些记录符合普通shmem映射条件，没有观察到私有复制或hugetlb映射。memfd的映射PSS为4／8 KiB；dev/zero的零RSS／PSS表示读取时没有统计到映射驻留，并不能推出后备对象全局缓存为零。

所属进程没有匹配描述符的已删除对象为2／4个，没有出现权限或设备查询错误。采集器因此仍将`special_metadata_status`记为INCOMPLETE：**缺的是整个后备对象的描述符元数据和完整驻留量，不是这些映射完全没有被测量。** 本阶段以原记录中的缺项为依据结束，不为找回描述符再跑任务或扩大到全机扫描。

### 查询成本已直接计时

| 直接观察的用时 | 单沙箱组 | 双沙箱组 |
| --- | ---: | ---: |
| ublkc查询合计 | 5.282毫秒 | 5.825毫秒 |
| 映射枚举、smaps、描述符查询及额外文件查询合计 | 23.450毫秒 | 29.323毫秒 |
| 主要对象和PSS的连续观察段 | 2.160秒 | 2.177秒 |
| 再包含之后的其他设备查询 | 5.259秒 | 6.840秒 |
| 工具累计 | 1.729秒 | 3.957秒 |
| 后缀墙钟 | 2.348秒 | 2.891秒 |

查询程序编译113.956毫秒，单列为准备成本；上述任务后查询不计入工具用时，双沙箱工具累计是两实例相加。上一轮映射枚举／额外文件查询为9.220／10.555毫秒、主要观察段2.062／2.083秒，范围少了本次新增元数据；这是不同轮次的实际用时，不是关闭采样对照，不能据此计算任务减速率。

### 其他测量范围与清理保持

35个额外普通文件的全局驻留仍各155.055 MiB，不与file或PSS相加；ublkb映射与已测目标设备身份一致，不重复加入。指定内存设备驻留408.488／424.375 MiB仍只按该对象报告。最终普通清单净不足1745.191406／1738.429688 MiB，两组保持SHORTFALL_REQUIRES_RESOLUTION；命令缓冲不用于抵扣文件记账差额。整体host物理总量／节省和guest缓存对应物理集合仍NOT_MEASURED。

整段2655个host点、266次PSS，其中2次不完整扫描保留。当前补采分项验证完成，不追加同类实跑。

- 工作流程：`.artifacts/host-file-independent-pair/20261021T080638Z-4169682/`，退出0，原生摘要PASS_INDEPENDENT_WRITABLE_PAIR。
- 单／双结果：`.artifacts/storage-isolated-service/20261021T080756Z-4170563/`、`20261021T080931Z-4173413/`中的`prettier6604/compact-memory.json`及`host-matched.json`。
- 离线核对、执行及资源记录：`.artifacts/host-special-live-20261021/`；[脱敏结果](evidence/host-special-live-20261021.json)。

开始115.757 GiB可用，资源核对时115.744 GiB。自有guest、服务、卷、挂载、loop及准备RAM已释放，同一源服务恢复为原停止状态；没有删除旧失败卷。下一次实跑仍重新检查资源，不根据此旧读数启动。

## 当前实现补什么，不改变什么

仅显式`--host-memory-detail`使用本次补采；命令不变，仍附加在独立卷恢复入口上。原普通轻量入口、0.1秒计数和1秒PSS保持。新代码仍在任务后、guest删除前执行一次，所有查询时间计入原明细观察段。

- `host_mapped_backing.py`保留特殊映射的进程启动身份、地址范围、权限、偏移和原名字。即使匿名inode相同，KVM的不同映射也保留各自记录，不用inode判断物理共享。
- `host_special_metadata.py`按地址范围和完整映射身份关联smaps，保存Pss、Anonymous、hugetlb和VmFlags等实际字段。映射变动、进程身份变化或权限不足分别记录，不按零处理。PFNMAP下即使原始Pss为0，也明确标为`SKIPPED_PFNMAP_NOT_RESIDENCY`，不解释成零占用。
- 对已删除后备对象，仅在所属进程仍持有匹配文件描述符时查询身份、大小和文件系统类型：TMPFS、HUGETLBFS或OTHER。以`O_PATH`打开元数据引用后核对身份，用fstatfs查类型；不读内容、不做mincore，不把文件大小当驻留。没有可访问描述符时仍未知。
- `host_ublk_info.c`只提交`UBLK_U_CMD_GET_DEV_INFO`，取得实际队列数、深度、设备状态和提供程序进程号。Python侧核对设备身份、进程身份、队列映射偏移、长度、共享只读权限及PFN标志，随后按下文源码公式计算命令缓冲分配。重复映射不重复分配计数；查询超时或条件不符时容量为null、状态UNKNOWN。

逐映射smaps没有Pss_Anon／Pss_File／Pss_Shmem三项，它们只在smaps_rollup中输出（`fs/proc/task_mmu.c:1328–1349`）。本次不合成这三项，也不把Anonymous驻留字节数当作匿名PSS。

**查询程序用O_PATH取得ublkc节点身份，不普通打开它。** `ublk_drv.c:1541–1550`的`ublk_ch_open`有独占检查并设置提供程序身份，普通只读打开也会进入此函数。O_PATH不调用设备open（`fs/open.c:913–919`）；真正发送的唯一命令针对`/dev/ublk-control`，其open仅使用通用nonseekable_open，没有提供程序接管或release操作（`ublk_drv.c:3882–3887`）。查询等待2秒，外层进程超时4秒；不发FETCH、START、STOP等命令，不暂停guest或操作其I/O请求。此路径已完成上述7次真实查询验证，开销单列。

仅启用明细时，工作流程会在准备输入和启动服务之前编译查询程序，编译失败记在准备阶段。成功时用`metadata_query_build.duration_ns`单列编译用时：它发生在周期host曲线开始前，不计入工具或任务后明细查询；编译后仍存在的组内缓存会反映在后续记账中。依赖本机cc、liburing开发头文件和库；本轮编译及`--abi`检查通过，后者不创建ring或访问设备。普通旧入口不新增这些运行条件。

原查询`status`继续表示普通文件等原查询是否有效；新增`special_metadata_status`单独表示补采是否完整，工作流程摘要保留此状态。补采完整也不等于物理覆盖完整，`coverage`仍把特殊设备和未解析来源列为INCOMPLETE。`ublkc_command_allocation_bytes`只在所有已枚举ublkc设备都可按源码计算时提供，任何一项未知就为null；它不是page cache，不自动加到PSS、kernel或普通文件驻留中。

## 离线检查结果与证据

48项原有相关回归通过，涉及普通文件去重、旧入口、恢复、任务顺序及报告；最终9项新增回归通过，覆盖同inode不同KVM映射、smaps关联／变化、已删除描述符、PFN零PSS、队列取整与重复映射、身份不符／超时保留未知、显式入口接线，以及C程序编译和ABI。所有设备查询均用测试数据替代，测试文件只有临时小文件；没有启动服务或真实io_uring查询。

例如测试中的队列深度400、描述符24字节：每队列映射12 KiB，按2的幂分配16 KiB，两个队列合计32 KiB；第三次映射同一队列不会增加该值。**这是测试输入，不是本轮沙箱实测容量。**

实现前版本、修改对比、回归日志和交付记录：`.artifacts/host-special-metadata-20261021/`。脱敏实现记录：[补采实现证据](evidence/host-special-metadata-20261021.json)。此前轻量备份保持原样，旧实跑的未知项及约1.7 GiB文件记账差额不追溯改判；后续真实结果见本页最新实跑一节。

下面保留上一段已有数据的源码分类及其数值。

## 保存的记录能回答什么

读取上一轮两组`compact-memory.json`、`host-matched.json`，核对进程身份及已测设备身份。源码目录为`/home/yxli/linux-6.19.9-ioctl/`；本机运行内核为6.19.9+，配置启用MEMCG、X86_64及ARCH_HAS_PTE_SPECIAL。下文行号均相对此源码目录，摘录保存在脱敏证据中。

| 特殊来源 | 单沙箱：汇总键／映射行 | 双沙箱：汇总键／映射行 |
| --- | ---: | ---: |
| io_uring | 9／23 | 15／40 |
| KVM vCPU | 1／4 | 1／8 |
| 已删除memfd:iov_deque | 1／2 | 2／4 |
| 已删除/dev/zero | 1／1 | 2／2 |
| 上述未知来源合计 | 12／30 | 20／54 |
| 另列的ublkc字符设备 | 3／3 | 4／4 |
| 另列的ublkb块设备 | 1／12 | 1／14 |

“汇总键”只是采集器按设备号和inode整理的记录，**不是物理对象数**。KVM使用的`anon_inode_getfd`会复用公共匿名inode（`virt/kvm/kvm_main.c:4128–4138`、`fs/anon_inodes.c:135–207`），所以两个VMM的不同vCPU可以落在一个键下；旧记录只保存首个名字和映射行数，不能据此认定共享了物理页。

ublkc来自ublk提供程序，ublkb来自VMM。两组ublkb身份分别与已执行mincore的目标内存设备一致，不能再加一份。ublkc不是块设备缓存，它映射的是内核给每个队列分配的命令描述缓冲，和承载读取数据的提供程序缓冲也不是同一项。

## Pss_File不能直接解释成page cache

PSS是按进程映射的共享比例分摊的驻留内存。`fs/proc/task_mmu.c:886–898`按页属性分类：匿名页进入Pss_Anon，其他可交换后备页进入Pss_Shmem，其余进入Pss_File。**最后一类还可能包括映射到用户进程的内核分配页，不只包括普通文件缓存。**

这与`memory.stat:file`的定义不同；后者来自文件页计数NR_FILE_PAGES，`kernel`来自MEMCG_KMEM（`mm/memcontrol.c:1332–1346`）。因此：

- 不能用Pss_File代替host page cache，也不能把它全部当成mincore清单外的新增缓存。
- PSS和cgroup记账是两种观察方式，不是互斥的物理类别。部分内核页可以同时出现在PSS和kernel中；总PSS不能再直接加kernel。
- PSS只包含它能够统计的驻留映射，不包含全部未映射缓存或全部特殊映射。名字未知不等于汇总里完全没采到，汇总存在也不等于全部覆盖。

### io_uring：映射环的内核页会进入PSS，也有内核记账路径

`io_uring/memmap.c:152–176`以`GFP_KERNEL_ACCOUNT`分配映射区域；`:258–269`排除用户自备区域进入这条mmap路径，`:284–292`用`vm_insert_pages`映射。`mm/memory.c:2274–2310`插入普通页表项，PSS可统计这些页；这些非匿名、非swap-backed页归入Pss_File。

分配标志要求内核内存组记账（`mm/page_alloc.c:5275–5282`）。在组内创建并成功记账的页会反映在kernel／current中，与PSS重叠。这里讨论的是已列出的环映射，不把io_uring的所有固定缓冲或其他资源都归成同一类。现有记录没有逐映射字节数，不能指定每个环贡献多少PSS，也不新增一项“未知环容量”加到总数上。

### KVM：run／PIO页也是映射到用户进程的内核页

`virt/kvm/kvm_main.c:4067–4093`在缺页时返回run、PIO等普通页；`:4207–4219`为run页使用`GFP_KERNEL_ACCOUNT`，x86 PIO页也使用这一标志（`arch/x86/kvm/x86.c:12723–12726`）。run／PIO页建立普通驻留映射后可进入Pss_File，同时有kernel／current记账路径。

这不是额外的guest RAM缓存。已分配但尚未建立用户驻留映射的页也不能靠PSS完整枚举。原记录缺少映射偏移和逐映射统计，不能仅用4／8行推算实际驻留量；其他KVM映射仍按对应源码路径和实际偏移分别判断。

## 已删除memfd和/dev/zero：按后备类型及写入方式分类

`mm/memfd.c:458–473`明确区分两条路径：普通memfd使用shmem，指定MFD_HUGETLB则使用hugetlb大页。共享的/dev/zero映射由shmem承载（`drivers/char/mem.c:519–527`、`mm/shmem.c:5923–5954`）。

| 分类条件 | 应如何解释 |
| --- | --- |
| 普通shmem后备，页面驻留且被普通映射 | 对应部分进入Pss_Shmem；在实际记账组中计入file及其子集shmem，不能把二者相加 |
| 私有映射写入后发生写时复制 | 新匿名页进入Pss_Anon／anon；同一映射可能混有未复制后备页与已复制匿名页 |
| hugetlb后备 | smaps另列Shared_Hugetlb／Private_Hugetlb，不能仅靠普通PSS或file推断覆盖 |
| 后备页仍驻留但未被进程映射 | PSS不能完整表示后备对象容量 |

shmem页的swap-backed属性、memcg记账和file／shmem计数见`mm/shmem.c:1965–1970、870–875`；hugetlb另列见`fs/proc/task_mmu.c:1230–1266`。

“deleted”只表示无法用原文件名访问，并不表示后备对象或物理页已经释放。原记录没有保存逐映射地址、权限、偏移、后备文件系统类型或逐映射smaps，无法确定每个iov_deque的实际分支和容量。开始时全组Pss_Shmem为4／8 KiB，不能把这些字节分配给某一个memfd，也不能据此把其他未知项记零。本地没有该Firecracker构建的对应memfd实现源码，本轮不按名称猜测其创建标志。

## 明确缺项：ublkc队列命令缓冲

这类映射在上一轮`special_objects`中，**不包含在12／20个未知来源键内**，但仍影响整体物理覆盖。

1. `drivers/block/ublk_drv.c:2705–2738`用`GFP_KERNEL | __GFP_ZERO`分配命令缓冲，没有`__GFP_ACCOUNT`。`include/linux/gfp_types.h:372–373`及`mm/page_alloc.c:5275–5282`说明这条页分配路径没有申请memcg内核记账，不能假设统计组kernel／current已包含它。
2. `ublk_drv.c:1775–1810`通过`remap_pfn_range`把命令缓冲映射给提供程序。该路径设置PFNMAP（直接映射物理页号的标志）和特殊页表项（`mm/memory.c:2808–2828、3061–3074`、`include/linux/mm.h:561`）。
3. 在本机配置下，`vm_normal_page`对这类页返回空，smaps随即跳过普通PSS统计（`mm/memory.c:682–706`、`fs/proc/task_mmu.c:1018–1066`）。**这不是读取更多普通文件mincore就能补齐的缓存项。**

已经确定分配及漏统计的机制，但现有记录没有映射长度、队列数和队列深度，不能恢复该轮字节数。后续可按实际队列配置计算这一项：

```text
每队列映射长度 S = PAGE_ALIGN(queue_depth × sizeof(struct ublksrv_io_desc))
每队列实际分配 = PAGE_SIZE << get_order(S)
命令缓冲分配合计 = 实际队列数 × 每队列实际分配
```

这里的分配按2的幂页数向上取整，不一定等于映射长度。尺寸依据`ublk_drv.c:799–806、2730`；设备信息包含nr_hw_queues和queue_depth（`include/uapi/linux/ublk_cmd.h:344–350`）。应核对设备身份、队列偏移及存活期，避免把多次映射算成多份分配。该结果应标为“按实际配置和源码计算的命令缓冲分配量”，不是page cache，不扩展为全部内核内存覆盖。

## 前一段源码核对的完成范围

此前已核对两组所有保存的特殊来源、映射行数、进程身份和目标设备重复项，保存32处源码摘录并核查关键配置。离线脚本：`.artifacts/host-special-mappings-20261021/analyze.py`，输出及核对记录在同目录。该段仅做源码核对，没有改采集代码；之后的实现与检查见本页前两节。

当前得到的是**分类机制和一个明确的补采项**，没有补造各类容量。原映射覆盖仍为INCOMPLETE，原最终清单不足1747.843750／1739.160156 MiB及SHORTFALL_REQUIRES_RESOLUTION保持；本次发现的命令缓冲不能用来抵扣文件记账差额。整体host物理总量／节省和guest Cached对应的host物理集合仍为NOT_MEASURED。原轻量曲线和指定设备测量继续按各自范围交付。

## 下一步计划供检查

补采、一次真实验证和离线报告整合均已完成，建议本阶段收尾，直接使用上述报告，不再为这些已验证字段安排相近实跑。整体物理总量仍受文件记账归属、完整覆盖和查询非同时等已列明缺口限制；若继续该项研究，应单独约定要补的证据，不把本次报告整合扩大为新采集或完整任务授权。
