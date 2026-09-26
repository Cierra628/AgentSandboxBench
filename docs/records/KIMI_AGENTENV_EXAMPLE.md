# Kimi K3 所链接的 AgentENV 官方示例

2026-09-23（北京时间），官方 Quick Start 的 Ubuntu 22.04 模板及官方文档中的 Python SDK 示例已跑通。不是新增自定义 smoke，也没有执行 K3 模型推理或训练任务。

## 来源及完成标准

[Kimi K3 技术报告 §5.3.2](https://arxiv.org/html/2607.24653v1#S5.SS3.SSS2) 的 AgentENV 脚注明确链接到 [kvcache-ai/AgentENV](https://github.com/kvcache-ai/AgentENV)。该节介绍 microVM 沙箱、暂停/恢复、fork 和 snapshot；本任务沿用它作为主要 baseline。

本地已有该官方仓库，提交 `2f48c9c78ec58e4d73c49aa07cb81d984a4a4184`，无需再次克隆。示例来源是该提交的 `README.md` Quick Start 和 `docs/src/integration/e2b.md` Python SDK Usage。上游源码保持未修改。

本轮先执行官方的 `aenv pull ubuntu:22.04 --name ...`，再运行 SDK 示例的创建、列举、命令执行、暂停、删除五步。模板名替换为本轮唯一名称；文档 Python 代码由运行器提取、按原始 AST 语句顺序执行，在语句之间加验收检查。没有将其描述为未经包装直接运行的独立上游脚本。

## 实际结果

| 项目 | 结果 |
| --- | --- |
| 官方 Ubuntu 22.04 模板构建 | PASS |
| `Sandbox.create` | PASS，返回新 sandbox ID |
| `Sandbox.list` | PASS，SDK 输出包含本轮 ID |
| `sandbox.commands.run` | PASS，退出码 0，stdout 精确为 `hello world\n` |
| `sandbox.beta_pause` | PASS，列表确认状态为 paused |
| `sandbox.kill` | PASS，本轮 ID 从列表消失 |
| 本轮模板清理 | PASS，最终 template 与 sandbox 列表均为空 |
| 示例执行后的服务健康 | PASS，HTTP 204 |
| 虚拟环境依赖检查 | PASS，`pip check` 无冲突 |

主证据目录：`.artifacts/official-python-example-20260922T160519Z-1454809`。`checks.json` 和 `result.txt` 是逐项验收；`official-example.py` 是实际执行的文档片段；`versions.json` 记录源码提交和文档 SHA-256；`runner-sha256.txt` 记录运行器及依赖锁定文件版本；`example.log` 为本地输出。结果目录使用 UTC 时间命名。

## SDK 版本边界

首次按照文档未指定版本的 `pip install e2b` 得到 `e2b 2.51.0`。Ubuntu 模板已成功，但 SDK `Sandbox.create` 返回 405。失败证据为 `.artifacts/official-python-example-20260922T155625Z-1451533`，本轮模板已清理。

已检查 SDK 实现：2.51.0 发送 `POST /v2/sandboxes`，而固定预构建镜像对该方法返回 405。无 templateID 的空请求对 `POST /sandboxes` 返回 422 参数校验错误，对 `POST /v2/sandboxes` 返回 405；这些探测没有创建沙箱。证据为 `.artifacts/sdk-api-compatibility/empty-create-probes.json`。

随后核查并固定 `e2b 2.26.0`，它使用 `POST /sandboxes`，完整官方示例通过。当前本地 AgentENV 源码的 OpenAPI 已有 v2 创建接口，但运行中的固定镜像不支持它，再次说明不能把本地源码和预构建镜像当成同一版本。没有为这次示例更新服务镜像或修改服务器。

此结果只确认该镜像与 SDK 2.26.0 的上述示例兼容，不代表该 SDK 的所有接口均兼容。TypeScript SDK、完整上游 E2E、K3 模型推理、benchmark 和训练流程未在本轮验证。

## 运行环境及复现

使用之前保留的服务 `http://127.0.0.1:18000`，服务结果目录为 `.artifacts/server-smoke-20260922T114006Z-1387374`。服务镜像 digest 为 `sha256:c5cd67759d365669b558d3754a18bdf52fa8b7614fe23bcb34250e723cf2ee03`，image ID 为 `sha256:db9eb044ae3d85f9b276b9d2e5dea28cb0be95263d217ea4989f095e1d828681`。

Python 3.12.3，SDK 安装在 `runtime/e2b-venv`，完整依赖锁定于 `scripts/e2b-requirements.txt`。凭据从私密的 `runtime/config/aenv/credentials` 读取，在子进程中设置 E2B 环境变量，不写入共享脚本或报告。原始 SDK 输出可能含临时访问令牌，只保留本地。

已有虚拟环境时，复现命令为：

```bash
cd /home/yyxie/agentenv_experiment
bash scripts/10-run-official-python-example.sh .artifacts/server-smoke-20260922T114006Z-1387374
```

重建依赖时，在项目目录执行以下命令；无需系统 pip 安装：

```bash
python3 -m venv runtime/e2b-venv
runtime/e2b-venv/bin/python -m pip install -r scripts/e2b-requirements.txt
```

运行器依赖当前用户可读服务凭据并可执行只读 `docker inspect`。本次进程为已授权的 root。每个 CLI 步骤有时限，模板构建上限 360 秒，Python 示例上限 120 秒；失败后仅清理本轮唯一模板及已取得 ID 的沙箱。若创建请求超时而尚未返回 ID，需根据该结果目录的前后列表核查残留，不能仅凭脚本清理状态断言无残留。

既有底层功能和来宾进程连续性证据仍见 [cpu-15 报告](CPU15_RESULTS.md)，本轮未重复它们，也未重启服务、修改内核或操作其他工作负载。
