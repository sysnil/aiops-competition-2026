# SF-007 ATTEMPT-08 正式生产报告

**状态：`READY_FOR_IMPORT`，GT 仍为 `DRAFT_PENDING_HUMAN_REVIEW`。** ATTEMPT-01～07 的失败证据与原始归档保留；本次使用独立新代码、计划、预算和 Worker3 Watchdog，并将身份刷新、五主机心跳、采集器真实健康握手与控制器启动串成一个受限入口。身份年龄 76 秒时通过无故障采集前检，采集器初始健康 SHA-256 `22b90f9f78b803e89eec5019fb046ae73db630f6a371601c5b15ec58cf0a2260`，之后才启动故障控制器。

| 项目 | 实际结果 |
|---|---|
| 采集时间窗 | `2026-10-18T10:27:45.435689Z`～`10:57:45.435689Z`，连续 1,800 秒 |
| 真实故障 | Worker3 上两个 Recommendation 原进程同时暂停 30.000132 秒；10:37:45.712567Z 启动，最坏偏差 0.279 秒 |
| 清理与恢复 | 独立 Watchdog/适配器清理、原 PID/start-ticks、`SERVICE-RECOVERY` 与 `POST` PASS；目标流恢复段 60/60 请求成功 |
| 目标业务覆盖 | 暂停期 Recommendation 固定流 30/30 请求发出、29 次失败；Image/Cart/Home 各 30/30 成功，未发生目标流漏发 |
| 采集 | 6/6 分段质量 PASS，15,157 条业务请求完成；Collector gRPC/HTTP refused 均为 0 |
| Public | Metrics 40,752；Logs 190,786；Spans 339,854；Trace Index 51,255；三模态 30/30 分钟，逐行读回、SHA、Pod UID/Trace Index 和泄漏扫描 PASS |
| Public Manifest | SHA-256 `b5b3291da7ebc0f8b54f2098ffb3193f763c657f99c2843552c23562a5b7493d` |
| Service Admission | 所有冻结检查 PASS；两副本故障前/后普通业务覆盖、公开直接服务信号、正常对照与恢复均满足合同 |
| Private GT 草案 | Public-only 异常分析得到 23 个候选、归并 4 个事件；未获人工批准，不可评分 |
| 双机归档 | 原始 3,681 文件/4,449,728,896 B；Public/Private 输出 11 文件/31,017,028 B，Master+Worker1 SHA 和恢复读回 PASS |
| 归档后安全 | 五主机残留、竞赛单元与单题容量预测 PASS；Master 数据盘约 67.66 GB、Worker1 数据盘约 15.01 GB 可用，均高于十进制 5 GB |

冻结 Public 中两个 Recommendation Pod 的服务端 Trace 数分别从故障前 293/333 降至故障期各 1 条，并在恢复段达到 198/186 条。故障期公开调用 Trace 为 54 条，其中 24 条失败，服务端归一化完成率约 0.032。Image before/during/after 的 p95 为 1.648/1.894/1.713 ms；Cart 为 20.625/18.919/16.519 ms，各窗口样本数满足要求，正常对照时延判据均 PASS。直接服务根因证据依据 Public Traces，未将私有暂停命令导出为选手证据。

Master 原始路径为 `/data/stage5-v3/ops-competition-2026/runs/SF-007-ATTEMPT-08/`，可导入的六文件 Public 路径为 `/data/stage5-v3/ops-competition-2026/packages/SF-007-ATTEMPT-08/public/`；Private GT 草案仅在同级 `private/`。Worker1 校验副本位于 `/data/rca-archives/ops-competition-2026/SF-007-ATTEMPT-08/`。官方总账 `/root/ops-competition-2026/plans/SF-003-to-SF-012-production-v2-ledger.json` 已记录 9/10 READY，SHA-256 `433f6703c21f6ef6e3097343aec1300d89c7511bf65bd8d134d613a1a9341b44`。本轮未向 51 服务器导入或发布。
