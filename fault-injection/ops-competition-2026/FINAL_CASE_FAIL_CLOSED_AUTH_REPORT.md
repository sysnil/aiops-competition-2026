# SF-013～SF-017 Worker 实时授权修复与安全验证

**最终状态：`PRODUCTION_BLOCKED`。** 本轮完成 SF-013/Worker2 的新版本 fail-closed 授权实现与无害验证；真实业务故障 0 次、正式 Case/Attempt 新建 0 个、生产主机重启 0 次。没有启动 SF-013 ATTEMPT-03 或 SF-014～SF-017。代码位于 `/root/ops-competition-2026/final-case-fail-closed-v1-20261019/`、`sf013-adapter-v4-20261019/`、`sf013-formal-v4-20261019/`，全部为**未冻结的 DRAFT**。历史 v3 及原验收报告仍保持原结论。

## 授权流程修复

旧路径：Master 提前把 4 秒本地租约和授权文件写到 Worker；Timer 触发时 `fault_worker.fire` 只校验这些本地文件。前次报告中的断联测试证明 Master 无法撤销时无害 Marker 仍会触发。

新路径：Timer 到时由 Worker2 的唯一受控 `fire` 入口用密码学随机 256-bit nonce **新建 SSH 连接**请求 Master。Master 的 SSH key 仅允许来自 `192.168.53.54` 的受限强制命令，不提供 shell、PTY 或端口转发；Worker 固定 Master 的 Ed25519 主机公钥，不回退到密码。Master 核对 Attempt/Action/Manifest SHA、Worker 身份及双方 boot ID、计划时间、预检许可、Controller `MainPID`、Guard active、未撤销和未消费状态；在签发前持久写入该 Action 的一次性消费回执。响应包含原请求 SHA、nonce、身份、Master boot ID、签发/失效时间，并由**仅存于 Master**的 Ed25519 私钥签名。Worker 验签、验身份及请求 SHA、验 1.2 秒有效期，原子写入本地消费记录后立即执行回调。旧本地租约仅作为运行安全心跳，**不再授予故障启动权限**。

Master 不可达、SSH 超时/断线、拒绝、状态不匹配、撤销、过期、签名错误或响应重放，均不能调用效果回调。v4 正式适配器直接 `run` 命令已禁用；只有被 v4 Timer Manifest 哈希绑定的 `fault_worker.fire` 可传入回调，且在真正 `SIGSTOP` 或激活内存 holder 前再次检查授权时效、本地撤销和预检安全。已开始的效果保留独立 Watchdog 清理。Master Guard 在尝试 Worker 撤销之前先持久写 Master 端撤销记录，不会扫描或停止其他 Attempt 的 Timer。

认证仍使用现有 SSH/22，**没有开放新端口**。Master 原 `authorized_keys`（空文件）已备份到受限目录；新增的只有一条 `from=192.168.53.54,restrict,command=...` 公钥规则。尝试用该密钥执行 `id` 被强制命令拒绝。Master 签名私钥及 Worker SSH 私钥均未进入 Git、Public bundle、运行 Manifest 或日志；只记录公钥及文件 SHA。密钥目录 0700、私钥 0600，受限 SSH 配置精确回滚 dry-check PASS，未执行回滚。Worker 公钥指纹：`SHA256:09F+cqLex0GxTcu8mOq01byB0Fx0SGc9jxeXT1iI6G8`；Master 签名公钥 SHA-256：`e2604761e20374a7feaba1030bd60cba27f832fb6f1673404f4f9c7f54b05e25`。

## 无害安全测试

Master/Worker2 的真实 systemd Timer、Master Controller/Guard、受限 SSH 和签名应答完成 **19/19 场景 PASS**，均为无害 Marker，无业务故障。每个拒绝场景都在原计划时间后确认 Marker 不存在、该 Attempt Timer/Service inactive，并保存精确撤销回执。汇总：`/root/ops-competition-2026/final-case-fail-closed-v1-20261019/results/marker-matrix-assessment-v2.json`，SHA `c65a26766b223ec007bd5cc41bc008b5c103039ef6782afa6cec82008c74ae5a`。

| 测试 | 实际结果 |
|---|---|
| 正常授权 | Worker 获签名响应并触发 Marker，Master/Worker 响应 SHA 一致，之后精确撤销。 |
| Master 拒绝、授权过期、已撤销 | Timer 均实际进入 Worker 入口，Master 拒绝，Marker 0，最终无待触发 Timer。 |
| Master→Worker 控制 SSH 中断 + Controller SIGKILL | Guard 无法送达撤销，但 Worker Timer 仍进入授权入口；Master 发现 Controller 已退出而拒绝，Marker 0。连接恢复后精确清理；Guard 失败有独立记录。 |
| Worker→Master 授权通道隔离 | Worker Timer 在独立网络命名空间中触发，实时 SSH 不可达，Marker 0。 |
| 授权检查途中断线 | Master 正在处理该次请求时杀掉 Worker 的 SSH 客户端，Worker 拒绝执行，Marker 0，未使用缓存许可。 |
| Controller 退出、Guard 明确停止 | 对应 Guard 撤销或 Master 拒绝，均无 Marker。 |
| Guard 崩溃与自动重启 | systemd 重启次数 1；Guard 恢复后正常授权 Marker。另一次“重启后 Controller 退出”场景中，新 Guard 在触发前撤销，Marker 0。 |
| Manifest/Action 身份不匹配、Master/Worker boot ID 变化 | 全部拒绝，Marker 0。boot ID 变化为隔离模拟，**不是实际主机重启测试**。 |
| 响应重放/一次性消费 | 签名篡改、不同 nonce 和已消费 Action 在测试中拒绝；Timer 预消费场景 Marker 0。 |
| Worker 回调异常 | Master 曾签发，但 Worker 效果回调异常；Marker 0，不能重放同一 Action。 |
| 两个 Attempt 同时 pending | A 被撤销后未触发，B 同时保持 active 并正常授权触发，互不误撤销。 |

首次 `guard_exit` 测试用 SIGKILL，systemd 自动重启 Guard，触发时它已经 active，故 Marker 正常触发；原失败记录 **`guard01.json` 保留**。随后用新测试 ID 明确停止 Guard 服务，验证触发时 Guard 不在运行则拒绝；没有覆盖或重写第一次结果。

Master 单元测试 **11/11 PASS**：覆盖签名/重放、过期、撤销、单位退出、boot ID、身份不匹配、网络超时不能到达正式适配器及直接适配器执行拒绝。v4 正式/Marker 入口 AST 接线审计 **9/9 PASS**；Master/Worker2 的 8 份共享与正式入口文件 SHA、签名公钥及受限 SSH 配置全部一致。v4 适配器另以两个牺牲 `sleep` 进程完成独立 systemd Watchdog 自测：被暂停进程恢复、受控子进程终止、恢复 Timer inactive，业务故障 0。

## 1800 秒无故障联合演练

新计划 SHA：`3bf5e43f6f3994480d4cbd5a299ae90d3cd2c5abec994a3dcd465a69008187db`，明确 `faults_allowed=false`。使用新 v7 的 `start_formal_nofault → capture → observer → v4 marker_controller → v4 marker_guard → cleanup`，主业务窗口从 `2026-10-19T06:06:25.277558Z` 起持续 **1800 秒**；采集查询尾部另保留 30 秒，随后完成六段不可变 +60/+120/+240 秒 Trace 补齐与审计。第 10 分钟的无害 Marker 于计划后约 **0.85 秒**触发；Master 签发到 Worker 写入 Marker约 **90 毫秒**。Controller、Guard 与 Worker Timer 最终状态一致，无残留。

结果为 `NOFAULT_1800_CHAIN_PASS` 与 `NO_FAULT_VALIDATION_PASS`，**0 blockers**。业务步骤 **7,950/7,950** 成功；Image、Cart、Home、Recommendations 各 **1,800/1,800** 成功；应匹配的请求 ID 与最终存储 Proxy Server Span 全部关联。六段 Metrics/Logs/Traces **30/30 分钟**有数据，共 **357,002 Span、重复 0**；Collector 各模态 receiver refused 增量 0，working set 峰值 **347,418,624 B**，Logs/Traces 最大新鲜度约 **2.11/4.76 秒**，OpenSearch parent breaker 无新增跳闸。五主机动态磁盘监控 136 次采样，终态 PASS。联合终态 SHA：`f21196266d5090a5fdec8acea389efc9c7ed8ed3538802b4acaabe0970aefbaf`；数据评估 SHA：`03e6c7321d74b29f909e5b6a40a0b47a4d6284c4c36b11e726c8f34019a87023`。

限制：这是**Marker-only**正式控制链等价演练，不是 `SF-013-ATTEMPT-03` 真实入口的故障实验；新 v4 机制适配器虽完成静态门禁与牺牲进程 Watchdog 自测，其变更后的真实业务故障资格尚不能用旧 v3 的资格证据直接替代，本轮未进行该项注入。

## 版本、环境与历史保护

代码/证据 Manifest：`/root/ops-competition-2026/final-case-fail-closed-v1-20261019/results/code-and-evidence-manifest.json`，SHA **`35c1cf2e4668970194b7d9b88580560cefec4e47e33ae4350a7c8a464abe016f`**，记录 44 份代码和 14 份证据。核心代码 SHA：`auth_protocol.py` `e6c2b695...6190d`，`master_authorizer.py` `445175f4...84f81`，`worker_auth.py` `34ea95d4...16d8de`，v4 `fault_worker.py` `ff1033f5...3ef08c`，v4 `qualified_adapter.py` `71cfe0f2...7a133f`；完整值以 Manifest 为准。Manifest 状态明确为 `LIVE_AUTH_DRAFT_VERSIONED_NOT_PRODUCTION_PERMIT`。

最终五主机：四个 Kubernetes Node Ready，13 个关键 Pod Ready；Calico 四节点各 3/3 BGP peer Established；Prometheus healthy，OpenSearch green、unassigned shard 0，Collector 配置 SHA 仍为 `ad42e84b2a1d25b6b9b716f2a119f8d3ab6c50a975b860f3a8eed939618dd05f`，最终 grpc/http refused 均 0。五机无活动竞赛 Service/Timer、无 `netem`/`tbf` qdisc。最终可用空间（十进制 GB）：Master 根盘/数据盘 **45.24/51.31**，Worker1 **36.68/17.78**，Worker2 **53.88/17.58**，Worker3 **35.29/11.14**，VM1 根盘 **87.39**，均高于 5 GB 底线。

原 v3 安全 Manifest SHA `b19a7d27918d096602895db082a19a70d4ea43d2e44943cce8ab7b6b33b69b23`、十题生产总账 SHA `813035c9c5e69741c73de7d0afb92e427d8745714992ab74c0c41c414f7e69f8`、SF-013 ATTEMPT-01/02 失败记录 SHA 均未变化。历史十题 10/10 Public Manifest、60/60 Public 文件 SHA 再次通过；未读取历史 Private GT 或覆盖旧实验。

## 仍未通过的安全边界与审批

1. **主机重启生命周期未通过。** 当前环境没有已验证的隔离 systemd 重启沙箱，本轮未重启生产 Master/Worker。boot ID 变化、Guard 进程重启和待触发 Timer 的 Marker 测试不能代替实际宿主机重启。拟开展的维护窗口、Marker-only 计划、带外访问和回滚步骤见 `/root/ops-competition-2026/final-case-fail-closed-v1-20261019/design/restart-maintenance-test-plan.md`；任何生产主机重启须组委会另行批准。
2. **正式机制资格与范围仍待完成。** 当前实机凭据、受限强制命令及 v4 正式适配器仅接入 SF-013 的 Worker2 Pod 机制；SF-014～SF-017 若涉及 Worker1/3 或不同适配器，必须按同一协议另行绑定、部署和完成无害回归。v4 适配器源 SHA 已变化，不能自动沿用 v3 的真实故障资格。此项不在本轮真实注入禁令下完成。
3. **签发后的短暂分布式竞态不能宣称为零。** Master 先消费一次性授权再签名，Worker 在效果发生前检查短效响应和本地撤销；若状态恰在签发之后改变，仍存在至多 1.2 秒的理论许可窗口。本轮 Marker 实测签发到执行约 90 毫秒。正式批准前应审核该窗口是否满足组委会的安全标准，并在最终代码版本重复门禁测试。
4. 上轮五题累计存储敏感性预算指出 Worker3 数据盘在后续题目可能低于 5 GB；此项没有因认证修复而消失，正式每题仍须刷新真实峰值预算。

因此**不能提出 `READY_FOR_HUMAN_PRODUCTION_APPROVAL`**。完成上述门禁、形成新正式计划/代码 SHA、再次健康预检并取得组委会单独批准后，方可考虑新的真实生产 Attempt。本轮到此停止。
