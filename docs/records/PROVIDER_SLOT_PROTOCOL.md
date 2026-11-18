# ublk缓冲覆盖协议离线核查（2026-10-16）

本页保留离线核查时的判断。随后已完成私有实现、真实设备协议和一次短恢复验证，结果见[GET_DATA验证报告](PROVIDER_GET_DATA_VALIDATION.md)；旧校准数值保持。

**现有“只读”设置不足以保证等待下一请求时缓冲不被改写，因此保留提交获取请求前失效的规则。** 已找到不修改内核的可行方向：只为私有观测设备启用`UBLK_F_NEED_GET_DATA`，让内核先通知写请求，再由后端明确允许复制数据。后端可在允许复制前先使归属记录失效。本次只核查源码和形成方案，没有实现或实跑这个新路径。

此前三任务完整采集、Prettier第24步恢复和真实双分支去重直接复用。短校准的保存前3点、恢复后10点及3,853次完整读取不变；3.9375 MiB完整缓冲页驻留与23张混合边界页仍为内容归属未知，不记为零占用，也不加入当前缓存物理并集。

## 核对的是本机实际内核及现有私有构建

host运行`6.19.9+`，不是guest的`6.1.175-asb-cache-v3`。本机构建树为`/home/yxli/linux-6.19.9-ioctl`，Git提交`7e4e3f1145e0c02604b9afb140594764057e2f56`；已跟踪源码没有修改，仅有无关的未跟踪`tools/ioctl-flexible/config.yaml`，未触碰。

运行内核的GNU build ID（构建标识）与该树`vmlinux`一致：`c49aee38e502b6a335b1bf2a764829a1b9ea2b5f`。已加载ublk模块与已安装模块的build ID一致：`d7e985bdcb4a382296d078aff27798d9b531bd76`；模块`srcversion`均为`6E232E51ECF56FFF80DC7BF`，也与构建树的`ublk_drv.mod.c`相同。

AgentENV仍为`2f48c9c78ec58e4d73c49aa07cb81d984a4a4184`加已有[槽位补丁](../../patches/agentenv-provider-slot-observation.patch)，沿用[短校准中固定的构建证据](PROVIDER_SLOT_CALIBRATION.md#方法和构建)。未修改源码、补丁或二进制。内核身份、Git状态、资源快照和带行号的源码摘录保存在本机`.artifacts/provider-slot-protocol-20261016/`，入口为`kernel-identity.json`和`source-evidence.txt`。

## 为什么不能仅凭只读设置保留归属

以下内核路径均相对上述构建树；AgentENV路径相对本项目的`AgentENV/`。

1. **镜像只读和内核设备只读不是同一项设置。** `storage/ublk-daemon/src/runtime.rs:496–511`把`read_only`转换成无可写上层；`storage/ublk/src/impls/overlaybd_target.rs:269–309`生成设备参数时却固定`basic.attrs=0`。`image.is_read_only()`在创建设备时用于决定是否支持discard，没有设置`UBLK_ATTR_READ_ONLY`。非池化路径见`storage/ublk-daemon/src/server.rs:780–789,844–880`。
2. **补上内核只读属性也不足以建立所需保证。** `drivers/block/ublk_drv.c:579–587`只调用`set_disk_ro()`；`block/blk-core.c:522–540,794–805`中的`bio_check_ro()`对只读设备写请求只告警，不在这里结束请求。`block/bdev.c:1087–1108`会拒绝按路径新开的写句柄，但这不能替代驱动在复制前拒绝写请求的保证。
3. **默认协议先改缓冲，后通知后端。** `drivers/block/ublk_drv.c:957–989,1257–1332`显示，带数据的WRITE先进入`ublk_map_io()`，以`ITER_DEST`把请求页复制进用户缓冲，然后才完成通知后端的命令。READ提交则走`ublk_unmap_io()`的`ITER_SOURCE`，从用户缓冲读取，不改写它（`:992–1011,1091–1120`）。
4. **后端收到WRITE后再拒绝已经太晚。** 当前`storage/ublk/src/dev.rs:177–207`是在收到下一请求后才调用`begin()`；只读镜像写失败发生在更后的`storage/overlaybd/src/image/image_file.rs:864–880`。默认协议下内核复制可先于这两个位置，采集器可能在记录仍有效时读到已改写的缓冲。

因此，不能只删除`before_fetch()`，也不能只补只读属性或在`handle_write()`里拒绝写。这里确认的是协议缺少覆盖前通知，不是观察到旧校准实际出现了WRITE。旧元数据没有保存协商后的设备flags（设备功能标志），不能事后把旧点升级为新协议证据。

## 推荐的最小改动：先通知，再复制

`UBLK_F_NEED_GET_DATA`表示写请求先通知后端、不立即复制负载；收到通知后，后端再发`UBLK_U_IO_NEED_GET_DATA`允许复制。当前内核已提供这条路径：

- `drivers/block/ublk_drv.c:1309–1324`：需要复制的写请求返回`UBLK_IO_RES_NEED_GET_DATA=1`，在`ublk_start_io()`之前返回。
- `:2353–2369,2478–2490`：收到GET_DATA命令后才调用`ublk_start_io()`，执行实际复制。
- `:2446–2452`：检查当前是否需要GET_DATA；不能跳过这一步直接提交写失败。
- `:3196–3212`：ADD_DEV会协商flags；USER_COPY、ZERO_COPY或AUTO_BUF_REG会清掉NEED_GET_DATA，必须检查实际返回值，不能只检查申请值。
- `include/uapi/linux/ublk_cmd.h:90–109,169–176`和`Documentation/block/ublk.rst:296–323`给出命令及复制顺序。固定依赖`libublk-rs-sys`提交`c6a3e069bafbde3e34a68dba3e16be01cf3fbe56`的缓存头文件已有这些常量，无需升级依赖。

后续实现限定在已启用观测的非池化`memory/image.json`用户缓冲设备：

1. 在`storage/ublk/src/ctrl.rs`增加设置NEED_GET_DATA的builder入口；在实际使用的非池化创建入口`storage/ublk-daemon/src/server.rs:create_overlaybd_device()`调用。保留其他设备和未启用观测的原始路径。ADD_DEV后确认该位确实保留，并把协商flags和失效方法写入观测身份记录；若未保留，仍在提交获取前失效。
2. 在`storage/ublk/src/queue.rs`补发GET_DATA的现有命令封装。使用同一队列和槽号，不更换缓冲，不增加睡眠或人为等待。
3. 在`storage/ublk/src/dev.rs`同时处理初始FETCH和后续COMMIT_AND_FETCH的完成结果。遇到返回1，先使元数据无效、有效长度归零并更新代次，再调用发送GET_DATA的函数；收到正常完成后才能调用原目标处理函数。映射失败后内核可能重新排队，因此再次返回1时也要按同一顺序处理。错误和退出维持失效，不能把返回1当作普通成功。
4. 仅在上述协商成功的路径，完整READ后的有效记录可跨越提交和等待；下一请求开始时，继续在调用目标读取函数前执行现有`begin()`。新READ、短读、失败及释放仍使整个旧记录失效，不保留旧尾部。原采集器的代次、内容状态、guest身份及PFN（物理页号）配对规则继续适用；更新方法说明，不把旧原始数据解释为新失效时机。

失效必须在**发送GET_DATA或启动新读取之前**完成，不能只放在对应`.await`之前的任意位置：`storage/util/src/io_ring/uring.rs:433–439`显示`send()`调用时就把请求放入队列。新READ的失效应发生在构造并调用目标处理路径之前，覆盖其第一次修改缓冲。

源码支持的时序推导如下，尚未作为运行结果：

| 阶段 | 缓冲内容与归属记录 |
| --- | --- |
| 完整READ结束 | 发布本次偏移、有效长度和新代次 |
| 提交READ并等待下一请求 | 内核读取缓冲；在协商成功的协议下不会提前复制下一WRITE |
| 下一READ已到达 | 先失效，再启动用户态填充 |
| 下一WRITE返回1 | 先失效，再发送GET_DATA允许内核覆盖；其后沿用目标原处理结果 |
| 出错或释放 | 失效，不再用于归属 |

推荐这条方向，而不是修改共享内核或靠只读名称推断：它提供明确的复制前控制点，也不依赖未来请求一定都是READ。它会给需要取数据的WRITE增加一次往返；实际成本需在后续验证中单独报告，不能预先写成零开销。

## 后续验证条件与本次结束位置

当前代码继续采用原保守规则。下一阶段先实现上述私有开关和不触碰设备的最小回归用例：读取完成后跨等待保留；下一读取启动前失效；初次及后续取请求的返回1在发GET_DATA前失效；再次返回1、短读、错误、取消和释放不留下有效旧内容；未协商成功及未启用观测时仍走旧规则。核对有效flags、身份说明和旧数据兼容性，完成定向格式、构建和测试。该实现与测试本次未执行。

真实协议验证的范围需在下一阶段确定：在私有设备上确认READ→READ和READ→WRITE的实际顺序，特别是覆盖已开始前元数据必已失效；只读文件内容、错误结果和释放状态分别检查。若之后恢复短校准，要同时取得跨配对窗口有效的正值归属、逐页独立复算和原三层去重证据；没有正值仍按实际未知量报告，不通过延迟请求、加轮次、预读或强制驻留补齐。三任务完整采集、Prettier完整恢复及真实双分支均不重跑。

本次Git修改已记录并保留。资源快照为2026-10-16 11:18（Asia/Shanghai）：load average为0.50/0.47/0.34，可用内存约470 GiB，1秒观测无I/O等待，可用磁盘约55.74 GiB，12个现有VMM。没有新校准、VM、设备创建、服务替换、额外暂停、任务文件预读或强制驻留；没有清理数据、暂存、提交或推送。未来24 GiB卷启动仍需重新确认超过50 GiB可用，并维持20 GiB保护量和6 GiB余量。
