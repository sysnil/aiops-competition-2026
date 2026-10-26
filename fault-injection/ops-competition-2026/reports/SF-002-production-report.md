# SF-002 至 SF-009 融合故障验证：20 GB 门槛后续执行

更新时间：2026-10-14T16:14:50.793178+00:00。最终状态 **BATCH_8_PARTIAL**。本轮实际生成 SF-002：完整 60 分钟、2 次真实故障、均恢复、Public 六文件及双副本完整校验通过。其余 7 道未开始，保留原组合，不替换机制、不伪造执行。

## 存储授权与执行版本

Master 与 Worker1/2/3 的绝对剩余磁盘下限按用户本轮明确授权改为 **20,000,000,000 bytes（20 GB）**。使用独立执行版本，未修改历史规则、原始数据、科研冻结资产或既有归档。CPU、内存、Collector、时间同步、2 秒启动容差、恢复及数据质量规则均未放宽。

单题峰值增量仍保留 Master 数据盘 34 GiB、Worker2 31 GiB、Worker3 29 GiB，JSON 24 GiB、原始加控制数据 26 GiB、导出临时 2 GiB、产物 2 GiB 上限；不是只看开跑前剩余 20 GB。后续每题都必须重新计算峰值预算，本次通过不代表余下七题已获存储许可。

执行代码：`/root/ops-competition-2026/sf002-production-20261014`；磁盘政策：`/root/ops-competition-2026/sf002-production-20261014/design/storage-policy.json`。Run Manifest SHA-256：`f4f35800bfa6536b048f9ead45699800c582cf42c291cb0f95d17ae2463642fc`。

## 实际测试和生产

58 项批量规划/调度测试通过；SF-002 正式分支 56 项非注入回归通过。六事件 Marker 联合演练通过，最大启动误差上界 0.386294 秒，错误哈希、错误 Worker 和取消后重启均被拒绝。Marker 只证明调度流程，不替代真实故障恢复证据，也不代表 2–6 个任意新机制已获准执行。SF-002 复用已验证的两事件正式后端。

实际观测窗口：`2026-10-14T14:55:28.695044+00:00` 至 `2026-10-14T15:55:28.695044+00:00`。负载 MEDIUM 1.4 sessions/s、Image 1/s、Cart 2/s，seed=20261014002；全程不调整。

| 事件 | 机制 | 实际开始 UTC | 持续秒 | 启动误差上界秒 | 恢复 |
|---|---|---|---|---|---|
| SF-002-E01 | ServicePause | 2026-10-14T15:07:28.979864+00:00 | 300.000142 | 0.326802 | COMPETITION_CLEANUP_ACTIONS_VERIFIED |
| SF-002-E02 | PodMemoryStress | 2026-10-14T15:30:28.988004+00:00 | 300.000137 | 0.328235 | COMPETITION_CLEANUP_ACTIONS_VERIFIED |

ServicePause 为两个 Recommendation 副本整体暂停；PodMemoryStress 仅作用于登记的 Frontend Pod，分配 32 MiB。实际公开内存中位增量 34.568 MiB、sibling 增量 0.000 MiB，连续异常 19 点并恢复。容器总增量包含 helper 开销，不冒充精确分配值。

## 数据质量与异常草案

| 来源 | 已派发 | 成功 | 公开 Proxy Span 匹配 |
|---|---|---|---|
| business | 29659 | 29489 | 29659 |
| image_asset | 3600 | 3600 | 3600 |
| cart_view | 7200 | 7200 | 7200 |

全部 11 个分段通过不可变 +5/+60/+120/+240 秒审计；40459 次派发均有终态，未完成派发 0，无法解释的 Span 缺失 0。真实业务失败分类：`{"business:TimeoutError:None": 139, "business:HTTPError:500": 31}`。未发出的 session 和 MAX_TRANSITIONS 另行保留：`{"None": 4568, "MAX_TRANSITIONS": 22, "TimeoutError": 139, "MISSED_ARRIVAL": 280, "HTTPError": 31}`，不伪造成 HTTP 请求或删除。

Collector 工作集峰值 324.797 MiB，receiver refused 增量 0，UID 和配置保持不变；可选 exporter/retry 指标缺失仍为 UNKNOWN。Collector 可检索日志快照保留原始日志及统计，不能仅以未拒收替代最终存储验证。

| 数据 | 实际条数 |
|---|---|
| Metrics | 84982 |
| Logs | 573991 |
| Spans | 818797 |
| Traces | 118968 |

公开包六文件共 78,046,401 bytes。60 个分钟均有三模态数据；Parquet 全量读回、UTC 纳秒类型、[start,end) 边界、Trace/Span ID、Pod UID、Trace Index、SHA-256 和泄漏扫描通过。没有向 Public 放入计划、故障数答案、调度字段或 GT。

基于 Public 实际观测产生 59 条异常候选，归并 14 个事件。每条候选重新解析公开证据引用。状态 **DRAFT_PENDING_HUMAN_REVIEW**，没有把两个注入直接改写成两个 GT，未发布正式评分答案。

## 归档与资源

原始、控制、执行代码、导出工具均已双副本保护。最终 Public/Private/Reports 追加后再次校验完整源目录、公开目录、私有目录和后处理代码；原始初始清单中所有文件哈希保持不变。

| 节点 | 最终系统盘剩余 GB | 最终数据盘剩余 GB |
|---|---|---|
| Master | 45.85 | 259.053 |
| w2 | 69.101 | 63.664 |
| w3 | 62.092 | 96.055 |
| w1 | 70.143 | 58.18 |

最终五主机环境状态 `COMPETITION_ENVIRONMENT_PASS_WATCHDOG_PENDING`，blockers=0。通用环境工具的 Watchdog 标记与实际两机制的独立回调/清理证据分开记录；本次真实 Watchdog 和恢复证据均通过。无活动故障 Timer、暂停进程、内存 holder 或网络故障残留。

## 八题状态与剩余阻断

| 题号 | 计划故障数 | 实际完成故障 | 状态 | 未解决条件 |
|---|---|---|---|---|
| SF-002 | 2 | 2 | SF002_READY_FOR_IMPORT | 无 |
| SF-003 | 3 | 0 | SAFETY_BLOCKED | SAFETY_BLOCKED; TIME_PLAN_PENDING_MECHANISM_CONTRACT |
| SF-004 | 4 | 0 | MECHANISM_UNAVAILABLE | MECHANISM_UNAVAILABLE; SAFETY_BLOCKED; TIME_PLAN_PENDING_MECHANISM_CONTRACT |
| SF-005 | 3 | 0 | MECHANISM_UNAVAILABLE | MECHANISM_UNAVAILABLE; SAFETY_BLOCKED; TIME_PLAN_PENDING_MECHANISM_CONTRACT |
| SF-006 | 5 | 0 | MECHANISM_UNAVAILABLE | MECHANISM_UNAVAILABLE; SAFETY_BLOCKED; TIME_PLAN_PENDING_MECHANISM_CONTRACT |
| SF-007 | 4 | 0 | SAFETY_BLOCKED | SAFETY_BLOCKED; TIME_PLAN_PENDING_MECHANISM_CONTRACT |
| SF-008 | 6 | 0 | MECHANISM_UNAVAILABLE | MECHANISM_UNAVAILABLE; SAFETY_BLOCKED; TIME_PLAN_PENDING_MECHANISM_CONTRACT |
| SF-009 | 5 | 0 | MECHANISM_UNAVAILABLE | MECHANISM_UNAVAILABLE; SAFETY_BLOCKED; TIME_PLAN_PENDING_MECHANISM_CONTRACT |

SF-003～009 的阻断来自尚未具备当前比赛安全实现/恢复边界的机制，未因本次磁盘门槛变更自动放行。PodCPU 的旧目标及按进程名清理、Pod/ContainerKill 的实例替换与观察器身份契约、NodeCPU 的共享监控资源隔离、Network 的监控/管理流量排除和新鲜路径证明仍需完成；多个 HTTP 响应/中止机制没有已配置的正式适配器。完整 14 机制状态见 fault-capability-matrix.json。保留原组合，不用已支持机制替换；没有启动这些题目的故障。

两种已执行机制本轮各 1 次，并保留 SF-001 历史结果作为单独参考；不把历史次数计入本批。资源异常未产生请求失败不等于无效，当前结论依据公开资源信号；正常对照稳定不代表证明所有非目标实体均零影响。

先前只读盘点边界事件保留在旧版报告及原 boundary-incident.json；本轮没有访问 TEST 数据/GT、没有执行 TEST。未操作比赛服务器 192.168.51.50/51，未导入数据库或发布 API。

## 交付路径

- Public：`/data/stage5-v3/ops-competition-2026/batch-sf002-sf009/SF-002/public`
- Private：`/data/stage5-v3/ops-competition-2026/batch-sf002-sf009/SF-002/private`
- 单题证据报告：`/data/stage5-v3/ops-competition-2026/batch-sf002-sf009/SF-002/reports`
- 本轮机器可读总账：`/data/stage5-v3/ops-competition-2026/batch-sf002-sf009/revisions/storage20-20261014`
- 归档索引：`/data/stage5-v3/ops-competition-archive-index/SF-PILOT-02-ATTEMPT-01`
- Worker2/Worker3 相同归档路径：`/data/rca-archives/ops-competition-2026/SF-PILOT-02-ATTEMPT-01`

Public manifest SHA-256：`458645d012ff5c6ffac1d9676a0275a15ab9da5bac403c1d3f5b3afa5b11011f`。SF-002 可进入下一阶段导入测试；本轮未进行传输或导入，GT 仍待人工审核。
