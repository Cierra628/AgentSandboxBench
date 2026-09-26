# TrEnv-X 工具任务 cgroup 与文件状态恢复（2026-09-26）

本轮以 AgentSandboxBench `4acf96e` 为基线，在独立 checkout、`codex/trenv-task-cgroup` 分支工作。固定 IncrementalDAX `a9bc7deadb68a6e24b7242eab343dfedf953ea38`、TrEnv-X fork `53041d8c924514fca27e363cce7f5af8079267ce`；未升级源码、修改共享服务、清缓存或运行性能实验。使用本轮新产生的隔离证据，没有读取历史实验机 `.artifacts/` 数据。

交付是任务生命周期接口、固定 fork 的实际启动路径补丁，以及 IncrementalDAX controller 的捕获/清理接入补丁。契约和隔离 kernel/启动代码 smoke 通过；**完整 TrEnv-X controller 服务、DAX 文件层恢复和真实 trajectory 尚未验收**。

## 检查到的路径及改动

- TrEnv-X `packages/envd/internal/process/simple.go`：HTTP `/process/create` 创建 bash，`/process/wait` 返回输出/退出码后删除 shell 记录。原实现没有后台后代归属；任务组现在独立保留，create 响应新增可选 `task_id`。
- `internal/process/service.go`：process RPC 的 `Service.Start` 最终调用 `cmd.Start`。补丁只替换启动入口；保留原命令、登录 shell、cwd、环境、凭据和退出通知。
- `internal/terminal/terminal.go`：PTY 的 `pty.StartWithSize` 在相同启动保护内执行，仍保留 PTY/session/凭据语义。
- `motivation/experiments/exp3-RL-fork/controller.py`：原 `capture_template → promote_snapshot` 会直接 cp 在线 writable 镜像，归档 `/tmp` 前执行全 guest cache drop，缺少任务冻结/CH pause。补丁改为已确认冻结、固定 `/tmp` 归档与 sync、CH pause/确认、独立复制、resume/确认、解冻；只在捕获成功后调用既有 `archive_upper/build_layer` 和 clean template 构建。删掉该 checkpoint 路径中的全局 cache drop 并更新其 metadata。
- `Controller.delete` 和最终清理循环先调用本 guest manager 的任务 inventory/cleanup，逐项记录失败，再删除本次 sandbox；不扫描 host PID 或其他 cgroup。

冻结动作与 guest runner/manifests 没有修改。退出码非零仍交给原 trajectory 的预期失败规则处理，不能统一转为后端错误。隔离测试实际核对 stdout、stderr 和 `127`；未运行 Exp1 的 26 步或官方完整任务评测。

## 最小接口及所有权

可审查实现位于 `backend/trenvx/taskcgroup/` 和 `backend/trenvx/quiesced_checkpoint.py`；安装到固定源码的内容同时保存在两个 `patches/*task*.patch`。应用脚本校验提交、补丁状态和实现字节一致性。

| 接口 | 语义 |
| --- | --- |
| `New(delegated_parent)` | 在现有 cgroup v2 parent 下创建随机、0700 的 manager 根组；不接管旧组。envd/control plane 留在任务组外。 |
| `Launch(cmd, start)` / `Start(cmd)` | 每次后端工具启动创建一个随机任务组，用 `SysProcAttr.UseCgroupFD/CgroupFD` 在 clone 时加入，再执行 shell。创建/加入是同一个原子启动操作，不提供事后搬迁任意 PID 的入口。 |
| `GET /tasks` | 返回本 manager 保留的 `{id,pid,events}`；pid 只是启动记录，清理不用它发信号。shell 退出后仍保留组及后台后代。 |
| `POST /tasks {op:"freeze",token,timeout_ms}` | 关闭新工具准入，对所有本 manager 的组写 freeze=1，逐组确认 events 的 frozen=1。token 为调用方生成的 32 位 hex。错误/取消/超时立即用独立恢复预算尝试解冻全部组。 |
| `POST ... {op:"thaw",token}` | 写 freeze=0 并确认 frozen=0；确认成功才重新开放启动。解冻失败保留 token/关闭准入；同 token 不能把不完整恢复误报为已冻结。 |
| `POST ... {op:"prepare",token}` | 仅在确认冻结且 token 匹配时，组外执行固定的 `/tmp` tar（保留原 mixfs 排除规则）和 guest sync，返回本 token 的 `/dev/shm/asb-runtime-*.tar`。不是任意命令的逃逸入口。 |
| `POST ... {op:"discard-runtime",token}` | 删除本 token 的 runtime 临时文件；捕获失败也调用。 |
| `POST ... {op:"cleanup",id}` | 只接受 registry 中的任务 ID，验证保留 FD 对应的目录 identity，解冻、cgroup.kill、等待 populated=0、rmdir。拒绝任意路径、外部 ID、仍处于 checkpoint 的清理；没有 remembered-PID kill fallback。 |

任务 ID 在 `/process/wait` 后仍可从 inventory 找到。没有自动以“shell 已退出”删除任务组。集成方可以在确认不再需要后台任务时显式 cleanup；manager 根组和 guest cgroup mount 随本 sandbox 销毁。任务自行创建的嵌套组不会被递归扫描删除，导致 rmdir 失败时会明确报告。

`ASB_TASK_CGROUP_PARENT` 未设置时保留 fork 原启动行为，但 `/tasks` 返回 503，新的 checkpoint 路径拒绝继续，避免静默退回不一致的在线 cp。设置了变量而 parent 不可写/不是 cgroup v2 时 envd 启动失败。要求 guest 支持 clone3 的 CLONE_INTO_CGROUP、cgroup freezer 和 cgroup.kill；不提供弱化的 PID/进程组回退。固定 6.1.134 guest 已通过本轮验证。

HTTP endpoint 采用原 envd 的私有 guest 控制面访问边界；token 区分并发 checkpoint 所有者，不是用户认证。拥有 cgroup 管理权限的 root 工具主动迁组、外部 daemon 代写以及并发 filesystem RPC 写入不在本次归属保证内。

## checkpoint 失败与恢复语义

`capture_upper` 在 freeze 请求前生成 token，所有异常都走 finally：尝试 CH resume（即使 pause/info 回复丢失）、确认 Running、删除 runtime 文件、解冻任务。原始错误与恢复错误分别进入 audit，同时抛出组合错误；resume/解冻无法确认时不生成成功 checkpoint。恢复调用使用独立预算，不依赖已取消的操作 context。

`capture_joined` 对整个同步捕获 worker 使用 shield，并在异步取消后等待其退出；不会让仍在执行的 cp 与 parent 删除/临时镜像删除并发。cp 超时会 kill/wait 其子进程。每个控制调用和复制有界，freeze 最多 30 秒、解冻确认 2 秒；这不是完整 controller 的总 deadline。内核不可中断的 sync/I/O、guest 不可达或 CH 持续故障不能由客户端保证恢复，记录中会保留解冻/resume 错误，需集成方处理本次 sandbox。

**TrEnv-X 的恢复继承文件状态**：封存 upper 作为 DAX lower，配全新 writable upper 和 clean-template VM，按原路径恢复选定的 `/tmp` 文件。它没有恢复任意父 shell、后台 worker、打开 FD、TCP 会话或任意父 VM 的完整进程内存。父 VM 的 resume 只是暂停继续；新 guest 不会复活任务 registry 中的父进程。当前测试没有新增文件层恢复证据。

## 复现与验证命令

从仓库根目录运行，源码 checkout 仍独立且被 `.gitignore` 排除：

```bash
# 现有固定源码获取入口；不会启动服务。
bash scripts/14-fetch-research-sources.sh
python3 scripts/40-apply-trenvx-task-cgroup.py
python3 scripts/40-apply-trenvx-task-cgroup.py --check

# 无共享服务、纯标准库的契约。
GOCACHE=/tmp/asb-go-cache go -C backend/trenvx/taskcgroup test -race -v -count=1 ./...
python3 -m unittest discover -s tests -v

# 固定 fork 的实际包与 envd 编译；使用 go.sum 中原依赖，不升级。
GOCACHE=/tmp/asb-go-cache GOPATH=/tmp/asb-go-path \
  go -C IncrementalDAX_moti/baselines/TrEnv-X/packages/envd test \
  ./internal/process ./internal/terminal ./internal/taskcgroup
GOCACHE=/tmp/asb-go-cache GOPATH=/tmp/asb-go-path \
  go -C IncrementalDAX_moti/baselines/TrEnv-X/packages/envd build -o /tmp/asb-envd .

# 无网络 QEMU TCG、256 MiB、2 vCPU、一次短 smoke；不需要 sudo/KVM。
# --kernel 必须由运行者提供兼容内核；本轮使用已有的固定 guest kernel。
python3 scripts/39-smoke-trenvx-task-cgroup.py \
  --kernel /var/lib/trenvx/kernels/ch-6.1.134/vmlinux --launchers
```

普通 go test 会明确 SKIP 两个需要真实 cgroup 的测试，不能将这个 SKIP 算成内核 PASS。`--launchers` 会在新 initramfs 中加入实际 envd process 测试二进制、bash 与运行库，运行真实 simple HTTP handlers、RPC `Service.Start` 和 PTY；没有启动完整 envd/main、orchestrator、template-manager 或 CH 服务。smoke 只操作新 guest 里的 cgroup，并随 guest 关机退出；QEMU 超时由 subprocess.run kill/wait。每次输出到独立 `.artifacts/trenvx-task-cgroup/<run_id>/`，不覆盖失败记录。

## 实际验证及边界

随仓库保存的脱敏最小结果为 `docs/records/evidence/trenvx-task-cgroup-20260926.json`。原始 serial、initrd 和二进制留在本 worktree 本轮 `.artifacts/`；复现者无需访问它们。固定 kernel SHA-256：`a50bbc107954f603fce3c8eb8105c6c65290dbe2224402061acc022c95968730`。

| 检查 | 本轮结果 | 范围 |
| --- | --- | --- |
| Go 契约（含 race） | 8 个 PASS；内核用例在 host SKIP | 超时/写入失败解冻、解冻失败关闭准入、token、所有权/identity、启动失败清理。模拟 cgroup I/O。 |
| Python 契约 | 4 个 PASS | 捕获顺序、各阶段异常、lost pause 回复、取消后 join、清理只使用 inventory 且继续处理其他 owned task。模拟 guest/CH。 |
| 固定 fork 包测试及 envd build | PASS | 实际 process/terminal 接入编译；真实 guest 用例在 host SKIP。 |
| QEMU 6.1.134 core | PASS | 父 shell 退出后 setsid 后代仍归属；frozen=1 后文件 200 ms 不增长；thaw 后增长；取消 freeze 后恢复；定向清理保留另一 manager 的进程。 |
| QEMU 实际启动代码/HTTP handlers | PASS | simple HTTP 的 stdout/stderr/127 与 task_id；simple、RPC 和 PTY 后代归属；真实 freeze/thaw endpoint。 |
| 补丁复现 | PASS | 固定提交的干净源码快照 apply/check、已应用幂等 check、实现字节一致。 |
| 完整 controller + runtime archive + CH pause/copy + DAX 新 guest 恢复 | 未验证 | 需要隔离服务、patched envd 模板、guest GNU tar、正确 socket/data_root。 |
| Exp1/更多 trajectory、打开 FD、ACL 专项、恶意迁组、并发压力/性能 | 未验证 | 本轮没有运行，不能引用历史 fixture 的 PASS 替代。 |

首次实际启动代码 smoke `20260926T060601Z-1907330`：core 和 simple HTTP 127 通过，PTY 因 initramfs 未挂载 devpts、`/dev/ptmx` 不存在失败。补齐测试 guest 的 devpts 后通过；未据此改动上游 PTY/session 逻辑。

## 集成方配合

1. 将补丁编译出的 envd 装入**新的隔离模板**，在 envd 开始接受工具前设置 `ASB_TASK_CGROUP_PARENT` 为本 guest 可写、仅控制面可管理的 v2 parent。可以通过隔离 envd systemd unit 的 `Delegate=yes` 和环境变量配置，parent 路径以该 guest 实际层级为准；本轮没有改动任何已有模板或 systemd service。
2. 为本 sandbox 串行安排 checkpoint、工具准入和 filesystem RPC 写入。确认所有能修改 upper 的应用写入者经过三条受控启动路径；外部基础服务的写入需另行纳入一致性协议。
3. 对齐 `DATA_ROOT`、envd 私有地址和 CH `vmm-<sandbox_id>.socket` 所在目录。若 orchestrator 使用不同 TMPDIR，设置 controller 的 `ASB_CH_SOCKET_DIR`；不要猜测扫描其他实例的 socket。留存 `checkpoint-*-quiesce.json`、`cleanup-*-tasks.json` 和原动作输出/退出码。
4. 在隔离完整服务中验证 `/tmp` 原排除规则、guest sync、CH pause/copy/resume、upper sealing 与新 guest 恢复，再跑真实冻结 trajectory。为恢复失败定义 sandbox 的销毁/重试策略；保持 file-state-only 声明，不声称父进程恢复。
