# SF-003～SF-012 正式题生产 v2 进度报告

**当前合格完成 2/10：SF-003、SF-010 为 `READY_FOR_IMPORT`；SF-004、SF-005 已真实采集但准入失败；SF-009 在正式注入前被 Collector 拒收阻断；其余 5 题尚未执行。当前正式生产暂停。** 该状态不等于正式 GT 获人工批准，也不等于比赛 API 已发布。新版九题草案为 `/root/ops-competition-2026/plans/SF-remaining-9-production-v2-draft.json`；旧版计划保持原样。每题只有通过独立的当前身份、安全、容量、时序和机制准入后，才生成并 SHA 冻结正式执行 Manifest。

| Case | v2 组合/数量 | 目标及实际窗口 | Public 数据与 GT 草案 | 双副本与最终状态 |
|---|---|---|---|---|
| SF-003 | Frontend PodMemoryStress，1 | Worker2 Frontend UID `d4bb49b4-6fb8-4a07-aaf6-e2caff5df974`；2026-10-16 03:26:49.724～03:56:49.724 UTC；实际 03:36:50.049 开始、持续 60.000 秒并恢复 | Metrics 41,160；Logs 298,746；Spans 433,676；草案 5 候选/5 事件，待人工审核 | Master+Worker3 原始与最终包 SHA、读回 PASS；`READY_FOR_IMPORT` |
| SF-004 | 首页请求延迟，1 | ATTEMPT-01 已完成 2026-10-16 04:47:55.627～05:17:55.627 UTC；真实延迟 84 次请求 | Metrics 41,160；Logs 300,311；Spans 437,558；草案 6 候选/6 事件，不能用于评分 | Master+Worker3 原始与最终包 SHA、读回 PASS；Cart 恢复期正常对照 p95 比值 1.515 > 预注册 1.25，`FAILED_OBSERVABILITY_CONTROL` |
| SF-005 | Recommendation ServicePause，1 | ATTEMPT-02 已完成 2026-10-16 06:10:49.895～06:40:49.895 UTC；双副本真实暂停 30.000 秒并恢复 | 六段三模态原始采集质量 PASS；Image、Cart 各漏发 1 次，未生成 Public/GT | Master+Worker3 原始 5.74 GB SHA、读回 PASS；`FAILED_CONTROL_COVERAGE` |
| SF-006 | PodMemoryStress→ServicePause，2 | 未执行；需组合恢复验证 | 未产生 | `NOT_STARTED` |
| SF-007 | ServicePause→PodMemoryStress，2 | 未执行；需组合恢复验证 | 未产生 | `NOT_STARTED` |
| SF-008 | PodMemoryStress→首页延迟，2 | 未执行；需组合恢复验证 | 未产生 | `NOT_STARTED` |
| SF-009 | 首页延迟→ServicePause，2 | 独立 30 分钟无故障反序演练在 T+301 左右因 Collector 拒收终止；没有正式 Manifest、没有故障注入 | 未产生 Public/GT；部分演练原始证据 68 文件经 Master SHA、读回 PASS | 两个无害 Marker Timer 取消，五主机残留 PASS；`PRE_FORMAL_BLOCKED_COLLECTOR_REFUSAL` |
| SF-010 | ServicePause→首页延迟，2 | 已完成 ATTEMPT-07；2026-10-15 16:37:43.727～17:07:43.727 UTC，两故障均生效恢复 | Metrics 41,581；Logs 295,980；Spans 432,558；草案 20 候选/5 归并事件，待人工审核 | Master+Worker2+Worker3 原始与输出 SHA、读回 PASS；`READY_FOR_IMPORT` |
| SF-011 | ServicePause→PodMemoryStress→首页延迟，3 | 未执行；需独立三故障 1800 秒时间合同 | 未产生 | `NOT_STARTED` |
| SF-012 | 首页延迟→PodMemoryStress→ServicePause，3 | 未执行；需独立三故障 1800 秒时间合同及与 SF-011 的实际差异审核 | 未产生 | `NOT_STARTED` |

SF-003 与 SF-010 已有实质差异：前者是单 Pod、UID 级内存曲线（目标故障窗中位数增加 36,114,432 bytes，正常副本稳定，故障后恢复），候选主要是 Metrics；后者是双事件 Service 暂停与 HTTP 延迟，Recommendation 故障窗 17 次调用中 16 次失败、首页 p95 由约 14.5 升至约 265.9 ms，呈现调用错误与 Trace 延迟传播。二者不只是种子或时刻不同。SF-004 的首页异常也真实存在，但 Cart 正常对照未通过预注册判据；SF-005 的 Service 暂停真实生效，但正常 Image/Cart 各有一次真实漏发。两者都不能算作合格独立题。未执行题目的异常类型、传播范围、模态覆盖及实际难度均为 **UNKNOWN**，不能仅凭草案组合认定九题多样性合格。SF-009 与 SF-010、SF-011 与 SF-012 特别需要 Public/GT 草案相似性复核；若高度相似，应先扩展经资格验证的目标或业务路径，再继续生产。

SF-003 的权威报告为 `/root/ops-competition-2026/reports/SF-003-attempt02-report.md`。ATTEMPT-01 因控制器/采集器启动竞态在故障前终止且证据保留；ATTEMPT-02 完成全部准入。其原始数据 5,844,894,469 bytes，Master 和 Worker3 各有完整副本；最终 Public/Private 包 41,191,853 bytes，同样有两机副本。SF-004 的失败报告为 `/root/ops-competition-2026/reports/SF-004-attempt01-failure-report.md`；它的原始 5,896,657,963 bytes 和输出 41,580,804 bytes 也完成 Master+Worker3 双副本。SF-005 的失败报告为 `/root/ops-competition-2026/reports/SF-005-attempt02-failure-report.md`，原始 5,738,362,310 bytes 同样完成 Master+Worker3 双副本，但未生成 Public/GT。此布局没有在 Worker2 长期保存新题原始归档，实际减轻了 Worker2 容量压力。Master 的隔离 Parquet 运行库约 192 MB 位于 `/root`；数据盘仍维持原 `noexec` 保护，原始采集和导出文件都写 `/data/stage5-v3`。

SF-009 演练期间 Collector 于 07:06:41 UTC 记录 memory limiter 超过软限制并拒收数据，初始 gRPC/HTTP refused 均为 0，停止后分别观察到 662/476；这使后续正式题的观测准入失效，不能因为 Pod 仍 Ready 而继续注入。演练失败报告为 `/root/ops-competition-2026/reports/SF-009-reverse-nofault-blocker.md`。最终五主机残留 `HOST_RESIDUE_PASS`、Master 业务安全 `LIVE_ENVIRONMENT_PASS`；2026-10-16 07:09:32 UTC 数据盘可用空间 Master 212,523,278,336、Worker1 45,481,463,808、Worker2 48,836,321,280、Worker3 50,026,881,024 bytes，相关根盘也均高于 5 GB 下限。没有删除历史原始数据、修改练习题或 SF-010，也没有操作 50/51 比赛服务器或开放正式 API。

最后一次独立检查的机器可读证据为 `/root/ops-competition-2026/reports/SF-remaining-9-v2-final-residue.json`（2026-10-16 07:15:08 UTC，五主机均 PASS）和 `/root/ops-competition-2026/reports/SF-remaining-9-v2-final-storage.json`（07:15:07 UTC，单题空间预测 PASS）；Worker3 没有活动 SF-009 Marker Timer。该健康状态不等于 Collector 数据完整性恢复。

下一步先只读定位 Collector 内存突发的来源、队列与 exporter 背压，再形成可回滚的最小修复版本；修复后需用至少两个新鲜、完整的无故障窗口重新验证零拒收、三模态入库及请求到最终 Span 的身份匹配。正常对照在 SF-005 中因 1～2 秒真实时延填满 Image 2/Cart 4 并发槽，增加并发槽可以作为**下一版**负载工程方案，但必须先独立无故障验证资源和流量，并重新登记未来 Case；不能修改已冻结尝试的零漏发门槛或回填漏发请求。当前余量不是主要阻碍，观测链路和对照稳定性才是。

机器可读总账：`/root/ops-competition-2026/plans/SF-003-to-SF-012-production-v2-ledger.json`。当前仍有 8 题未达到合格状态，其中 SF-004、SF-005 需要先解决正常对照稳定性并独立注册新尝试；SF-009 及其余题目必须等待 Collector 拒收问题经新的完整无故障窗口验证解决。单/双事件新组合须逐题锁定目标和恢复检查，三事件题须先通过独立完整时间合同。不以未执行、准入失败或仅有无故障演练的尝试冒充生产成果。
