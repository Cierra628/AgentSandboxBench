# 完整实跑需要已准备的主机

独立目录已验证的范围是：离线复算、选定单元检查、固定AgentENV源码加补丁及非root构建。完整沙箱重放的证据来自已有实验主机；没有验证全新机器从零部署。不要直接在其他人的服务上运行实验入口。

## 固定源码与构建

AgentENV上游提交：`2f48c9c78ec58e4d73c49aa07cb81d984a4a4184`。`patches/agentenv-v0.1-measurement.patch`包含实际测量及初始文件副本控制的ublk修改，也保留原提供者观测接口，未配置时不启用。许可证见`third_party/AgentENV.LICENSE`。

```bash
bash scripts/prepare-agentenv-source.sh AgentENV
# 或从已有本地Git仓库复制到另一新目录：
# bash scripts/prepare-agentenv-source.sh AgentENV --from /path/to/upstream
```

按固定源码的Cargo.lock准备依赖缓存；构建入口故意使用`--offline --locked`，不自动下载或安装。需要Rust、Cargo、rustfmt、Clippy，以及该源码工作区要求的C/C++构建工具和库。可设置`CARGO_HOME`、`RUSTUP_HOME`、`CARGO_TARGET_DIR`；`ASB_AGENTENV_SOURCE_DIR`可指向另一份已打补丁源码。确切验证工具版本记录在[发布整理验证](VALIDATION.md)。

```bash
# 普通用户运行；输出到新目录，不替换已安装程序。
bash scripts/build-rootfs-copy-daemon.sh .artifacts/release-backend-build
```

脚本检查三个相关Rust文件格式、4项副本控制过滤测试、库Clippy并构建`uvm-ublk-daemon`。其他需要设备／能力的Rust测试不在本版离线检查范围。

## 资源必须另行提供

现有适配器依赖固定任务镜像、已验证的私有模板／卷备份、冻结动作和参考结果，以及可用的AgentENV源服务。这些数据不包含在候选包内；配置中的`.artifacts/`路径和源服务默认值是原主机的准备约定，不是可自动获取的公共依赖。

实跑前逐项核对：

1. Linux KVM、ublk及权限可用，host内核与AgentENV固定版本兼容。
2. 自己拥有可停止／恢复的源服务；用`ASB_AGENTENV_SOURCE_SERVICE`指定。不得复用旧池中预启动VMM作为新统计组里的沙箱。
3. 按相应任务配置准备原冻结输入、参考结果、镜像及验证过的卷归档。Prettier完整副本模式还需修正后的七角色冻结导出。
4. cgroup v2内存及I/O记账可用。先创建统计组再启动私有服务和VMM；不能靠移动旧进程使缓存记账迁移。
5. 检查实际负载和空间。24 GiB卷启动前须严格超过50 GiB可用；完整GRPO为48 GiB卷／超过74 GiB，BPO为64 GiB卷／超过90 GiB；运行中保留原空间保护。

失败现场默认保留。删除失败卷须另获明确授权，并确认无挂载、loop和使用者；不删除其他工作负载或原始记录。源码包不携带凭据，凭据由自己的部署安全提供。

## 已准备主机上的显式入口

以下命令会创建沙箱、写盘并产生实验数据；它们不是发布整理的一部分，也不能替代上面的准备核对。

```bash
# 普通三任务轻量采样：按实际已准备资源选择配置。
bash scripts/use-platform.sh run configs/platform-prettier6604.json
# 另两任务：platform-sphinx7590.json、platform-xarray6992.json。

# GRPO：共享／独立各189动作，0.1秒guest／host计数，PSS关闭。
bash scripts/run-host-full-rootfs.sh configs/platform-prettier6604-host-file.json \
  --schedule grpo --copy-daemon .artifacts/release-backend-build/uvm-ublk-daemon
# BPO：共享／独立各84实际动作，原两次保存及六个恢复／组。
bash scripts/run-host-full-rootfs.sh configs/platform-prettier6604-host-file.json \
  --schedule bpo --copy-daemon .artifacts/release-backend-build/uvm-ublk-daemon
```

新模式保持默认回收，不增加预读、强制驻留或测量暂停；原生快照自身的暂停属于原调度。包内还有为旧适配器保留的依赖代码，v0.1不将精细内核采集、TrEnv-X或在线模型调用列为新机支持范围。
