# SF-005 / ATTEMPT-02 正式采集失败报告

**终态：`FAILED_CONTROL_COVERAGE`。真实 ServicePause 已执行并恢复，但不形成合格题目；没有 Public 包，也没有异常 GT 草案。**

冻结运行 Manifest SHA-256 `86975e7770451538467c9ad978e89a37ce45abedfbb5d4531179656fe3e7d2a5`。预定连续窗口为 2026-10-16 06:10:49.895746～06:40:49.895746 UTC，MEDIUM 1.4 sessions/s、Image 1/s、Cart 2/s，种子 72016。Recommendation 两个已登记 Worker3 副本于 06:20:50.207538 UTC 真实暂停，启动最坏偏差 0.322 秒，持续 30.000142 秒；06:21:20.214306 UTC 完成两原 PID 恢复及独立 Watchdog 清理。服务恢复预检 PASS，Pod Ready 且无重启。

六个 300 秒分段均 `CHUNK_QUALITY_PASS`，Trace 采用不可变 +5/+60/+120/+240 秒审计。但最终固定频率对照验收失败：Image 计划 1,800、成功 1,799、漏发 1；Cart 计划 3,600、成功 3,599、漏发 1。两次漏发分别计划于 06:28:48.897215 和 06:28:48.397421 UTC，状态 `MISSED_ARRIVAL / SCHEDULE_LATE_OR_STREAM_FULL`。之前 Image 的两个并发请求分别耗时约 2.086/1.021 秒，Cart 的四个并发请求耗时约 1.014～2.090 秒；故障已结束，仍出现一次共同的正常对照延迟/并发槽占满事件。该现象不能重标为成功请求，也不能降低零漏发准入门槛。冻结采集终态 SHA-256 `e0f427d4a94681d1d381ef1d6d82e7258c495bba9cd66653a087a7d946041419`，故障终态 SHA-256 `310c96d69705837a793304173ea291c33b9e387c9dca87b515cf84533dc12fd3`，对照结果 SHA-256 `94fbf239395304f4222428964842f8a93496b39ba3e829ddbd12849e89433dc5`。

原始目录 `/data/stage5-v3/ops-competition-2026/runs/SF-005-ATTEMPT-02/` 永久保留。Master 与 Worker3 第二副本 `/data/rca-archives/ops-competition-2026/SF-005-ATTEMPT-02/raw/` 各有 2,605 文件、5,738,362,310 bytes；源 Manifest SHA-256 `ca6daef56db86dbd246df7cbd7e81baab076aeda9c6b92b946d9a521a405a499`，两端逐文件 SHA 和恢复读回 PASS。最终五主机 `HOST_RESIDUE_PASS`，Worker3 Timer/Service 均不活动；未触碰历史题目或比赛 API。

ATTEMPT-01 另有独立的注入前失败记录，未被覆盖。本次失败揭示的是正常对照业务在无故障恢复段出现的真实短暂延迟，后续先做新鲜无故障稳定性验证，不能靠挑选一次幸运重跑把失败尝试改称 READY。
