# 真实测量的脱敏数值样例

这些JSON摘自2026-10-25 Prettier #6604的四个有效组，不是模拟生成的实验数据。每个对象的驻留页数、字节数、设备号／inode、相对来源、使用者和查询成本保持原值；任务摘要保留原状态、动作数及官方测试结果。

绝对文件路径替换为`REDACTED/object-N`，移除了进程号、服务绑定、命令、stdout、补丁内容、数据库及文件数据。相对运行编号仅用于来源追溯，见`provenance.json`。它不是完整运行日志，不能用来重新验证全部任务动作、恢复连续性和清理。

`grpo.json`／`bpo.json`指向本目录`records/`中的元数据摘录；无需本机`.artifacts`或服务即可计算结果：

```bash
bash scripts/report-host-copy-summary.sh --example --output .artifacts/example-report
```

输出包含`SUMMARY.md`和`summary.json`。汇总器核对对象身份去重、19／133个对象及7／1个使用者、驻留页数、两阶段数值、公式、动作数及BPO生成层分类；未知值不补0。`scripts/export_host_copy_example.py`是原实验主机上的白名单导出工具，重新导出需要原私有JSON，本样例的使用不需要它。
