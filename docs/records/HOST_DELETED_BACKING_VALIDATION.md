# 已删除共享内存查询：40 KiB自有对象验证通过（2026-10-21）

**独立查询函数已经实现，一次自有小对象实测通过：原描述符关闭后仍可通过map_files查询memfd和共享/dev/zero后备；memfd已映射RSS仅4 KiB，整后备缓存为8 KiB，包含未映射的测试页。重复映射只计一次。** 两个不同对象合计12 KiB缓存，查询用时1.069606毫秒；前后独立驻留检查一致，自有句柄和映射已释放。

本轮没有启动AgentENV、VMM或真实任务，没有新建卷，没有预读任务文件、锁页或触发全局回收。只是创建并写入自有测试数据，逻辑容量共40 KiB，低于同意的1 MiB范围。实际任务中此前2／4个已删除对象的完整驻留仍未查询，原未知字段和整体物理节省判定保持。

后续[显式明细接入与跨进程／超时回收](HOST_DELETED_BACKING_INTEGRATION.md)已完成；本页保留同进程自查询的原用时和证据，两种范围不混用。真实AgentENV对象仍未补采。

[源码及条件](HOST_QUERY_SOURCE.md) · [原增量证据](HOST_INCREMENT_EVIDENCE.md) · [脱敏实测数据](evidence/host-deleted-backing-20261021.json)

## 本轮实现在哪里

- `scripts/host_deleted_backing.py`：独立函数`query_deleted_backing(region)`及按设备号／inode去重的`query_deleted_backings(regions)`。输入是已采到的映射和PID启动身份；没有接入现有监控调用链。
- `scripts/validate_host_deleted_backing.py`：只创建自有memfd和共享dev-zero对象的验证入口。
- `scripts/run-host-deleted-backing.sh`：20秒有限超时，终止后再留2秒清理；Python入口在异常或正常退出时均释放本次资源。
- `tests/test_host_deleted_backing.py`：10项模拟元数据和系统调用的测试，不查询真实服务、设备或进程缓存。

查询函数只读PID身份和maps元数据，通过map_files先建立O_PATH身份引用，确认普通TMPFS后备，再只读重开并调用cachestat。不读取进程内存或文件内容，不创建数据映射，不调用回收／预读／锁页接口。源映射、PID或后备元数据变化时不生成有效容量。引用在函数退出前关闭。

本轮直接在创建对象的进程中查询自己的映射。跨进程目标访问、真实AgentENV对象和监控入口的超时接线尚未验证；后续接入时应在有超时的独立进程中调用，不能把本次自查询用时当作真实任务开销。

## 实际对象、动作和结果

基本页大小为4096字节，内核为6.19.9+。

| 自有对象 | 逻辑容量 | 保留映射 | 查询时已映射RSS | 整后备缓存 | 查询nr_evicted |
| --- | ---: | ---: | ---: | ---: | ---: |
| memfd | 32 KiB | 两段各4 KiB，均对应文件第0页 | 合计4 KiB | 8 KiB | 0 |
| 共享/dev/zero后备 | 8 KiB | 一段8 KiB | 4 KiB | 4 KiB | 0 |

memfd只写入第0和第7页，查询前已关闭原fd，且没有任何映射覆盖文件第7页；两段第0页映射中只触碰第一段，用于准备测试。非触页mincore独立检查在查询前后均得到索引`[0,7]`。cachestat返回2个缓存页，包含无法从这两段映射RSS取得的第7页。

共享/dev/zero只在测试准备时写入第0页，关闭原字符设备fd；其映射后备实际为普通TMPFS文件，不是重新打开字符设备查询缓存。查询前后mincore均得到索引`[0]`，cachestat返回1个缓存页。两对象均没有匹配的打开描述符，且后备nlink为0。

按设备号／inode去重后输出两个对象，映射数分别为2和1；合计为8+4=12 KiB，没有把memfd的两段映射算成16 KiB。O_PATH直传cachestat实际得到EBADF（errno=9），核实后只读重开的路径成功。

| 本次直接计时 | 用时 |
| --- | ---: |
| memfd查询，含身份、maps和后备核对 | 0.627502毫秒 |
| 共享dev-zero查询，含身份、maps和后备核对 | 0.428728毫秒 |
| 两对象查询及去重合计 | 1.069606毫秒 |

这是一次小型自查询的实际用时，不是采样开关对照、稳定均值或真实任务减速率。此轮nr_evicted均为零，只验证了没有交换条目的路径；交换条目、权限失败等状态由下面的模拟测试覆盖。

## 失败状态和资源释放

10项测试覆盖：系统调用参数和40字节返回结构、ENOSYS错误、O_PATH与只读打开顺序、实际身份检查函数的PID／映射变化、后备身份不符、hugetlb或字符设备不重开、访问／重开失败、查询后元数据变化、交换条目保留部分未知、重复对象及不完整合计。测试均通过，没有执行真实系统调用来制造这些失败。

- 查询成功且没有交换条目：`OBSERVED`，保存原始计数和整后备缓存观察量。
- 有交换条目：`PARTIAL_SWAP_ENTRIES`，保留可见缓存字节，整后备缓存量为null。
- 身份变化或查询失败：`UNKNOWN`；非普通TMPFS为`UNSUPPORTED_BACKING`。原始错误保留，容量不补零。
- 任一对象未完整查询：对象合计保持未知。即使缓存查询成功，页记账归属仍为`NOT_MEASURED`。

实测中查询前后缓存驻留索引完全一致。查询函数退出后原始fd集合恢复；最后所有自有映射消失，句柄全部关闭，cleanup无错误。没有留下服务、卷或需要删除授权的运行镜像。

## 如何复现与查看结果

```bash
bash scripts/run-host-deleted-backing.sh
```

每次创建新的`.artifacts/host-deleted-backing/<时间-进程号>/result.json`，终端只输出实际状态、阶段和目录。也可用`--output NEW_DIRECTORY`指定一个新目录。仅这条入口会创建和写入自有测试数据；普通host采样入口和默认频率不变。

本轮原始结果：`.artifacts/host-deleted-backing/20261021T102642Z-19141/result.json`。实现副本、10项测试日志、独立离线核对和本次文档修改前版本：`.artifacts/host-deleted-backing-validation-20261021/`。原始进程号、地址和完整元数据只保存在本机结果；脱敏证据只保留对象类别、容量、计数、耗时和状态。

## 下一步计划供检查

本轮独立实现和一次小型验证完成，原定的显式接入、跨进程及超时释放现已[完成](HOST_DELETED_BACKING_INTEGRATION.md)，不再重复小对象自查询。后续只建议按该报告的范围补一次原快照后30动作，不重跑前缀或完整任务。

这一步补的是已删除后备缓存，不解决旧文件页初始记账归属。整体host物理节省和guest Cached对应的物理集合继续保留未测。
