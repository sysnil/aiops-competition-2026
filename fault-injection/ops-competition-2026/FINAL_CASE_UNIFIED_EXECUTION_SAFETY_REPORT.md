# SF-013～SF-017 统一执行安全入口审查（2026-10-19）

**最终状态：`PRODUCTION_BLOCKED`。** 本轮真实故障注入 0 次、正式 Attempt 0 个、生产主机重启 0 次。独立候选版本的授权核心与 Worker 无害 Marker 已验证；尚未把所有正式 Controller/Adapter 和 Master HTTP 负载接入该入口，不能标记为五题生产就绪。此前 SF-003～SF-012 数据、SF-013 ATTEMPT-01/02 及旧 v4 源码未修改。

候选版本：`/root/ops-competition-2026/final-case-unified-execution-v1-20261019/`。源码、DRAFT 合同及无害证据完整 SHA 清单：`results/source-evidence-manifest.json`，SHA-256 `951c2c4f8f51b3befe279a55d18369cdac7155e36f16fb7f0324092c67c38bd1`。最终无害联测 JSON：`results/live-marker-integration.json`，SHA-256 `f80a8ab947af18e418df00d50d979773a9574be647825d641a1e907bf2d7324d`。

## 1. 签发与撤销竞态

原报告 `grant-race-analysis.json` 证明：旧签发器在检查 systemd Controller/Guard 时，撤销可异步写入，随后仍可能签出 grant。本轮建立 `action_decision.py`，使同一 Attempt/Action 的 `permit`、`revoke` 和 Master 签发共享 `decision.lock`。撤销在等待锁前先以 `O_EXCL` 写入绑定身份的 `REVOCATION_REQUESTED.json`；签发器在读取状态时、查询 Controller/Guard 后、签名前再次检查撤销意向、终态、Manifest 文件 SHA、许可期限、Master boot ID 和 Controller/Guard 活性。一次性 `consumed.json` 在锁内落盘，原 Ed25519 签名、随机 nonce、1.2 秒有效期及 Worker 防重放仍保留。锁获取或状态读取失败会拒绝，不存在异常后放行分支；重复撤销仅对相同 run/action/manifest 幂等。

Master 的两条受限 SSH 公钥仍分别绑定 Worker2 `192.168.53.54` 与 Worker3 `192.168.53.55`，强制命令现指向新候选签发器。原 `authorized_keys` 备份于 `/root/.ssh/authorized_keys.pre-unified-20261019`，原 SHA `cfe4dd626c0196cb142383c6bc7be03d0eed53e84cf3a780c5340b4770928b5f`；当前 SHA `202dac9363c6770830f876cf53b939696e0edd5ef142b9d428d404b0cc881277`。未复制或输出私钥。Worker2/Worker3 使用同一份新 `auth_protocol.py` SHA `2d2ac188d0142791e386a687afc4b02e1f0362f65f1e6443377baf7f7d21bb07` 和 `worker_auth.py` SHA `37d0a5021f672135aee05eab3192aacd36094b2ef26f5f548d3991f53d7d7d71`；各自仍使用独立现有 SSH 身份与 Master 签名公钥。

**剩余窗口：** 若 grant 已签发，Master 随即撤销或失联，但 Worker 尚未收到本地撤销，签名在签发后最多 1.2 秒仍可通过密码学验证；Worker 效果前的本地撤销与过期检查可缩小风险，无法消除分布式撤销到达的延迟。合成测试中 90 ms 后仍有效、1.21 s 后拒绝。这是可观察的上界，不是“绝对零竞态”。可选后续方案是 Worker 效果前再做一次独立短确认/提交握手，并对授权代际及远端撤销做全链路回归；不得延长现有 grant 或复活旧 4 秒租约。若已开始真实效果，仍须由独立 Watchdog 恢复，不能用本轮 Marker 代替该资格。

## 2. 新五题入口映射

`design/action-entry-map-draft.json` 把 Case、Action、主机、机制、时长和入口固定为 DRAFT；每个 Action 必须带独立 Attempt ID、Action ID 和 Manifest SHA。`entry_policy.py` 要求入口精确为本候选版本的 `worker_entry.py`、`http_gate.py` 或无害 `marker_cli.py`，并拒绝 SF-007/SF-011/SF-012 历史本地租约路径。

| Case | Action 与位置 | 本轮证据 | 正式故障资格 |
|---|---|---|---|
| SF-013 | Worker2，Frontend PodMemoryStress 32 MiB/300 s | 新入口 Marker 授权及计时触发 PASS | **未通过**：变更后真实适配器、Watchdog、身份及恢复仍需单独验收；PodCPUStress 不在本次资格内。 |
| SF-014 | Worker3，Recommendation ServicePause/30 s | 新入口 Marker PASS | **未通过**：两副本真实暂停/撤销适配器尚未绑定新 Controller/Manifest 并完成资格。 |
| SF-015 | Master 本地 HTTP ReqDelay/60 s | `http_gate.py` 单测 PASS；每个 GET `/` 请求实时检查 Controller/Guard、进程起始 ticks、许可及撤销，Image/Cart 不加故障头 | **未通过**：现有正式状态机负载与 Controller 尚未改为调用新请求工厂；旧 4 秒 HTTP lease 入口不能用于新题。 |
| SF-016 | Worker2，Frontend PodMemoryStress 32 MiB/300 s | 独立 Action ID/Manifest 的 Marker PASS | **未通过**：SF-016 专属正式 Timer/Controller/适配器未完成资格。 |
| SF-017 | Master HTTP + Worker3 ServicePause 两个独立 Action | Worker3 子动作在触发前撤销：无 Marker、无 grant 消费；双动作取消函数单测 PASS | **未通过**：Master HTTP 与 Worker3 的正式联合状态机、失败传播及两个真实适配器尚未联测。 |

新代码**没有修改**旧历史脚本。旧入口仍在磁盘上，root 可直接调用；目前的精确路径拒绝只保护采用新 DRAFT 入口校验的调用方。正式执行器尚未整体切换，因此“旧入口无法旁路”不能宣告为已完成的系统级门禁。尤其 SF-015/SF-017 的 HTTP 负载仍需把 `controlled_request_factory` 接进实际普通业务请求路径，并验证请求进程身份及控制器失败时的即时停止。SF-017 的 `cancel_other_actions` 只允许取消同一 Attempt 的兄弟 Action；正式控制器尚须接入此失败传播。

## 3. 无害验证

Master Python compile PASS；`test_unified_safety.py` **13/13 PASS**。覆盖检查期撤销、签发前撤销、签发后撤销与 grant 过期、一次性消费/防重放、Manifest 改动、Controller/Guard 退出、跨 Attempt 隔离、Worker 本地撤销、HTTP 正常对照不加头、HTTP 许可过期、旧入口拒绝和 SF-017 兄弟 Action 取消。网络断联与 Controller 崩溃在单测中仅以拒绝/不活跃状态模拟，不能替代真实系统生命周期测试。

使用最终共享源码和真实 systemd Timer、Master 受限 SSH 强制命令、Worker 本地授权客户端完成四项 **Marker-only** 联测：

| 场景 | 结果 | Master 一次性授权 |
|---|---|---|
| SF-013 / Worker2 | 无害 Marker fired | consumed |
| SF-016 / Worker2 | 无害 Marker fired | consumed |
| SF-014 / Worker3 | 无害 Marker fired | consumed |
| SF-017 / Worker3，触发前撤销 | **未** fired | **未** consumed |

原始 Marker `fired.json`、Master `consumed.json`/`REVOKED.json` 分别保留在本候选版本的 `marker-runtime/` 和 `runtime/` 下；测试脚本停止了对应 systemd Timer/Service。SF-015 Master HTTP 为单元级无故障请求构造验证，没有向真实 Frontend 发送故障请求头；SF-017 Master HTTP + Worker Timer 的完整联合联测未完成。以上均不能记为真实故障资格。

## 4. 重启、空间与环境门禁

未在 Master 或本机找到可复现宿主机 PID 1、SSH、Timer、Guard、cgroup 和网络恢复的独立 VM/systemd 测试环境（QEMU/virsh/multipass/systemd-nspawn 未配置）。**真实主机重启生命周期门禁未通过**。不以 Guard 重启、boot ID 模拟或容器重启替代。现有维护窗口方案为 `final-case-production-approval-20261019/design/restart-maintenance-window-plan.md`；需组委会批准隔离 VM 资源或生产维护重启方案后执行，重点验证已撤销状态在重启后仍不可触发、待触发 Timer 不复活、授权不可达时拒绝以及故障 Watchdog 恢复。

08:17 UTC 左右只读空间快照（十进制 GB）：Master 根盘 **45.22**、`/data/stage5-v3` **51.31**；Worker1 根盘 **36.68**、`/data` **18.58**；Worker2 根盘 **53.74**、`/data` **17.54**；Worker3 根盘 **35.28**、`/data` **11.08**；VM1 根盘 **87.39**。当前均高于 5,000,000,000 B 底线，但五题累计不合格。用历史单题 7,350,324,113 B 和每题 Worker 数据盘 2 GB 后台占用的敏感性假设，较新 Worker 系统盘归档 DRAFT 下，Worker3 `/data` 在 SF-015 约剩 **5.08 GB**、SF-016 **3.08 GB**、SF-017 **1.08 GB**；最后至少缺 **3.92 GB**，且未含额外突发余量。较早 DRAFT 则还对 Worker1 `/data` 构成越线风险。两份归档分配仍冲突，未审核、未据此建正式归档；需要确定唯一分配、重新实测峰值、补足余量或经授权迁移历史归档。不得删除历史数据或降低 5 GB 底线。

本轮结束时：4/4 Kubernetes Node Ready；Frontend 两副本分别在 Worker1/2，Recommendation 两副本在 Worker3；OpenSearch `green`、Prometheus healthy；Collector Pod Ready，最近 10 分钟 Prometheus 中 Metrics/Logs/Spans receiver-refused 增量均为 0。五机无活动竞赛 systemd Service/Timer 或 `netem`/`tbf`。这属于当前只读健康快照，不等于正式生产前的完整 Global Safety Gate；Calico BGP、精确目标 UID 与全链路存储预算仍需在生产前重新核验。

## 5. 恢复生产前仍须完成

1. 把 SF-013～SF-017 的正式 Controller、Worker Timer/Guard、真实适配器及状态反馈绑定到唯一新入口，关闭新题调用旧本地租约的所有路径；完成逐题无害联合和真实适配器安全资格，但本轮不注入。
2. 将 SF-015/SF-017 的实际状态机业务负载接入 Master 本地逐请求门禁；对双 Action 中任一失败完成同 Attempt 后续动作取消及审计联测。
3. 完成可信主机重启生命周期测试，并确定唯一可满足 5 GB 余量的累计归档方案及恢复读回。
4. 刷新五机 Global Safety、Calico、目标 UID/进程、Collector、Watchdog 和容量检查，再由组委会单独批准生产。

因此仅授权核心和无害 Worker Marker 可记为 **`CODE_AND_MARKER_VALIDATED`（局部）**；五题总体保持 **`PRODUCTION_BLOCKED`**。不得据此启动 SF-013 ATTEMPT-03 或 SF-014～SF-017 正式生产。
