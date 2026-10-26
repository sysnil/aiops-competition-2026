# SF-013～SF-017 决赛候选 Case 生产报告

状态：**BATCH_STOPPED_BY_TIMER_ABORT_SAFETY_DEFECT，新增 READY 0/5**。本轮没有真实故障注入，也没有新增 Public 数据包或 Private GT。SF-013 的两次 Attempt 均在故障前失败并保留，按照“连续两次非偶发失败暂停该 Case；涉及实际安全风险停止整批生产”的规则，未开始 SF-014～SF-017。不得把规划的 12 个候选写成 12 个已可用数据集。

本报告中的 `design/`、`plans/`、`results/` 均相对于 Master `/root/ops-competition-2026/final-cases-20261019/`。

## 已有决赛候选基线

只读核对原生产总账及各 READY Public Manifest、Private GT 草案。原总账 SHA-256 为 `813035c9c5e69741c73de7d0afb92e427d8745714992ab74c0c41c414f7e69f8`，本轮未修改。

| Case | READY Attempt | 已验证机制和持续时间 | Public Metrics / Logs / Spans | GT 候选数 | 本轮状态 |
|---|---|---|---:|---:|---|
| SF-004 | ATTEMPT-05 | 首页 HTTP 延迟 250 ms、60 s | 39,000 / 196,371 / 349,727 | 3 | 历史 READY，未改动 |
| SF-006 | ATTEMPT-01 | Frontend 单 Pod 内存 32 MiB、60 s | 39,000 / 199,152 / 346,841 | 4 | 历史 READY，未改动 |
| SF-007 | ATTEMPT-08 | Recommendation 两副本 ServicePause、30 s | 40,752 / 190,786 / 339,854 | 23 | 历史 READY，未改动 |
| SF-008 | ATTEMPT-09 | Frontend 单 Pod 内存 32 MiB、300 s | 40,560 / 187,619 / 346,964 | 4 | 历史 READY，未改动 |
| SF-009 | ATTEMPT-04 | 首页 HTTP 延迟 250 ms/60 s + ServicePause30 | 40,734 / 195,753 / 354,327 | 16 | 历史 READY，未改动 |
| SF-011 | ATTEMPT-05 | 首页 HTTP 延迟 250 ms、60 s | 40,792 / 192,786 / 349,397 | 2 | 历史 READY，未改动 |
| SF-012 | ATTEMPT-04 | Frontend 单 Pod 内存 32 MiB、300 s | 40,560 / 190,092 / 351,032 | 1 | 历史 READY，未改动 |

上表 GT 数为总账的候选数，不代表人工批准或独立异常事件数。既有题的 30 分钟 Public、Admission 和归档证明见原生产报告与交接索引。两组机制重复（004/011、006/008/012）仍然存在，不能凭故障参数推断新题难度等价。

## 五题设计与资格

草案为 `/root/ops-competition-2026/final-cases-20261019/design/SF-013-to-SF-017-preregistration-draft.json`；它不是五题的正式注入许可。均拟沿用 1,800 s LOW-STABLE 负载：0.7 sessions/s，Image、Cart、首页、Recommendation 各 1/s，Before/During/After 固定。

| Case | 原建议与拟采用机制 | 拟定窗口（相对 T0） | 实际状态 |
|---|---|---|---|
| SF-013 | PodCPUStress 尚为 `DRAFT_SAFETY_BLOCKED_NO_INJECTION`；选择已资格验证的 Frontend PodMemory32MiB/300s fallback | 600～900 s | ATTEMPT-01、02 均故障前失败；暂停自动重试 |
| SF-014 | Recommendation ServicePause30 | 600～630 s | NOT_STARTED，未注册正式 Attempt |
| SF-015 | 已验证首页 HTTP ReqDelay250ms/60s | 900～960 s | NOT_STARTED，未注册正式 Attempt |
| SF-016 | Frontend PodMemory32MiB/300s | 600～900 s | NOT_STARTED，未注册正式 Attempt |
| SF-017 | HTTP ReqDelay250ms/60s 与 ServicePause30，非重叠 | 600～660 s、1080～1110 s | NOT_STARTED，未注册正式 Attempt |

SF-013/016 的拟用目标与历史 Frontend 内存题高度相似，SF-014/015/017 也复用历史机制。新 Public 异常强度、正常对照和多模态难度均**尚未测得**，没有资格宣称难度与七道既有题一致。任何新目标或接口都需要重新做真实身份、请求量、Watchdog 与观测资格检查。

## 实际执行与失败终态

SF-013 ATTEMPT-01 先通过 54/54 单元测试、五主机预检、Worker2 无害 Watchdog 自测及正式输入只读验证。代码/设计 Freeze Manifest SHA-256 为 `de4c2b11f074d5d307c95991b3daa3669218f1b86f91e65225e71ed59674f186`；Run Manifest SHA-256 为 `1f78644422ee0fa44555bd63bd40973c3dac6bed0ef83c439e3c7a3a2885d388`。由于注册身份在启动前超过既有 180 s 新鲜度门禁，**未启动采集、未注入**，按原哈希撤销 Worker2 Timer。失败记录：`results/SF-013-ATTEMPT-01-preinjection-failure.json`。

SF-013 ATTEMPT-02 采用新的代码/计划版本，保留原 Admission、故障强度和 5 GB 门槛。54/54 测试、入口烟测、Worker2 独立 Watchdog、五主机容量与残留均通过；Freeze Manifest SHA-256 `df732a9d834344bf145c897bc7452d6d8629049c9e211675bc84b6c3b7b99729`，Run Manifest SHA-256 `65ae9b1c90177279ceb517965c4ec65ac6ccc2c99c6be82bae4fc303b8c040c3`。T0 预定 `2026-10-19T02:54:43.109086Z`，正式单元在 `02:51:11Z` 启动，身份年龄 118.27 s。控制器在 `02:51:12Z` 因重复注册 Worker Timer 被 systemd 拒绝，停止采集，仍在 T0 前。**故障 0、完整窗口 0、Public 0、GT 0**。失败记录：`results/SF-013-ATTEMPT-02-preinjection-failure.json`。旧 Attempt 未覆盖。

根因是外部先执行了 `fault_worker.py arm`，而新控制器在 `sf003_controller.py:186` 仍会自行 `backend('arm')`。第二次 arm 抛出异常时 `armed` 尚未设为 True，`finally` 在同文件 230 行跳过了 cancel；注册 Timer 因而短暂保持 pending。运行专属 `fault_worker.py cancel` 随后生成 `REVOKED.json`/`cancelled.json`，Timer 和 Service 均核验为 inactive，未出现 `actual-start.json` 或业务故障。这是故障前中止后可能留下待触发 Timer 的安全缺陷，不能仅靠本轮人工发现作为正式生产保障。必须在新版本中明确 Timer 的唯一所有者、实现任何异常终态都核对并撤销该 Attempt 的 pending Timer，并用真实 systemd 无害 Marker 做负向联合演练后才考虑新注入。不得修改 ATTEMPT-01/02 冻结版本追认结果。

两次失败原始目录均未删除。Master 的 `runs/SF-013-ATTEMPT-01/02` SHA 清单在 `/data/stage5-v3/ops-competition-archive-index/SF-013-ATTEMPT-01/02/`；Worker1 的独立副本在 `/data/rca-archives/ops-competition-2026/SF-013-ATTEMPT-01/02/`，源 Manifest SHA 分别 `b2ddbb6ddd144f7f8b9401e1cf003fb6f7a7af3ff9c22ffdcd258d4629cb9fb6`、`236d92d8e33f2dd49d986ebf764774c3aad052a020289f6029567932cca5302d`，Worker1 逐文件 SHA PASS；两次均无三模态文件可供 Parquet 读回。Worker2 的运行专属 arm/revoke/cancel 原始证据保留在原 Worker，副本在 Master `final-cases-20261019/results/worker2-runtime-att01/02/`。失败尝试无三模态数据，不生成异常 GT。

## 资源与最终安全

SF-013 的单题容量合同沿用历史峰值 7,350,324,113 B，Worker1 数据盘启动前预测扣除预留后约 11.97 GB，超过十进制 5 GB 底线。五题累计容量尚未获准；特别是 Worker3 数据盘最终仅约 11.39 GB，不能按同一 7.35 GB 归档预留再加 2 GB 后台写入预算承接新题。后续若改用 Worker 系统盘归档，必须独立完成容量、SHA 与恢复读回资格，不得靠删除历史数据释放空间。

最终快照 `results/final-health-snapshot.json`：Master 根盘/数据盘 45.263/60.660 GB；Worker1 36.719/19.089 GB；Worker2 53.861/17.750 GB；Worker3 35.310/11.390 GB；VM1 根盘 87.401 GB。四个 Kubernetes Node Ready，四个 Calico Node Pod Ready，Prometheus healthy，OpenSearch green。Collector 配置 SHA `ad42e84b2a1d25b6b9b716f2a119f8d3ab6c50a975b860f3a8eed939618dd05f`，receiver refused grpc/http 均 0，工作集 256,720,896 B，Logs/Traces 存储新鲜度各 <1 s。`results/final-host-residue.json` 为 `HOST_RESIDUE_PASS`；`results/final-competition-units.json` 为 `COMPETITION_UNIT_RESIDUE_PASS`，五主机竞赛 Service/Timer 列表均空。未访问 50/51 比赛系统，未修改原 SF-003～SF-012 总账或 GT。

## 交接判定

SF-013：`BLOCKED_BY_SAFETY`，自动重试暂停；SF-014～SF-017：`NOT_STARTED`。新增 `READY_FOR_IMPORT` 数为 **0**，新增 `FAILED_ADMISSION`/`FAILED_CAPTURE` 正式完整窗口数为 0，新增 `DRAFT_PENDING_HUMAN_REVIEW` GT 数为 0。交接索引 `plans/SF-013-to-SF-017-master-ready-handoff.json` 的 READY entries 必须为空。恢复本批之前须先修复并演练 Timer abort cleanup，审核 SF-013 两次失败和新容量归档策略；随后对剩余每题分别重新做新鲜安全预检及机制资格，不能沿用已过期的快照。
