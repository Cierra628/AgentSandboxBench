# Cython代理：规则归属已补，发现并修正防火墙工具不一致

最新结果：两份失败卷均已获准定向删除，卷外记录保留；nft修正版已完成原83步、测试、两端曲线及全部清理，见[完整测量报告](EXTERNAL_CYTHON_LIGHT.md)。以下保留本阶段的过程与当时结论。

## 已按授权删除首个失败卷，并验证一次临时规则

用户确认两项后，重新检查无挂载、loop和使用者，仅删除原32 GiB失败卷：

`.artifacts/storage-isolated-service/20261030T090338Z-2643675/volume.ext4`

全部卷外日志保留，可用空间恢复约63.189 GiB。删除收据为`.artifacts/external-cython-network-fix-20261030/approved-volume-deletion.json`。没有扩大删除范围。

随后补上单guest临时INPUT规则和必要离线检查，执行原范围一轮。新guest创建及工具上传通过，实际路由来源为`10.211.0.1/32`，接口`veth-1`；仅允许该接口、来源、代理目的`192.0.2.17/32`及随机TCP端口。原HEAD检查前后和清理前保存规则计数，不清零、不抓业务内容。

这一轮仍在HEAD阶段立即报告连接被拒绝。允许规则的计数前后均为0；原83步和guest采样仍未开始。host采集572点通过，仅用于准备／失败诊断。不能把规则存在视为网络已通，不能从零计数推断guest没有发出请求。

## 实际问题：主机默认legacy，服务使用nft

本机有两套可并存的iptables规则表。只读核对显示：

| 对象 | 实际结果 |
| --- | --- |
| 主机默认`iptables --version` | `iptables v1.8.10 (legacy)` |
| 主机`iptables-nft --version` | `iptables v1.8.10 (nf_tables)` |
| 固定源服务容器的`/etc/alternatives/iptables`链接 | `/usr/sbin/iptables-nft` |
| 本次临时规则及计数命令 | 默认`iptables`／`iptables-save`，即legacy |

因此本次允许规则和服务的默认拒绝规则不在同一套表中。legacy表里允许，仍不能解除nft表里的拒绝。这是已确认的实现缺陷，解释了为何“规则已添加”不足以验证访问路径。上轮仅凭当前源码推测INPUT的分析还漏掉了实际运行工具差异；首轮具体拒绝规则的计数仍没有保存，不能追溯补作命中证据。

修正后，新增路径的添加、检查、删除统一使用`iptables-nft`，计数统一用`iptables-nft-save`，与固定服务一致，不修改系统默认工具链接。收据明确写`backend=nft`，不把legacy计数混作nft证据。修正版本的真实HEAD与原83步尚待重试。

## 所有权与失败清理

改动仅涉及`external_guest_proxy.py`、`run_external_workload.py`和`run_host_external_workload.py`的Cython分支；旧Django／C4不启动代理或临时规则，原预算、输入及生成程序不改。

- 只接受本私有服务网段中唯一实际`/32`路由，不预先把整个`/16`开放。
- 规则带本轮唯一标记；添加前拒绝接管已存在的同名规则。
- 在添加前保存`PLANNED`所有权收据，添加后为`ADDED`。添加超时但实际成功时，外层仍能按收据精确检查和删除。
- 外层`GuestProxy.stop()`按记录重建受限规则参数，执行精确`-C/-D`并确认不存在，记录`REMOVED`。不清空规则链或重置计数。
- 删除规则失败也会继续关闭本次代理进程和临时地址，整轮不会记成功；收据、计数和日志均留在运行卷外。
- HEAD前后规则读取属于准备检查，发生在guest采样和任务计时之前；它不是周期采集开销。

新增5项规则检查及原11项Cython检查通过，旧外部任务13项继续通过，共29项。检查覆盖单来源／单目的／单端口、nft工具一致、计数保存、精确清理、添加超时恢复、拒绝接管已有规则、路由异常及清理失败后仍终止代理。它们使用替代子进程，不修改真实防火墙。原隔离存储代码未改，复用此前27项有效证据。

## 本次清理已完成，新失败卷保留

实际核对guest和私有服务已删除，源服务恢复停止；代理进程、地址、临时规则和loop均清理。legacy和nft两套表均无本项目`asb-relay-`规则残留，没有活动Docker容器或Firecracker。

新失败卷为：

`.artifacts/storage-isolated-service/20261030T091812Z-2649167/volume.ext4`

32 GiB卷按失败保留规则留下，无挂载、loop和使用者。当前可用约31.18 GiB，低于>58 GiB门槛；上一份卷的删除授权不套用到这一份。已另请用户确认这一份卷的定向删除；未获确认前不删、不再分配。

## 下一步最小范围

获准删除本次新失败卷后，重查资源，再以已修正的nft工具重试原一个8 GiB／2 vCPU guest、83步，不扩大规则或追加任务。先检查原HEAD成功且本次nft允许规则计数增加，再启动guest采样和任务；原参考退出、两环境各18通过、NumPy2.3.0及三个`.so`等条件保持。

完成后交付有效时延、两端0.1秒曲线和成本、卷边界与全部自有清理。任何首个失败先诊断，不跳过检查或放宽原业务结果。

## 证据

- [脱敏摘要](evidence/external-cython-relay-fix-20261030.json)
- 修补前后源码、29项检查、两套规则收尾快照、容器工具链接及删除收据：`.artifacts/external-cython-network-fix-20261030/`
- 本轮驱动：`.artifacts/external-cython-live-20261030-relay/`
- 规则收据、前后计数及host采集：`.artifacts/host-external-workload/20261030T091811Z-2649156/`
- 失败现场：`.artifacts/storage-isolated-service/20261030T091812Z-2649167/`
- [首轮出网失败记录](EXTERNAL_CYTHON_VALIDATION.md)
