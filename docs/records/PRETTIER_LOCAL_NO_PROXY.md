# Prettier本地输入无代理模式：实现与短验证

## 新选项只使用现成本地材料，不再注入17891

2026-10-31完成显式`--local-no-proxy`，本报告介绍Prettier #6604、持久化输入、单沙箱完整原29步和current轻量采样。旧入口的参数、默认行为和冷创建请求字段保持；本项目后续实际操作必须选用无代理路径，不再运行旧代理路径。随后[Sphinx也已完成一轮完整无代理验证](SPHINX_LOCAL_NO_PROXY_PLAN.md)，不改变这里已验证的Prettier行为。

最初离线实现没有联网或启动。随后一次仅创建检查在私有服务启动时失败：本地regctl入口未支持启动必需的`version`命令，触发依赖重新获取，直连GitHub被重置。第一次没有创建guest；版本修补后第二次已成功创建并删除一个guest，服务／guest代理环境检查通过，实际regctl仅`version`和`manifest`。实际IPv4规则允许公网DNS，第二次原记录因“只允许内部DNS”检查保留失败。本次已修正新检查口径，并完成一轮完整无代理29步／原评测：原重放和检查通过，官方原失败签名保持，实际写文件边界及全部清理通过。[完整结果与曲线](../../results/prettier-local-no-proxy-20261031/REPORT.md)已放在results。[两次验证详情](PRETTIER_LOCAL_NO_PROXY_CHECK.md)保留结果、失败条件和清理证据。

## 仅清除Shell代理变量还不够，三处一起处理

1. 外层清除大小写HTTP_PROXY、HTTPS_PROXY、ALL_PROXY六个变量；新systemd任务显式置空这些变量，避免继承管理器环境。
2. 新私有服务不走旧的17891注入分支，并显式置空六个代理变量，覆盖容器镜像可能自带的默认值。用户的代理服务、全局配置及原服务均不修改。
3. guest创建请求设置六个代理变量为空，并使用现有API的`allowInternetAccess=false`请求关闭出网。创建后通过登录Shell执行一次环境检查，只有输出`guest-no-proxy-ready`才继续原任务；该检查失败仍沿用原guest清理。

原29步命令及验收不改，但新模式明确改变guest联网策略，并增加一次准备检查。以后得到的耗时应单列，不能无条件与旧有代理数据混算采样影响。默认回收和0.1秒采样不变。

## 命中本地镜像缓存后，原始清单查询也不再联网

源码显示后端可能先执行`regctl manifest get --format raw-body`，再使用转换缓存；因此只检查19个本地数据文件还不足以避免请求注册表。

本地已有原始固定镜像清单4312字节，以及Docker保存的对应镜像配置9549字节。新增辅助模块只读取这两份小元数据，核对它们与原镜像的固定OCI摘要，不读取镜像层正文。真实本地导出和完整性检查已通过，总文件大小13,861字节。

恢复并导入输入后、创建私有服务前，新路径还检查固定image配置及其metadata、19个不同的本地lower文件和tools配置；远程或辅助lower、缺文件、空文件及错误目录会失败。原备份恢复、完整性校验和输入身份核对继续保留。

只在本次私有卷内替换regctl入口，保留原入口副本。镜像元数据入口只接受两种精确请求：

- 原固定Prettier镜像的清单查询，返回本地原清单；
- 对应配置对象查询，返回本地原配置。

此外，修补后的入口接受唯一的`version`命令，返回安装前从已校验的本地原始ELF可执行文件取得的版本原文，要求独立版本字段为`v0.11.5`。这满足后端的依赖检查，不伪造版本，也不调用被保存入口作请求回退。其他请求包括镜像复制、其他镜像和未知配置均退出78并记录`blocked`。它是这个固定镜像的本地元数据分派，不是通用镜像代理。

该模式清除本实验代理、限制镜像命令为本地输入，并设置`allowInternetAccess=false`。版本问题已通过第二次真实启动验证，本地镜像创建和两端代理环境检查通过。实际规则是普通新IPv4连接拒绝，但提前放行到`8.8.8.8:53`的TCP／UDP DNS；该字段不等于完全断网。首次依赖直连失败记录保留，第二次没有镜像或依赖下载日志。

## DNS检查已按实际语义修正，原29步没有显式下载命令

本次只离线修改新模式的规则检查和帮助说明。`PASS`现在表示符合AgentENV的默认IPv4限制：已有连接可返回，一台明确的IPv4 DNS服务器在53端口分别放行TCP和UDP，其余新转发连接被拒绝。不再要求DNS必须位于内网；必须恰有一个`/32`目的地址及两种协议，其他端口、网段放行、多个DNS或额外允许项仍失败。

检查输出新增`dns_exception`，记录地址、端口、协议及是否公网，并明确`complete_egress_isolation=false`。已有`guest_internet=false`保留，其含义通过`guest_internet_scope`说明为API请求字段，而非完全隔离的观测结论。服务／guest代理环境检查和普通入口的请求字段不变，未修改后端、主机DNS或防火墙。

保存的第二次真实规则已离线解析，结果是`8.8.8.8:53`、TCP／UDP、公网DNS。17项相关检查通过，短报告保留DNS字段，未知允许项仍拒绝。第二次原始`FAIL`、查询后身份复核缺失和未执行的最终写文件边界检查均保留；没有重新启动。

原29步已逐条读取，并将执行清单与原采样命令逐条比对一致：

| 命令类别 | 步数 | 联网判断 |
| --- | ---: | --- |
| Shell读取／检索／本地Git | 17 | 无显式网络操作，Git仅log、diff、checkout等本地动作 |
| 本地Node解析／格式化 | 7 | 调用已有源码与模块，无显式安装 |
| 本地补丁和测试文件写入 | 2 | 本地文件操作 |
| `yarn jest` | 3 | 第23、26、29步，执行已有Jest；没有`yarn install` |

第2步缺少`rg`的原预期退出127继续保留，不额外安装工具。原输出显示`node_modules`及`/testbed/node_modules/.bin/jest`；固定镜像的小配置记录了构建时安装Node、Yarn和项目依赖。这些构建命令不在本次任务运行路径中，无需再拉镜像或安装依赖。

独立检查脚本只加载本地Prettier并检查输出。额外官方评测是`nvm use 20.16.0`、`npm pkg set scripts.lint="echo noop"`及`yarn test ... --json`：切换已有Node版本、修改本地package字段和运行测试，不是`nvm install`或`npm install`。任务输入加载使用本地`load()`，不会调用有下载能力的`prepare()`。

包脚本、Jest配置和测试辅助代码的全部间接行为未从rootfs逐项展开，因此“无显式下载命令”不等于已验证运行时零请求。原采样的Yarn输出曾有无互联网连接警告，但本地Jest仍完成；这支持使用现有依赖，不足以证明没有DNS请求。随后一轮完整任务已保留原命令和出网限制成功完成，没有自动补装、加`--offline`改写任务或回退代理；仍未测量运行中的网络请求量。

- [本次脱敏核对](evidence/prettier-no-proxy-dns-offline-20261031.json)
- 本地逐条命令、源码依据、修补前后副本和检查日志：`.artifacts/prettier-no-proxy-dns-offline-20261031/`

## 入口与目前的前提

本轮已使用下面的完整入口执行原29步和原评测；调用前须按原条件准备固定源服务：

```bash
export ASB_AGENTENV_SOURCE_SERVICE=asb-source-rebuilt-20261018T160925Z-2726362
bash scripts/run-host-file-monitor.sh \
  configs/platform-prettier6604-host-current.json \
  --persistent-input --local-no-proxy
```

后续已接通并完成Prettier两对开关对照：关闭只换为`platform-prettier6604-host-current-off.json`，仍保留上面两个选项。运行内存未测，结束后输入驻留查询保留；92项离线回归及四次真实运行通过，见[开关方案与实现](PRETTIER_LOCAL_NO_PROXY_SAMPLING_PLAN.md)和[两对结果](../../results/prettier-local-no-proxy-sampling-controls-20261031/REPORT.md)。本报告原单轮的数字不改写。

沿用现有固定源服务就绪条件、准备前磁盘严格>54 GiB、来源释放后分配原8 GiB卷前>34 GiB、MemAvailable>64 GiB、失败保留及定向清理规则。Sphinx及[Xarray](XARRAY_LOCAL_NO_PROXY_PLAN.md)另有显式实现和自身启动门槛，均已完成一轮完整验证；不支持外部任务、GRPO／BPO、恢复、双沙箱或关闭采样，不能只给Cython加这个选项。

小元数据目前从本机Docker的overlay2镜像配置存储及已有固定清单导出；缺失会在恢复归档前停止，不自动重新拉取。导出副本保留在本次记录的`verified-metadata/`中，不能把本机布局当作其他Docker存储驱动已经验证。

## 最初离线实现的99项相关检查通过

| 检查范围 | 项数 | 结果 |
| --- | ---: | --- |
| 新模式、清空环境、本地缓存缺失、元数据固定摘要、regctl拒绝回退、创建API字段及外层分派 | 9 | 通过 |
| 原持久化输入、导入／释放、三任务分派和失败清理 | 28 | 通过 |
| 外部任务和旧冷创建调用合同 | 48 | 通过，复用该候选下有效结果 |
| 平台默认配置及原离线输入验证 | 14 | 通过 |

测试只使用临时玩具文件、模拟Docker／服务／API调用和既有离线输入；小型regctl测试实际执行本地Shell分派，不访问网络。六个运行脚本语法和两个帮助入口通过。旧冷创建默认请求没有新增联网策略或envVars字段；新条件只作用于显式选项。

首次新测试的玩具缓存目录使用了错误的临时摘要名称，已修正。旧同步分支测试还缺后来加入的两个默认变量，已补齐测试环境；未改变原同步行为或放宽验收。早期失败日志保留，最终有效检查均通过。

- [脱敏证据](evidence/prettier-no-proxy-implementation-20261031.json)
- 新辅助模块：`scripts/prettier_no_proxy.py`；回归检查：`tests/test_prettier_no_proxy.py`
- 本地代码前副本、差异、导出小元数据及检查日志：`.artifacts/prettier-no-proxy-implementation-20261031/`
- [本地镜像与依赖清单](LOCAL_INPUT_INVENTORY.md)

## 仅创建检查入口与剩余验证

已新增显式短检查入口，复用同一恢复／导入／私有服务／清理流程：

```bash
export ASB_AGENTENV_SOURCE_SERVICE=asb-source-rebuilt-20261018T160925Z-2726362
bash scripts/run-host-file-monitor.sh \
  configs/platform-prettier6604-host-current.json \
  --persistent-input --local-no-proxy --local-no-proxy-check
```

它只创建一个4 GiB／2 vCPU guest，核对服务和guest代理环境，在该Firecracker的网络命名空间中只读检查IPv4规则，然后删除。不执行29步、评测、guest周期采样或最终输入mincore查询。host轻量采样和原存储保护保留；短检查报告不生成任务曲线或任务通过结论。

当前检查要求实际进程归属于本次私有服务，查询前后身份不变；允许明确记录的一台DNS服务器，其余新转发IPv4连接拒绝，不发外网探测。第二次旧检查因公网DNS失败的记录不改写，现有规则解析已离线修正，不为同一规则再做一次仅创建验证。

## 完整任务已通过，本阶段结束

用户批准定向清理后完成一次原29步：工具累计11.466秒、路径重放13.666秒、任务控制程序41.940秒；组内完整流程69.484秒，组外备份准备105.007秒单列。原最终32组／66项及独立检查通过，官方评测保持历史1组／2项失败签名。

host／guest各681／403个0.1秒点，直接采样CPU约单核0.303%／0.614%，五个阻塞管理调用0.441秒；本轮没有关闭采样对照，不输出减速率。统计组current峰8418.293 MiB，创建前基线7589.691 MiB，增量828.602 MiB；guest内存／Cached峰550.270／246.996 MiB。原最终输入mincore、实际写文件边界和全部自有清理通过，没有新失败卷或活动服务。

三张图、数据表及完整说明在[results/prettier-local-no-proxy-20261031](../../results/prettier-local-no-proxy-20261031/REPORT.md)，[脱敏摘要](evidence/prettier-local-no-proxy-full-20261031.json)保存结果位置和成本范围；图片已实际查看。本阶段完成，不追加Prettier轮次。若继续扩展，建议仅离线核对Sphinx固定本地输入和原命令，明确最小无代理接入，之后再安排实跑。
