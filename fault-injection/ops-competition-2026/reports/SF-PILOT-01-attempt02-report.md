# SF-PILOT-01 ATTEMPT-02：调度修复与试生产报告

**最终状态：SF_PILOT_01_BLOCKED。** 本次没有生成完整题目，不能导入比赛服务器。

Worker 本地 UTC 调度、即时启动偏差检查及取消演练通过；但新 Master 控制器存在首条安全心跳的启动竞态，唯一的新正式尝试在观察窗口启动后约 0.50 秒中止。**实际业务故障注入 0 次**，Service 和 Pod 均未触发。本报告不把无故障演练或短时采集冒充完整一小时生产。

## 1. 实际执行范围

| 工作 | 结果 | 说明 |
|---|---|---|
| 旧调度器、时钟和迟发检查审计 | 已执行 | 旧版由 Mac time.time() 等待，SSH 到达决定启动，终态才检查 onset |
| Master 控制、Worker 本地 UTC timer | 已实现、已演练 | 原 2 秒容差不变 |
| Worker2、Worker3 各 3 次无害时间标记 | PASS | 6/6；不暂停业务、不施加内存压力、不改 Kubernetes |
| 两次故意延迟标记与后续取消 | PASS | >2 秒标记即时拒绝，后续两个 marker 未执行 |
| 本地及 Master 回归 | 各 14/14 PASS | 这并未覆盖真实观察器首心跳握手，存在测试缺口 |
| 新鲜五主机/监控/目标身份审查 | 已执行，环境检查 PASS | 见保存的 fresh 安全决策；无沿用过期注入许可 |
| 新 MEDIUM + Image/Cart 无故障采集 | PASS | 687/687 请求匹配 Proxy Span |
| ATTEMPT-02 正式启动 | 已执行，BLOCKED | 第一条心跳尚未写入即被误判 stale |
| Service / Pod 故障 | 均未执行 | Worker runtime/state 和 injection output 均不存在 |
| 完整 60 分钟及 +5/+60/+120/+240 审计 | 未完成 | 正式 capture 尚未产出完整 manifest |
| Public / Private 异常 GT 导出 | 未执行 | 不满足完整采集前提，不制造空包、标签或评分答案 |
| 双副本归档 / SHA /恢复读取 | PASS | 原始失败记录、准备数据、代码、后续草案均保存 |
| 第三次正式尝试 | 未执行 | 已停止，等待人工审核 |

## 2. 调度实现与时钟证据

代码目录：`/root/ops-competition-2026/attempt02-20261014/scripts/`。

- Master 上的 `pilot_controller.py` 负责阶段协调、实时安全心跳和短期授权；Mac 只分发与查看。
- Worker3 Service、Worker2 Pod 使用绝对 UTC 的本地 systemd calendar timer。AccuracySec=10ms，RandomizedDelaySec=0，Persistent=false。
- 计划 SHA-256、scheduler SHA-256、目标 UID/容器/PID start_ticks、原 adapter 与 holder 版本绑定。
- Worker 执行时验证 NTP、近期 Master/Worker 时钟偏差及不确定度、最多 4 秒授权租约及撤销状态。
- 启动误差同时检查原始 UTC 偏差与计入时钟不确定度的偏差，不超过 2 秒。时钟总界预算为 0.5 秒。
- 在原 adapter 保存 ACTIVE / actual_start 后同步核验；超差会立即进入原 finally cleanup。Master 也立即读取 actual_start，反馈上界限 2 秒。
- 原 `competition_adapter.py` 未修改，SHA 为 `791481b3e12ce6f64bb77c75acaff7df8000d800081589194ccde5d5ad3f2a92`。独立 duration+5 秒恢复 timer、精确 PID 恢复及 memory holder 机制复用已验证代码；新 wrapper 只增加两个注册 ID 和同步检查。
- 两个故障的本地任务提前部署，但故障前 fresh baseline / safety / authorization 尚未通过时不会签发租约。提前部署不等于已获准执行。

当前 Mac 比 Master 慢约 2.28 秒；该值仅诊断，未修改任何旧时间戳。Master 与 Worker 的直接往返时钟测量在新 Master 控制器上执行，测量和全部样本保存在 `results/clock-*.json`、`results/clock-runtime.jsonl`。Marker 最终测量中 Worker2−Master 约 −9.16 ms，Worker3−Master 约 −12.25 ms，不确定度约 19–20 ms。Mac 不参与精确触发。

### 无故障调度实测

| Worker/轮次 | Master 预定 UTC | 原始启动偏差 | 含时钟不确定度上界 | Master 反馈上界 | 结果 |
|---|---|---:|---:|---:|---|
| w2 / 1 | 2026-10-14T04:39:56.660162+00:00 | 0.253199 s | 0.281822 s | 0.637348 s | PASS |
| w2 / 2 | 2026-10-14T04:40:16.660162+00:00 | 0.083386 s | 0.111986 s | 0.450288 s | PASS |
| w2 / 3 | 2026-10-14T04:40:36.660162+00:00 | 0.078041 s | 0.107507 s | 1.233230 s | PASS |
| w3 / 1 | 2026-10-14T04:39:56.660162+00:00 | 0.217900 s | 0.249623 s | 1.315294 s | PASS |
| w3 / 2 | 2026-10-14T04:40:16.660162+00:00 | 0.078847 s | 0.111176 s | 1.045810 s | PASS |
| w3 / 3 | 2026-10-14T04:40:36.660162+00:00 | 0.077521 s | 0.108898 s | 0.552491 s | PASS |

两个额外迟发 marker 原始偏差分别 2.369662 / 2.379033 秒，均触发即时拒绝；对应后续 marker 已撤销且未生成 marker 文件。系统日志、计划哈希、实际启动时间和反馈时间保存在 `results/marker-*.json` 与 `results/scheduler-rehearsal.json`。所有测试任务已停止，无业务故障。

## 3. 新鲜无故障采集验证

独立准备数据：`/data/stage5-v3/ops-competition-2026/attempt02-20261014/readiness-smoke/`。

随机种子 1007100，与正式尝试 1005100 分离。保持 MEDIUM 1.4 sessions/s、Image 1/s、Cart 2/s。负载执行 60 秒，采集窗口额外覆盖边界和请求完成时间；**不是正式题目数据**。

- 687 条完成请求全部成功，687/687 匹配真实 Proxy Server Span。
- Metrics：2,040 个样本；Logs：9,988 条；首次 Trace：15,010 Spans；+60 秒补齐：15,011 Spans。
- 首次与 +60 数据均保留，差异不覆盖；未将短时 smoke 当作一小时 +120/+240 完整性证明。
- Collector 配置 SHA `128ed286e8a959aacddc2ee6c00e2d6e8b0a3eb18ea43e5de730a20e3793af2d` 不变；拒收计数无增量。
- 三种真实 schema 各 12 条观测的 Parquet 回读测试通过，纳秒、原 ID 和未知值保留；仅为软件回归。

## 4. 唯一正式尝试及失败原因

新计划 SHA-256：`70fc081fbb590b35b5cf36c99e9c40ae8b1eafd1d4b483a647488012ab69b1fa`。

原始根目录：`/data/stage5-v3/ops-competition-2026/runs/SF-PILOT-01-ATTEMPT-02/`。

| 项目 | UTC / 状态 |
|---|---|
| 计划全窗口 | 2026-10-14T04:49:10.838859+00:00 → 2026-10-14T05:49:10.838859+00:00 |
| Service 计划 | 05:01:10.838859 → 05:06:10.838859 |
| Pod 计划 | 05:24:10.838859 → 05:29:10.838859 |
| 观察器被停止 | 04:49:11.338314 |
| Worker3 timer 撤销 | 04:49:11.204831 |
| Worker2 timer 撤销 | 04:49:11.297557 |
| 实际 Service / Pod onset、持续时间、恢复 | N/A：没有执行故障 |
| 控制器终态 | CASE_CONTROLLER_INVALID / LIVE_SAFETY_HEARTBEAT_STALE |

具体缺陷在 `scripts/pilot_controller.py:37–39`：一到 T+0 就要求 `safety.jsonl` 非空。观察器 `scripts/pilot_observer.py:79` 会先执行 Kubernetes、Prometheus、OpenSearch 和 Collector 检查，然后才写入第一条心跳。两者启动并发，控制器在首条写入前误判 stale，并按安全停止路径中止整个尝试。

**这是本轮新控制器实现缺陷，不是已证实的 Collector、NTP、业务健康或注入失败。** 14 项回归及无害 marker 演练未覆盖控制器与真实观察器的启动握手，不能据此宣称端到端控制器已经就绪。

正式 runtime 中仅保留 7 条业务、2 条对照的已完成请求记录，均成功；中断时未完成请求数量不能从这些完成记录推断。正式首条安全采样、三模态 capture manifest 均未完成。因此：

- 完整正式 Metrics/Logs/Spans 数量、每分钟覆盖和正式请求端到端匹配：**N/A / 未完成**。
- 正式 Collector 连续内存峰值、exporter 超时/重试增量：**未形成完整观测，不宣称为 0**。
- 故障 onset 精度与两次故障恢复：**未验证**，不能拿 marker 或旧资格实验代替。
- Public 六件套、异常候选、事件 GT：**未生成**；异常候选不是“观测到 0 个”，而是没有执行标注。

## 5. 停止、环境检查与数据保护

最终只读五主机检查时间：2026-10-14T04:52:22.507902+00:00。

`COMPETITION_ENVIRONMENT_PASS_WATCHDOG_PENDING`，`current_environment_blockers=[]`；41 项附加环境检查均通过，4 节点 Ready、Calico/网络、CoreDNS、关键服务和监控状态正常，无停止进程、残留规则或活动 fault timer。

保留原始 Global Safety 输出：其唯一 false 项是 `component_independent_watchdog`，因为最终检查不授予新的故障执行许可；这不是将实际环境失败覆盖为 PASS。已验证原恢复代码与证明保留，但本次没有真实故障，不新增恢复成功结论，`faults_allowed=false`。

最后 Collector grpc/http refused 均为 0，与初值一致；最终工作集 274.28 MiB。Logs / Traces 最近数据年龄分别约 1.72/1.06 秒。这是最终快照，不代表整个一小时质量。

Master 根盘可用 46,124,605,440 bytes，数据盘可用 290,724,155,392 bytes。数据盘 UUID `a35051a2-3d09-4b83-bbc0-3bb055e8a4f3`。所有大文件仍写入数据盘，容量阈值未调整。

所有本次注册源代码哈希核验未改变。旧失败报告 SHA 仍为 `45aadba7eb5cf53d79bb9b8bb6c82078da1ded631a47d8b233a8d81307bd6a4b`。没有覆盖原 SF-PILOT-01，没有修改 Stage5-v1/v2/v3 冻结数据，没有访问 TEST 或比赛服务器。

## 6. 后续修复草案，未部署到本次尝试

`/root/ops-competition-2026/attempt02-followup-draft-20261014/`。

增加 WAITING_FOR_FIRST_SAMPLE 状态：等待不等于 PASS，不能签发故障租约；首条真实样本必须在一个 15 秒监控周期内到达，之后仍执行原 30 秒 freshness 上限。未到、失败、过期或未来时间均 fail closed。8 项本地合成回归通过。

这只是候选状态机和测试，**未集成替换已注册代码、未运行新正式尝试、未宣告生产就绪**。下一步需先在独立无故障窗口用完整 Master 控制器和真实观察器验证启动握手、缺失心跳和取消分支，再提交新的试生产请求。不能仅凭此单元测试自动启动第三次生产。

## 7. 双副本归档与 SHA

Worker2、Worker3 相同根目录：
`/data/rca-archives/ops-competition-2026/SF01-ATTEMPT02-BLOCKED-20261014/`。

Master 归档索引：
`/data/stage5-v3/ops-competition-archive-index/SF01-ATTEMPT02-BLOCKED-20261014/result.json`。

| 部分 | 文件数 | Bytes | 结果 | 源清单 SHA-256 |
|---|---:|---:|---|---|
| raw | 15 | 381,574 | 两台 SHA-256 一致 | `d5cb81c83118e57997463ff1587e5a78ba1347121d89941fcd02484fddf9752d` |
| preparation | 89 | 108,432,697 | 两台 SHA-256 一致 | `fde40f2d6ec1893e0346379c592dd23077a750fe7f5892bba57b80a13c952bce` |
| control | 79 | 1,241,101 | 两台 SHA-256 一致 | `9fcbd5f39d4fe2a319e4603c637936d2fa3d8ef777b6cef5081db96825d40ce1` |
| followup-draft | 4 | 3,515 | 两台 SHA-256 一致 | `8a4c7789b47429c36f57d2cc90c856546cf0546ed83f321359e8ecbd2d119b9a` |

全部文件逐项 SHA-256 一致。准备数据完成 Metrics/Logs/Traces 样例恢复读取；正式 raw 没有成熟三模态 capture，因此其 restored_reads 为空，不能宣称正式三模态恢复验证通过。归档通过只证明已存在的失败证据保存完整。

## 8. 交付路径与审核结论

- 报告：`/root/ops-competition-2026/reports/SF-PILOT-01-attempt02-report.md`
- 注册计划：`/root/ops-competition-2026/plans/SF-PILOT-01-ATTEMPT-02.json`
- 源/计划 manifest：`/root/ops-competition-2026/attempt02-20261014/plans/manifest.json`
- 演练结果：`/root/ops-competition-2026/attempt02-20261014/results/scheduler-rehearsal.json`
- 失败终态：`/root/ops-competition-2026/attempt02-20261014/results/controller-terminal.json`
- Worker 无注入与取消证据：`/root/ops-competition-2026/attempt02-20261014/results/worker-final/`
- 最终环境检查：`/data/stage5-v3/ops-competition-2026/audits/COMP-SF01-A02-FINAL-READONLY/`
- 新正式尝试原始目录：`/data/stage5-v3/ops-competition-2026/runs/SF-PILOT-01-ATTEMPT-02/`
- Public 数据包：未生成。
- Private 异常 GT：未生成；已有注入计划是预注册私有资料，不能当作异常 GT。
- 比赛服务器导入：**不可导入，且未连接或操作 192.168.51.50 / 192.168.51.51**。

本轮到此停止。最终结论为 **SF_PILOT_01_BLOCKED**，等待人工审核。
