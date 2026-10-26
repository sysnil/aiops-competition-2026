# SF-013～SF-017 生产恢复前最终安全验收

**最终状态：`PRODUCTION_BLOCKED`。** 截至 2026-10-19 13:15 CST，本轮真实故障注入 0 次、正式 Case/Attempt 新建 0 个、生产主机重启 0 次。1800 秒无故障采集、Guard 崩溃恢复、足额异机归档和最终环境健康均通过；但无害 Marker 实测证明 Master→Worker SSH 失联时，已注册 Timer 可以在本地授权仍有效的窗口内触发。该行为不符合“无法确认有效授权时不得启动新故障”的强制门禁。Master/Worker 重启生命周期亦未通过隔离或经批准的真实重启验证。本报告不是生产许可，不启动 SF-013 ATTEMPT-03 或 SF-014～SF-017。

## 范围与版本

- 权威代码：`/root/ops-competition-2026/sf013-adapter-v3-20261019/`、`/root/ops-competition-2026/final-case-timer-safety-v1-20261019/`。原验收报告位于 `/root/ops-competition-2026/reports/FINAL_CASE_TIMER_SAFETY_FIX_REPORT.md`。
- 原 66 个代码/合同文件的 SHA 与 `results/code-and-evidence-sha-manifest-v3.json` **66/66 一致**，该 Manifest SHA-256：`b19a7d27918d096602895db082a19a70d4ea43d2e44943cce8ab7b6b33b69b23`。`fault_worker.py` SHA：`fc76dcfe86fa3e0b52724d8b89fc73550be1714cc01a3c1ba86d207b94ac46b0`；`timer_lifecycle.py` SHA：`0c4b76a8c7ec1c361d7943d75a886c2721c296dcdb29a9bc4e5d82618c7e44bc`。本轮未修改这两份权威代码。
- 本轮测试代码和证据独立存放在 `/root/ops-competition-2026/final-case-readiness-audit-20261019/`；无故障采集控制文件在 `/root/ops-competition-2026/final-case-production-readiness-20261019/`，原始验证数据在 `/data/stage5-v3/ops-competition-2026/final-case-production-readiness-20261019/`。无故障计划 SHA-256：`94be34ff3b975d1da50498a8acebe58cccf41b57e07266648ebe262785efca22`，明确 `faults_allowed=false`。
- v3 适配器单元测试 **64/64 PASS**，正式入口相关单元测试 **12/12 PASS**。这些测试不代替断联或重启生命周期测试。

## Timer 生命周期与失联

| 场景 | 结果 | 证据与边界 |
|---|---|---|
| 控制器异常退出、Timer 尚未触发 | PASS（连通时） | 前轮 SIGKILL Marker 测试和本轮 Guard 测试均验证按 Attempt 精确撤销；未误撤销其他 Attempt。 |
| Guard 进程被 SIGKILL | PASS（连通时） | 本轮 systemd `NRestarts=1`；重启后的 Guard 发现控制器 SIGKILL，于计划触发前约 5.7 秒撤销 Worker Timer，Marker 未触发，重复 cancel 返回同一安全终态。`results/guard-restart-probe.json` SHA：`fd58eed3ce17b3ee87b7109b7e7efc59774314864efc503114ac52e7bb2871c2`。 |
| Master→Worker SSH 通道中断且控制器退出 | **FAIL** | 测试专用控制 socket 在 Worker Timer 注册和授权后暂时隔离；Master 对 Worker 的 SSH 确认失败，Worker 无害 Marker 仍于预定 `05:04:38Z` 触发，触发时尚无撤销记录。Guard 未留下成功撤销终态；之后 socket 恢复，测试程序才对该 Attempt 执行精确撤销。业务进程未暂停、未施压。`results/ssh-loss-marker-probe.json` SHA：`4f4cf31494b24297135a21fb8f298f34b1ac3a64214aa49affda4414f168b7e2`。 |
| Master 网络不可达但本地租约尚有效 | **FAIL（校验函数级）** | `unshare --net` 内仅 loopback，连接 Master SSH 返回 `Network is unreachable`；当前 `fault_worker.validate_lease` 仍接受作用域/时钟/4 秒有效的本地租约，撤销和过期租约正确拒绝。该探针未调用 `fire`、Timer 或适配器；结合上面的真实 Marker 测试，足以判定实时 Master 存活不是启动条件。`results/lease-loss-probe-v2.json` SHA：`63a27071033a721a9d106e62dd787c297ed844d08b8e4e45ccb94cd4ad7b4be2`。 |
| Master 主机重启、Worker 主机重启、重启期间待触发 Timer | **未验证** | 当前生产主机没有重启；`Persistent=false`、Guard `Restart=on-failure` 和短租约不构成重启安全实测。当前环境无已验证的独立 systemd 重启沙箱，不以静态推断冒充 PASS。 |
| 已启动故障的独立 Watchdog | 历史自测 PASS、重启场景未验证 | 前轮牺牲 `sleep` 进程的 Watchdog 恢复证据保持有效；未在本轮启动真实故障，也未测试主机重启时真实故障恢复。 |

**安全解释：** Master Guard 与控制器均运行在 Master。SSH 断联或 Master 失联时，即使 Guard 想撤销，也可能无法把撤销送达 Worker；Worker 的真实故障入口当前只看本地租约/授权文件/时钟证明，没有触发瞬间的独立 Master 活性确认。4 秒窗口不能作为 fail-closed 证明。正式恢复前需要前瞻性修复 Worker 侧授权/失联处置并重新版本化，重复断联、Guard 崩溃与完整无故障演练。不能回改旧 Attempt 或事后放宽门禁。

## 1800 秒无故障联合演练

**结果：`NOFAULT_1800_CHAIN_PASS`，数据质量 `NO_FAULT_VALIDATION_PASS`。** 本轮使用独立、仅允许无害 Marker 的 `start_formal_nofault → capture → observer → marker_controller → marker_guard → cleanup` 链路，共六段真实业务和现有 Metrics/Logs/Traces 采集。主业务合同为 1800 秒，起点 `04:27:06.166159Z`；采集查询计划的最后结束点为 `04:57:36.166159Z`（比主业务窗口多 30 秒），随后完成最后一段 +60/+120/+240 秒不可变 Trace 审计。该 30 秒查询尾部未冒充额外业务窗口。六段 capture/补齐/审计子进程均退出码 0，Observer、Controller、Guard 终态一致且无 Timer 残留。预定 Marker `04:37:06.166159Z`，Worker 实际写入 `04:37:06.239264Z`，偏差约 0.073 秒。

- 业务步骤 **7,930/7,930** 成功；Image、Cart、Home、Recommendations 四条固定流分别 **1,800/1,800** 成功。预期请求 ID 到最终存储的 Proxy Server Span 关联完整。
- 最终审计 **349,703 Span**、重复 0；Metrics/Logs/Traces **30/30 分钟**有数据，六段完整。Collector spans/logs/metric-points 的 receiver refused 增量均为 **0**，峰值 working set **346,841,088 B**；Logs/Traces 最大新鲜度 **2.22/4.72 秒**。OpenSearch parent breaker 无新增跳闸，采集 stderr 文件未出现 429，队列及正常 Image/Cart 对照判据均通过。
- 五主机动态磁盘监控 **132 个采样**，终态 `FIVE_HOST_NOFAULT_MONITOR_COMPLETE`，未越十进制 5 GB 底线。
- 联合终态：`/root/ops-competition-2026/final-case-production-readiness-20261019/results/no-fault-1800-01/joint-terminal.json`，SHA `175bf08c9a6842023bf34d1d1ce2c6e07486fcc4006e7ffcfcdfbae9f61bbdce`；评估结果：`/data/stage5-v3/ops-competition-2026/final-case-production-readiness-20261019/validation-runs-v6/VAL6/assessment.json`，SHA `4e09425d6c6cc59e58a8d3f9197efb77064c068fae9139c1f61216af1e6fce75`。

**限制：** v3 正式 `start_formal.py` 将运行身份硬绑定到 `SF-013-ATTEMPT-03` 和真实故障动作；本轮为了不创建正式 Attempt/不运行真实适配器，使用单独哈希锁定的 Marker-only 入口及现有 no-fault v6 capture。它验证共享 Timer/Guard 核心与真实采集链路，但不能声称未经修改的正式入口在完整 1800 秒内已经被原样执行。该次联合演练没有发生采集/Observer 失败，因此“失败状态能否跨整条正式链路传播”仍未获得端到端实测；此前负向 Marker/单元测试不能替代此项。

## 足额归档与容量

按历史单题峰值 **7,350,324,113 B** 创建非稀疏、无敏感信息的 Master 合成原始/Public 载荷；顺序传至 Worker1、Worker2、Worker3 的受保护系统盘目录。三台 Worker 均对传输副本和独立恢复读回副本完成大小、物理块占用、逐文件 SHA-256 校验，结果 `THREE_WORKER_FULLSIZE_ARCHIVE_AND_READBACK_PASS`。Worker1/2/3 根盘在双份占用后的实测剩余分别为 **21.996/39.162/20.588 GB**，均高于 5 GB。合成载荷随后仅凭源 Manifest 与三份回执限定路径清理；Manifest/回执保留，历史数据未删。足额归档结果 SHA `1a79b56acba3cdb64c29334c6d41d4a87d421382ce83abff06dca1d5e31e56d1`，清理结果 SHA `95581cf203ec34129204b208466d1dec88839ecb316adab22aac4a676cb31fe9`。本测试证明字节级完整交接和恢复读回；合成 `export.bin` 不等同于真实 Parquet Schema 验证，真实 Public 格式仍须在未来每题导出后独立检验。

最终快照可用空间（十进制 GB）：

| 主机 | 根盘 | 数据盘 |
|---|---:|---:|
| Master | 45.24 | 56.04 |
| Worker1 | 36.70 | 18.73 |
| Worker2 | 53.89 | 18.06 |
| Worker3 | 35.29 | 11.30 |
| VM1 | 87.39 | 不适用 |

使用**未修改**的五题 DRAFT 分配/单题 7.35 GB 承诺，从上述新快照重新投影：基础情形 SF-013～SF-017 均不越线；若 Worker 数据盘每题额外产生 2 GB 后台写入，Worker3 在 SF-015 后预计 **5.30 GB**，SF-016 后 **3.30 GB**，SF-017 后 **1.30 GB**，后两题不满足 5 GB 硬底线。`results/fresh-storage-projection.json` SHA `582c320029c1ca1150ac6dced997004d2ac3bc0c00d8dbed186d3d788b5e749d`。因此五题连续生产**不能预先一揽子批准**；每题须刷新实际容量与后续峰值，必要时先获批准扩容/迁移，不能删历史归档或降低底线。

## 最终环境与历史保护

- 2026-10-19 05:10Z 快照：四个 Kubernetes Node Ready；13 个关键业务/观测/Calico Pod Ready；四个 Calico 节点各 **3/3 BGP peer Established**；Prometheus healthy、OpenSearch green 且 unassigned shard 0。Collector UID `b6e0e86e-fd79-46ed-be58-61d494c6b074`，配置 SHA `ad42e84b2a1d25b6b9b716f2a119f8d3ab6c50a975b860f3a8eed939618dd05f` 未改变，最终 grpc/http refused 均 0，working set 331,100,160 B，日志/Trace 存储新鲜。
- 五机无活动竞赛 Service/Timer；五机 qdisc 无 `netem`/`tbf` 残留；无新正式故障。最终健康快照 SHA `b5a26426095c26a466ed16e70d10790bf3741e150aa93c5d98141221839dea6f`，qdisc 证据 SHA `ff719961f19b3fcfabaae759bd9a551871c998512be9f4a666b4639f889dc8d5`，Collector 快照 SHA `55db61f50ce907d23ab964194ece687f145496d5a892f6c85196d1e42d483049`。
- 历史 SF-003～SF-012 总账 SHA `813035c9c5e69741c73de7d0afb92e427d8745714992ab74c0c41c414f7e69f8` 未变；十题 **10/10 Public Manifest、60/60 Public 文件 SHA** 与时间窗/行数校验通过，未读历史 Private GT。历史复核证据 SHA `0fc084facb734eb7ab1da4be6f340ca6bf31a3429fbb1ed918e65fd4542d5d4d`。SF-013 ATTEMPT-01/02 原失败记录 SHA 仍为 `0e7958de950c18611f0d2e75726967325ab906d1b3458042a102076da3a4b471`、`cdcaf590e1ba755525efa769b6bb17fa49b5f963cdb4bf352b4d1f9596bbe17b`；没有覆盖或追认。

## 恢复生产前的必需动作

1. **修复 fail-closed 缺口。** Worker 在 Timer 触发前必须获得可验证的实时、Attempt 绑定授权；Master/SSH 不可达时新动作不得启动。既有短租约和 Master 侧 Guard 不能单独承担这项保证。修复后重新固定代码/合同 SHA，并重复断联、Guard 退出、跨 Attempt 隔离、完整无故障联合演练。
2. **完成重启生命周期验证。** 先在可隔离的 systemd 环境做 Master/Worker 重启模拟；若必须重启生产主机，向组委会提交维护窗口、受影响业务/观测服务、仍待触发 Timer 的 Marker 计划、带外恢复通道、回滚及五主机复原检查，取得单独批准后才实施。本轮未批准、未执行真实重启。
3. **复核生产入口与容量。** 新版本必须证明正式入口和 Marker-only 路径共用相同的授权、撤销与失败传播逻辑；任何新的正式 Attempt 还须重新核验 Pod/PID 身份、独立 Watchdog、Collector、无残留以及逐题容量。SF-016/017 的累计敏感性缺口须通过实际空间或获批归档调整解决。
4. 完成以上强制门禁并取得**另一次人工生产批准**后，才能重新评估是否输出 `READY_FOR_HUMAN_PRODUCTION_APPROVAL`。当前不得启动 SF-013 ATTEMPT-03，也不得自动生产 SF-014～SF-017。
