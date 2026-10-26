# SF-012 ATTEMPT-04 正式生产报告

**状态：`READY_FOR_IMPORT`，GT 仍为 `DRAFT_PENDING_HUMAN_REVIEW`。** SF-012 ATTEMPT-02 因冻结导出器边界检查失败、ATTEMPT-03 因采集启动时身份快照过期，均保留失败终态和原始证据；本次使用独立新代码、计划、Manifest 和 Worker2 Watchdog。没有追改前两次尝试。

| 项目 | 实际结果 |
|---|---|
| 采集时间窗 | `2026-10-18T09:19:13.286218Z`～`09:49:13.286218Z`，连续 1,800 秒 |
| 真实故障 | Worker2 上单个 Frontend Pod，32 MiB 内存压力 300.000132 秒；09:29:13.565919Z 启动，最坏偏差 0.282 秒 |
| 清理与恢复 | 独立 Watchdog/适配器清理 PASS，`POD-RECOVERY` 与 `POST` PASS；Pod UID、容器和 Node 未变，OOM/OOM-kill 0 |
| 采集 | 6/6 分段质量 PASS，15,167 条业务请求完成；Collector gRPC/HTTP refused 均为 0 |
| Public | Metrics 40,560；Logs 190,092；Spans 351,032；Trace Index 53,722；三模态 30/30 分钟，逐行读回、SHA、Pod UID/Trace Index 和泄漏扫描 PASS |
| Public Manifest | SHA-256 `7e7306cf61205d91f5e9c34dea33bc86fc077e0c3bb25e1bce15b684be6bbd12` |
| Pod Admission | 10/10 项 PASS；目标与正常副本的 Metrics、普通业务 Trace、UID 连续性和恢复证据满足冻结合同 |
| Private GT 草案 | 1 个 Public-only 候选：目标 Pod 的 `MEMORY_WORKING_SET_INCREASE`；人工审核未完成，不可评分 |
| 双机归档 | 原始 3,803 文件/4,592,247,337 B；Public/Private 输出 11 文件/31,845,178 B，Master+Worker1 SHA 和恢复读回 PASS |
| 归档后安全 | 五主机残留、竞赛单元与单题容量预测 PASS；Master 数据盘约 72.26 GB、Worker1 数据盘约 19.55 GB 可用，均高于十进制 5 GB |

冻结 Public 中目标 Pod UID `d4bb49b4-6fb8-4a07-aaf6-e2caff5df974` 的 before/during/after 内存中位数分别为 134,639,616 / 171,167,744 / 134,635,520 B，各 20 个样本，连续 20 个故障期样本超过预注册的 16,777,216 B 增幅阈值。正常副本 UID `6305d17a-27f0-4dbf-8fc7-7238acb3be91` 分别为 143,118,336 / 143,155,200 / 143,155,200 B；目标普通业务 Trace 样本为 1,044/1,007/1,060，正常副本为 1,144/1,176/1,088。直接证据由 Public Metrics/Traces 核验，私有注入记录没有进入 Public。

Master 原始路径为 `/data/stage5-v3/ops-competition-2026/runs/SF-012-ATTEMPT-04/`，可导入的六文件 Public 路径为 `/data/stage5-v3/ops-competition-2026/packages/SF-012-ATTEMPT-04/public/`；Private GT 草案仅在同级 `private/`。Worker1 校验副本位于 `/data/rca-archives/ops-competition-2026/SF-012-ATTEMPT-04/`。官方总账 `/root/ops-competition-2026/plans/SF-003-to-SF-012-production-v2-ledger.json` 已记录 8/10 READY，SHA-256 `82984ea1361fe3c775e0070096f46e07a6956ee75dde01ccd95e8f144cf62bf3`。本轮未向 51 服务器导入或发布。

**题目差异限制：** SF-012 与已 READY 的 SF-008 均使用 Frontend 单 Pod 内存压力，候选 GT 也只有一个同类内存异常。两题的不同时间和随机种子不能解释为新的故障机制或更高难度。题库多样性分析须单独披露这一重复。
