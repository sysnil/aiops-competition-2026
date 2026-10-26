# SF-PILOT-01 / ATTEMPT-04 首道半决赛题目试生产报告

最终状态：**SF_PILOT_01_READY_FOR_IMPORT**。实际完成连续 60 分钟、两次真实故障及恢复、三模态导出、Public 泄漏检查和双副本归档。该状态仅表示可进入下一轮服务器导入测试；本轮没有连接或操作 192.168.51.50 / 192.168.51.51，GT 尚待人工批准。

生成时间：2026-10-14T10:40:37.350050+00:00。

## 交付摘要

| 项目 | 实际结果 |
|---|---|
| 窗口 UTC | 2026-10-14T09:12:25.156656+00:00 至 2026-10-14T10:12:25.156656+00:00 |
| 北京时间 | 2026-10-14 17:12:25.156656—18:12:25.156656 |
| 真实故障 | Recommendation 两副本整体暂停 300 秒；Frontend 单 Pod 分配 32 MiB、300 秒 |
| 数据量 | 80,358 Metrics；567,346 Logs；817,529 Spans；119,091 Traces |
| 数据段 | 11/11 通过；60/60 分钟三模态非空 |
| 请求身份 | 40,382/40,382 已解释：40,381 公开窗口匹配 + 1 真实私有边界核验 |
| 普通对照 | Image 3,600/3,600；Cart 7,200/7,200；无失败和错过调度 |
| 故障业务失败 | 140 次 TimeoutError、3 次 HTTP 500；保留全部记录 |
| Collector | 拒收增量 0；峰值 323.309 MiB；最终 11,516 行日志快照无 WARN/ERROR |
| Public | 6 个文件，77,945,722 字节；全量读回、Schema、真实 ID、SHA-256 和泄漏校验通过 |
| GT | 58 条可复查候选，13 个归并事件；DRAFT_PENDING_HUMAN_REVIEW |
| 最终安全 | 五主机环境与残留检查 PASS；blockers = 0；无活动故障任务 |

## 实际执行与恢复

本文件记录实际执行；最终归档与导入就绪结论见外部总报告，避免将尚未完成的归档预先标为 PASS。

连续窗口：2026-10-14T09:12:25.156656+00:00 至 2026-10-14T10:12:25.156656+00:00，3600 秒。公开题号 SF-001。实际控制器状态 `FORMAL_EXECUTION_COMPLETE`。

Run Manifest：`/root/ops-competition-2026/attempt04-20261014/plans/SF-PILOT-01-ATTEMPT-04/run-manifest.json`

SHA-256：`3580846fad4904fc38c0c82448f1e88f5f1def8061992e30937eb923593c1be2`

采用 Master UTC、Worker 本地定时器、显式运行登记、15 秒心跳/30 秒新鲜度、4 秒短期租约、原 2 秒启动容差与原已验证适配器。未重跑 Marker 或资格故障；正式新入口执行前 56 项非注入回归测试通过。

| 机制 | 实际开始 UTC | 实际结束 UTC | 持续秒 | 原始偏差秒 | 含不确定度秒 | 反馈上界秒 | 清理 |
|---|---|---|---|---|---|---|---|
| SERVICE | 2026-10-14T09:24:25.430130+00:00 | 2026-10-14T09:29:25.444369+00:00 | 300.000 | 0.273 | 0.311 | 0.478 | COMPETITION_CLEANUP_ACTIONS_VERIFIED |
| POD | 2026-10-14T09:47:25.498016+00:00 | 2026-10-14T09:52:25.608243+00:00 | 300.000 | 0.341 | 0.382 | 0.198 | COMPETITION_CLEANUP_ACTIONS_VERIFIED |

Service 实际恢复原 PID 3416103、3415737；Pod 清理自有 holder PID 3046695。Pod 参数为分配 32 MiB，容器总内存变化同时包含 helper 开销，不能把总增量误称为精确 32 MiB。两次公开恢复和事后五主机预检均实际通过。

正常负载：MEDIUM 1.4 sessions/s，seed 1014404，Image 1/s，Cart 2/s，三个阶段策略不变。请求 29582 + 对照 10800。终态分类：`{"None": 4590, "MAX_TRANSITIONS": 27, "TimeoutError": 140, "MISSED_ARRIVAL": 280, "HTTPError": 3}`。280 个 MISSED_ARRIVAL 是未发出的业务会话，不能计算为缺失 Span；27 个 MAX_TRANSITIONS 单独保留，不擅自归因于注入。

没有启动 ATTEMPT-05、Node/NetworkPath 额外故障；未修改 Collector 配置；未访问比赛服务器。安全审计工具记录的 faults_injected=0 表示该只读工具自身不注入，不代表本次总故障数；本次总数以两条真实注入记录为准。

Service 公开证据：140 个故障期错误请求有对应错误 Trace 证据；关联调用方 Span p95 从 8.696 ms 变为 289617.951 ms，样本数 1370/230。这是内部调用 Span 时长，不是用户成功 HTTP 请求 p95；客户端超时另计。

## 采集质量、已知缺失与证据

实际判定：`CASE_CAPTURE_AND_EFFECTS_PASS`。11/11 分段通过 +5/+60/+120/+240 秒不可变完成审计。

| 模态 | 行数 |
|---|---|
| metrics | 80358 |
| logs | 567346 |
| traces | 817529 |

真实 Trace 数 119091；指标序列 345。60 个分钟均有 Metrics、Logs、Traces；明细见 minute-coverage.csv。

| 请求来源 | 应核对 | 公开窗口匹配 | 真实边界解释 | 成功请求 |
|---|---|---|---|---|
| business | 29582 | 29581 | 1 | 29439 |
| image_asset | 3600 | 3600 | 0 | 3600 |
| cart_view | 7200 | 7200 | 0 | 7200 |

派发记录 40382，未完成派发 0；无法解释的缺失 0。失败分类：`{"business:TimeoutError:None": 140, "business:HTTPError:500": 3}`。边界 1 个请求使用真实 Trace/Span 身份核验，未改写时间戳，也未将窗口外 Span 填入 Public。

Collector 峰值 323.309 MiB（既有上限 380 MiB），无 receiver refused 增量，最终日志快照未发现 WARN/ERROR。缺失的可选 exporter failure/retry 指标为 UNKNOWN，不解释为零。Collector UID/配置未改变，配置 SHA-256 `128ed286e8a959aacddc2ee6c00e2d6e8b0a3eb18ea43e5de730a20e3793af2d`。原始计数及资源曲线数据见 execution-accounting.json、collector-resource-series.json。

数据限制：Service 延迟主要由真实 Trace duration 提供；Prometheus 调用计数保留原始 counter，不虚构 histogram。某些可选指标为空、部分状态/身份属性为 NULL，完整缺失统计见 public-readback-verification.json。异常超时与观测缺失分开核对。

允许的原始缺失明确保留：日志级别 NULL 431,416 行；Span 显式状态 NULL 806,492 行；Pod UID 未提供 439,324 个 Span；可用 Pod UID 378,205 个 Span 对应 11 个真实 UID，均能在拓扑中找到。52,962 条指标记录没有后端提供的类型元数据，单位元数据为空；不按名称猜测填充。父 Span 不在窗口或未被客户端记录的关系共 41,708 条，保留原引用与未解析状态。上述字段缺失不等同于已发请求丢失，未声称全量身份属性覆盖。

请求总数包含真实失败。280 个 MISSED_ARRIVAL 为固定并发条件下未发出的会话；27 个 MAX_TRANSITIONS 为状态机终态，不自动归因于故障。全部派发有终态，无静默删除或伪造 Span。+120/+240 审计保留所有原始版本，导出不重复累加同一 Span。

## Public 导出与 GT 草案

实际执行：Parquet 读回、Schema、纳秒 UTC 类型、半开时间窗口、真实 ID、Trace Index、文件 SHA-256 和逐文件/逐行泄漏检查通过。服务器导入测试未执行。

| 文件 | 字节 | SHA-256 |
|---|---|---|
| logs.parquet | 20787419 | 9ec942570e905d120769000ad8cb4f8a74254150209a18468f6cba2172cdc8f2 |
| metrics.parquet | 428270 | f23e4f418381b153c6b863444f9a39a88a89033fe9097e846b004dfd4f2cad3f |
| topology.json | 230380 | 0da34eb05e9b0ff535c4d661bf5bb21362e4c77e1993eeebd89ba583578fcf98 |
| trace_index.parquet | 3860927 | acfe812df2f1368e35c67a6e58cd68b15e26c46507e5e578dfc40e29546fa249 |
| traces.parquet | 52632712 | a35452a8ef7dca2db3914c1a840131f1acb202fb74135f3c9511fc2dbb4e1c96 |

Public manifest SHA-256：`8ad8c5eb25e48a33ebf776a40db0f3fbe62284ee7c26df3e4d04139532db0eaa`。

原始查询边界/重复统计：`{"excluded_metric_query_boundary_rounding": 3677}`。仅过滤原始时间戳实际位于 `[start,end)` 之外的 Prometheus 查询端点；原始响应永久保留，未平移时间。

Public 仅含六个指定文件；计划、故障记录、调度 Manifest、GT、评分标签和分析报告均留在独立私有目录。源缺失保留 NULL，日志等级不按关键词伪造。Public-only 分析和读回检查无 GT 输入。

状态：`DRAFT_PENDING_HUMAN_REVIEW`，不是正式评分答案。

基于 Public 三模态生成 58 条候选，按相同实体及重叠/相邻观测区间归并为 13 个事件。包括 Service、Pod 与实际 client/server 身份相连的调用边症状。

候选检测不读取注入计划或注入记录，不把两次注入直接变成两个 GT。时间范围来自实际异常样本；注入计划只供后续人工私有审核。保留每条候选的 Public 文件、筛选条件、Trace/Span ID 和证据。阈值是探索性候选检测规则，不是重新修改采集准入；正常波动、传播现象与多模态重复仍待人工复核。

公开内存证据：目标中位增量 33.727 MiB，正常副本增量 -0.066 MiB，连续异常样本 18，恢复通过。Service 公开影响明细保留在 capture-and-effects-assessment.json。

旧无故障参考仅用于辅助比较，保留不同 seed、时间及进程 uptime 的可比性限制。未用旧故障数据拼接本次题目。

每条最终候选均有 Public 支持证据，调用边引用通过真实 Trace ID、client/server Span ID 和 CHILD_OF 关系核验；原始探索性候选、调用边补充版本、引用核验与源代码均保留。归并事件并非“两次注入对应两个 GT”，也未自动认定所有事件由注入造成。

## 存储峰值预算与实际使用

| 位置 | 预注册增量预算 | 实际/最终状态 |
|---|---|---|
| Master 数据盘 | 36507222016 B | 最终完整 case 11,375,090,023 B；运行控制记录 217,855,880 B |
| Master 系统盘 | 64 MiB（含代码、日志及余量） | 最终剩余 46,079,840,256 B，未降低 46,000,000,000 B 下限 |
| Worker2 数据盘 | 31 GiB，单份归档直接导出 | 最终剩余 75,845,939,200 B；导出临时峰值 297,198,920 B |
| Worker3 数据盘 | 29 GiB | 最终剩余 110,148,313,088 B |
| 采集硬限制 | JSON 24 GiB；原始+控制 26 GiB | 实际 JSON reservation 11,170,986,722 B；未触及上限 |
| 导出硬限制 | 临时 2 GiB、产物 2 GiB | 实际产物 77,945,722 B；容量门禁 PASS |

系统盘与数据盘净变化含背景写入，不全部归因于本实验。未删除历史记录、科研冻结资产或受保护归档；未复制整份原始数据到 Mac。Master 数据盘最终剩余 270,697,103,360 B。Worker 的 50 GB 安全余量要求保持不变。

## 双副本、清单及最终五主机状态

| Worker | 归档内容 | 文件数 | 字节 | 校验 |
|---|---|---|---|---|
| w2 | raw-final | 5024 | 11375090023 | SHA + 恢复读取 PASS |
| w2 | public-final | 6 | 77945722 | SHA + 恢复读取 PASS |
| w2 | private-final | 45 | 4743721 | SHA + 恢复读取 PASS |
| w2 | finalization | 4 | 21632 | SHA + 恢复读取 PASS |
| w3 | raw-final | 5024 | 11375090023 | SHA + 恢复读取 PASS |
| w3 | public-final | 6 | 77945722 | SHA + 恢复读取 PASS |
| w3 | private-final | 45 | 4743721 | SHA + 恢复读取 PASS |
| w3 | finalization | 4 | 21632 | SHA + 恢复读取 PASS |

原始 4,999 个文件逐一确认未改写；最终完整包增加 Public/Private/报告后共 5,024 个文件。Public 与 Private 独立目录和独立清单分别验证。

| 主机 | 可用内存字节 | 系统盘剩余字节 | 数据盘剩余字节 |
|---|---|---|---|
| master | 12061777920 | 46079840256 | 270697103360 |
| w1 | 28639178752 | 70190399488 | 60098813952 |
| w2 | 29187866624 | 69127458816 | 75845939200 |
| w3 | 31023030272 | 62128386048 | 110148313088 |
| router | 7630045184 | 87402844160 | 无独立数据盘 |

最终新鲜健康证据：`/data/stage5-v3/ops-competition-2026/audits/COMP-SF01-A04-FINAL-DELIVERY/phase-a-decision.json`，环境 blockers = 0；残留证据：`/data/stage5-v3/ops-competition-2026/audits/COMP-SF01-A04-FINAL-DELIVERY/residue.json`，状态 `HOST_RESIDUE_PASS`。安全审计原状态 `COMPETITION_ENVIRONMENT_PASS_WATCHDOG_PENDING` 的 Watchdog 后缀表示不授予未来新故障许可；本次两套实际 Watchdog、清理及撤销均有独立执行证据，结束后不应保持故障许可。

| 清单 | SHA-256 |
|---|---|
| run_manifest_sha256 | 3580846fad4904fc38c0c82448f1e88f5f1def8061992e30937eb923593c1be2 |
| public_manifest_sha256 | 8ad8c5eb25e48a33ebf776a40db0f3fbe62284ee7c26df3e4d04139532db0eaa |
| raw_final_manifest_sha256 | 76a4e3083df6e8eb15aef360647b8a910ef4d071730412c076360efb1d7bb7c0 |
| private_final_manifest_sha256 | 67d00e5e6653dd8bdbb58f44ee617ec9c12512472ec3d115e0d5ca5dc2d94472 |
| fresh_health_sha256 | dc01bd3fc6270991ca732643c13ccef7dd90a30da46d3ca2cc5a597ff2597b6f |

## 产物与未执行事项

| 产物 | 路径 |
|---|---|
| 公开题目 | /data/stage5-v3/ops-competition-2026/runs/SF-PILOT-01-ATTEMPT-04/public |
| 私有计划、记录、异常候选与 GT 草案 | /data/stage5-v3/ops-competition-2026/runs/SF-PILOT-01-ATTEMPT-04/private |
| 执行/质量/异常/导出报告 | /data/stage5-v3/ops-competition-2026/runs/SF-PILOT-01-ATTEMPT-04/reports |
| 运行计划与代码 | /root/ops-competition-2026/attempt04-20261014 |
| 最终归档索引及决策 | /data/stage5-v3/ops-competition-archive-index/SF-PILOT-01-ATTEMPT-04 |
| Worker2 / Worker3 归档 | /data/rca-archives/ops-competition-2026/SF-PILOT-01-ATTEMPT-04/ |
| 本总报告 | /root/ops-competition-2026/reports/SF-PILOT-01-attempt04-report.md |

本轮实际完成一次 ATTEMPT-04，未重试 ATTEMPT-05，未改写 ATTEMPT-01/02/03，未修改科研冻结协议/数据/GT/预测，未访问 TEST。没有执行新的 Marker Dry-run、Node/NetworkPath 故障或 Collector 参数搜索。服务器导入与正式评分 GT 审批均未执行，等待人工审核。
