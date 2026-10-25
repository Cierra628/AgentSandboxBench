# 第三方代码与发布权限

## AgentENV

来源：<https://github.com/kvcache-ai/AgentENV>，固定提交`2f48c9c78ec58e4d73c49aa07cb81d984a4a4184`，MIT许可证，Copyright (c) 2026 AgentENV。原许可证完整保存在[AgentENV.LICENSE](../../third_party/AgentENV.LICENSE)。随包补丁包含该固定源码的修改，构建需另取上游源码；候选包不捆绑上游checkout、二进制或运行状态。

## IncrementalDAX_moti及任务资源

既有实验参考固定仓库<https://github.com/132ash/IncrementalDAX_moti>提交`a9bc7deadb68a6e24b7242eab343dfedf953ea38`，子版本记录在`configs/source-revisions.json`。本轮未找到该仓库根许可证，因此不捆绑其源码或补丁。冻结任务轨迹、镜像、原始输出和数据集也不随包分发，取得和使用权由使用者另行确认。

## 本项目新增代码

当前根目录无项目许可证。所有者尚未确认新增代码的许可选择，公开发布前需补齐许可证及必要权利确认；本次发布整理不代替此决定。

Python绘图依赖不捆绑，版本见`requirements-release.txt`；各依赖使用自身许可证。本地凭据、AGENTS.md和实验运行数据均不属于发布内容。
