# 轻量测量方案本机备份（2026-10-21）

按用户要求，在继续改进host测量前保存了当前方案。备份目录：

```text
.artifacts/light-measurement-backup-20261021T064910Z/
```

- `light-measurement.tar.gz`：1222个文件，压缩后13948077字节（约13.3 MiB）。
- `manifest.json`：文件名、大小及备份范围。
- `git-status.txt`、`working-tree.patch`：当时Git状态与已有修改；未提交、暂存或推送。
- `RESTORE.md`：恢复方法，先解压到新目录检查，不覆盖当前工作区。

归档保存scripts、tests、平台配置、README、docs和脱敏证据，并保存最近一次同步后单／双沙箱的host原始计数、PSS及验收元数据。包含当时未提交、未跟踪的相关源码，不只保存Git提交版本。归档文件清单和大小已核对，关键采集代码及报告另按实际内容核对。

不包含运行时大镜像、guest内存归档、凭据或完整任务原始日志，不上传。原始运行产物和第三方依赖留在原位置；重现实跑仍需原输入、服务镜像和资源预算。方案范围与结果以归档内`docs/records/HOST_MEASUREMENT_ACCEPTANCE.md`及`HOST_PHYSICAL_BREAKDOWN.md`为准：曲线、记账增量及指定设备驻留可交付，整体物理总量和guest缓存物理集合未测得。

本次备份位于新增host内存明细选项实现之前；该选项不改变原轻量入口的默认采样和查询顺序。
