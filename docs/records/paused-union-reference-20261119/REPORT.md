# 36,432 页与 37,352 页：计数规则相同，观测时刻不同

基线：`handoff-20261118`，`dd9ba4c9ebae59db68055da5e8ad566b87d6eb16`。本轮 `bash scripts/check-platform-offline.sh` 退出码 0，末行 `status=PASS scope=platform-offline`。

证据文件（以下用 N、O 指代两次运行的 guest-cache 目录）：

- N：`.artifacts/storage-isolated-service/20261119T070352Z-835971/guest-cache/` 的 `result.json`、`{before,after,host,direct,lower}.bin` 和 `frames.json`。对应前次提交 `f2c5ae6:docs/records/fast-export-pause-20261119/REPORT.md`。
- O：`.artifacts/storage-isolated-service/20261118T042313Z-432686/guest-cache/` 的 `events.json`、`live-cache/restored-g1/samples.jsonl` 及同目录的 `pair-0000-{before,after,host,backend,lower}.bin`、`pair-0000-provider.json`。索引为 `results/seven-restored-scale-wait-20261118/REPORT.md`，取其中 **O 列**，不是该报告的新七支数据。
- 计算入口：`f2c5ae6:scripts/run_fast_export_pause.py:124–159`；O 同次保存的 `../code/live_task_cache.py:315–338`（相对 O）。判定规则见基线 `scripts/provider_slot_cache.py:204–344`。

**判定：同口径。** 两次都对一个恢复分支的 guest 清单，计入四个来源中被确认为当前副本的宿主物理页，按物理页号取并集。把基线的同一个 `account_observed_provider` 用在两套原始输入上，分别重算出 **36,432 和 37,352 页**，与各自保存值一致。以旧恢复首点为参照，数量保住率为 **36,432 / 37,352 = 97.54%**，少 **920 页（2.46%）**。这是两次独立运行的数量比，不是同一批物理页的存活率。

## 哪些页实际计入了

表内单位都是 4,096 字节页。前三行是各来源内部去重后的数量，跨行有重复，不能相加。新旧两个来源集不是按设备路径名匹配的，而是分别对各自分支做归属。

| 来源或合计 | N 暂停查询 | O 恢复首点 | 原始字段与算法 |
|---|---:|---:|---|
| VMM 页表 | 27,858 | 12,764 | host 行：guest 页号、三次页表项；取符合条件且在内存中的页表项的宿主物理页号。N `identities.host_confirmed_cache_bytes`、O `host_vmm_confirmed_bytes` 可按字节对账 |
| RAM 直接后备 | 7,183 | 15,494 | N direct / O backend 行：guest 页号、偏移、首末查询结果；取有效的当前物理页号。O `host_snapshot_current_bytes` |
| 快照内存下层 | 7,587 | 16,885 | lower 行：guest 页号、下层偏移、首末查询结果；同样只取当前物理页号。O `host_lower_current_bytes` |
| 前三来源并集 | 36,423 | 37,277 | 对上述三组物理页号取并集；两次 `host_provider_current_bytes=0`，故也等于不含分段扩展的 `host_four_current_union_bytes / 4096` |
| provider 当前页 | 9 | 75 | 两帧槽位：`rows[].before/after, pagemap, segments`；`host_provider_combined_current_bytes / 4096`。这两点均由分段记录贡献，且与前三来源无重合 |
| **四来源最终并集** | **36,432** | **37,352** | N `result.json:account.host_four_segment_current_union_bytes = 149225472`，另存 `union_pages=36432`；O `samples.jsonl[point=0].host_four_segment_current_union_bytes = 152993792`。均除以 4096 |

N 的直接后备、下层分项取自原始行表，未将查询页次或全文件 mincore 驻留数当作归属页数。provider 是设备级槽位，只把与本次 guest 候选匹配的当前物理页放入并集；没有将设备总驻留按分支分摊。若只读名称相近的 `host_four_current_union_bytes`，就会漏掉表中的 9 和 75 页。

**归属与未知。** 两次都从首份 guest 清单取有效页号，要求末份清单中同一页的生命周期标记仍匹配；三次 VMM 页表的物理身份相关位必须一致。后备和下层还要求首末结果一致、在内存中且属于当前版本；VMM 文件映射与稳定后备页号冲突时不予确认。provider 用两帧稳定的页号、槽位字节范围与 guest 偏移核对；旧副本、混杂或未归属的槽位页不计入当前并集。N `identities.metadata_before/after.records` 均为 29,240，O `metadata_before/after.records` 均为 21,799。N `account.host_four_segment_unconfirmed_guest_capacity_bytes` 为 0，O 同名字段为 65,536 字节（16 个 guest 页容量）；后者保留为未确认，不补进并集，也不解释为宿主不驻留。

**跨来源去重。** `provider_context` 从合格的页表／查询结果中取宿主物理页号（PFN）；`account_observed_provider` 将前三来源、整槽及分段识别到的当前 PFN 作集合并集，再乘页大小。偏移用于归属，不是去重键。因此同一 PFN 即使由多个来源找到也只算一次，不同 PFN 即使内容相同也分别计入。两个分支的 guest 候选数不同、物理副本组成也不同，但计算规则一致。

## 为什么 920 页不能解释为采样时差造成的损失

| 时间或范围 | N | O |
|---|---|---|
| 实际样本 | 一次暂停查询，一个恢复分支 | 双实例校准的恢复分支 restored-g1，point=0；不是七支之一，也不是七支之和 |
| 距各自恢复返回 | +0.934085 秒完成暂停 | +4.703699 秒开始首采，含首份 guest 导出 |
| 实际宿主读取窗口（距恢复返回） | +0.935516 至 +1.433230 秒，guest 暂停 | +4.724982 至 +5.255091 秒，guest 运行 |
| guest 身份核对 | 暂停前导出，恢复运行后再导出 | 在同一运行中采样前后导出，无暂停 |

时间来源：N `result.json:events.restore_return/pause_response/host_start/host_end`、`stages.guest_before/guest_after`；O `events.json[stage=restore].start_monotonic_ns + duration_ns` 为恢复返回，`samples.jsonl:host_sample_start_ns` 为首采开始，`host_stage_costs.vmm_first.host_start_ns / maps_after.host_end_ns` 给出宿主读窗。按题中的暂停完成对旧首采开始，旧点晚 **3.770 秒**；按宿主首读对首读，旧点晚 **3.789 秒**。934 ms 是恢复到暂停的耗时，不是暂停后等了 934 ms 才读。

**时刻差是否能解释 920 页：现有记录不能判定。** 旧分支自身的五点依次为 37,352、37,514、37,514、37,514、3,985 页，说明取点时刻确实影响读数；37,352 是恢复首点，不是该序列最大值。两批还不是同一份快照：N `result.json:objects.lower_size` 为 301,363,200 字节，O `live-cache/restored-g1/lower-metadata/metadata.json:size_bytes` 为 472,018,944 字节。缺少同一快照在两个对应恢复时刻、相同暂停条件下的四来源并集，不能从这两个数分离时间、快照内容和运行状态的影响。N 的两条全文件驻留曲线也不能补这个缺口。后续引用宜写“达到旧恢复首点确认页数的 97.54%”，不写“暂停保住了真实峰值的 97.54%”。
