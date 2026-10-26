# SF-011 ATTEMPT-05 正式生产报告

**状态：`READY_FOR_IMPORT`，GT 仍为 `DRAFT_PENDING_HUMAN_REVIEW`。** ATTEMPT-01/02 的双故障 Admission 失败、ATTEMPT-03 的 Image 正常对照失败、ATTEMPT-04 的 Worker1 容量预测安全中止均永久保留；没有回填或重评。为本次独立 Attempt，Worker1 两份 Master-backed READY 归档先完成三轮 SHA/读回盘位迁移，随后 1,800 秒无故障五主机资格 121/121 PASS。32 个代码/合同/校准文件于正式实验前冻结，Manifest SHA-256 `52275e29fcea43bfb84158033b59c341c48a08a7d2a31bf2098e14a35731a273`。

| 项目 | 实际结果 |
|---|---|
| 采集时间窗 | `2026-10-18T13:10:57.472933Z`～`13:40:57.472933Z`，连续 1,800 秒 |
| 真实故障 | 普通业务首页 HTTP ReqDelay 250 ms/60 秒；状态机 42、固定首页流 60，共 102 条请求受作用 |
| 撤销与恢复 | `HTTP-PRE`、`HTTP-RECOVERY` PASS，无 Worker 故障 Timer 或持久规则 |
| 负载覆盖 | Image/Cart/Home/Recommendation 在故障窗口各 60/60 固定流请求成功；Before/During/After 策略一致 |
| 采集 | 6/6 分段质量 PASS，15,165 条业务请求完成；Collector gRPC/HTTP refused 增量 0 |
| Public | Metrics 40,792；Logs 192,786；Spans 349,397；Trace Index 53,230；三模态 30/30 分钟、逐行读回、SHA、Pod UID/Trace Index 和泄漏扫描 PASS |
| Public Manifest | SHA-256 `025b1a41fc56c17e5b0dd9e36731260eaa5268696b5f0e3b8d36b0a9af12d47e` |
| HTTP Admission | 9/9 项 PASS；首页直接时延、恢复、Image/Cart 样本和时延对照均满足独立冻结合同 |
| Private GT 草案 | Public-only 异常分析得到 2 个候选、归并 2 个事件；待人工审核，不可评分 |
| 双机归档 | 原始 3,780 文件/4,576,681,984 B；Public/Private 输出 11 文件/31,753,414 B，Master+Worker3 SHA 和读回 PASS |
| 归档后安全 | 四 Node Ready、五主机残留/竞赛单元 PASS、Collector refused 0、各文件系统高于十进制 5 GB |

冻结 Public 中首页 Proxy Server Span 的 before/during/after p95 为 15.043/265.071/14.833 ms，样本数 204/102/204。Image 为 1.272/1.305/1.423 ms、样本数 120/60/120，满足预先来自独立 VAL5/VAL6 无故障校准的 `max(1.25 × before, before + 2 ms)`；Cart 为 17.757/11.954/17.063 ms、样本数 120/60/120，严格满足原 1.25 倍上限。旧 ATTEMPT-03 仍按其原合同失败，新规则没有用于追认历史数据。

Master 原始路径为 `/data/stage5-v3/ops-competition-2026/runs/SF-011-ATTEMPT-05/`，可导入的六文件 Public 路径为 `/data/stage5-v3/ops-competition-2026/packages/SF-011-ATTEMPT-05/public/`；Private 仅在同级 `private/`。Worker3 校验副本位于 `/data/rca-archives/ops-competition-2026/SF-011-ATTEMPT-05/`。官方总账已达到 10/10 READY，SHA-256 `813035c9c5e69741c73de7d0afb92e427d8745714992ab74c0c41c414f7e69f8`；Master 十题 Public 交接索引 SHA-256 `8b8d9ed83779370318c94b40a9be0a401b3d38705d35cb586f26d6963164d401`。本轮未访问或发布到 51。

**人工审核重点：** GT 草案除 `frontend-proxy` 的 Trace 延迟候选外，还检测到一个 Pod 内存增加候选。后者可能是背景波动，必须回查 Public 指标与时间区间，不能因为正式采集已 READY 就自动批准 GT。SF-011 与 SF-004 都使用首页 HTTP 延迟机制，题目差异不足以宣称新故障类别；题库多样性统计应披露。
