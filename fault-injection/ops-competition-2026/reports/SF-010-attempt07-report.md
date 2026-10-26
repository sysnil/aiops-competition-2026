# SF-010 / ATTEMPT-07 正式生产执行报告

**状态：READY（可进入数据导入测试；Private GT 仍需人工审核）。**

本次在 Master 上连续采集 2026-10-15 16:37:43.727538 至 17:07:43.727538 UTC，共 1800 秒。使用预注册 MEDIUM 1.4 sessions/s、Image 1/s、Cart 2/s 和种子 71010。没有修改 SF-001/002 或科研冻结资产，也没有访问比赛服务器。

## 实际故障与业务影响

| 事件 | 计划 | 实测及恢复 | 普通业务证据 |
|---|---|---|---|
| ServicePause / Recommendation 两副本 | T+600，30 秒 | Worker3 于 16:47:43.983148 UTC 开始；相对计划偏差 0.256 秒，最坏时钟界 0.259 秒；持续 30.000 秒；16:48:13.999 UTC 完成撤销，原两个 PID 恢复，SERVICE-RECOVERY 预检 PASS | Recommendation 调用前窗 175/175 成功、p95 29.933 ms；故障窗 17 次中 16 次失败、p95 8010.947 ms；恢复窗 171/171 成功、p95 34.937 ms |
| HTTP ReqDelay / 首页 | T+960，60 秒，250 ms | REQDELAY-PRE PASS；窗口内 84 条真实首页请求携带延迟头，窗口外没有标记，短租约结束后不再施加 | 首页前窗 168/168 成功、p95 14.475 ms；故障窗 84/84 成功、p95 265.863 ms；后窗 168/168 成功、p95 15.793 ms |

实际 Service 启动与恢复见 `control/service-onset.json`、`control/service-worker-runtime/terminal.json`、`control/fault-controller-terminal.json`；业务统计见 `/root/ops-competition-2026/sf010-finalization-20261016/attempt07-business-effects.json`（SHA-256 `c350773539dac92b5d309a394ce9a289c0d400e95a86594480ca17a1a455f58f`）。

## 采集与质量

- 六个 300 秒分段全部 `CHUNK_QUALITY_PASS`，Trace 来源固定为 `JAEGER_OPENSEARCH_STORAGE`，保留 +5/+60/+120/+240 秒不可变版本；捕获终态为 `SF010_FORMAL_CAPTURE_PASS_PENDING_EXPORT`。
- 21,154 条完成请求中，21,152 条在公开窗口内匹配真实 Proxy Span，另 2 条具有独立边界证据；没有无解释的身份缺口。
- 140 次安全样本；Collector 工作集峰值 344,313,856 bytes，gRPC/HTTP receiver refused 均为 0。最终 Collector 及集群检查 `LIVE_ENVIRONMENT_PASS`，五主机残留 `HOST_RESIDUE_PASS`。
- Public：41,581 条 Metrics、295,980 条 Logs、432,558 个 Spans、62,868 条 Trace Index；30 个自然分钟每种模态均非空。独立全量读回、Parquet、Trace/Span 去重、Pod UID、半开时间窗、逐文件 SHA-256 和泄漏检查均 PASS。
- Public Manifest SHA-256：`6598702cfb9edd0cd5476de44e46c5c75191f12583c67c34c86e75a6e8d47a96`；Master 六文件合计 41,134,699 bytes。Public 只保留实际可见业务与遥测；显式 Envoy 注入控制标记已从公开日志/Span 属性中删除，真实时延与状态仍保留。

## 异常草案与归档

Public-only 分析得到 20 个异常候选、归并为 5 个事件，包括 Service 暂停期间的 Frontend/Recommendation 错误与时延、以及 HTTP 延迟期间的 Frontend Proxy 时延。Private 草案状态为 `DRAFT_PENDING_HUMAN_REVIEW`，不等于正式评分答案；草案 SHA-256 `e5a66b4bb7349b92327a5741ff6ea590fad82cea107a3bfc9f53e76053bceaa2`。注入计划与真实执行记录单独保存于 Private，仅供审核。

- 原始目录：`/data/stage5-v3/ops-competition-2026/runs/SF-010-ATTEMPT-07/`。
- Master Public/Private：`/data/stage5-v3/ops-competition-2026/exports/SF-010-ATTEMPT-07/{public,private}/`。
- Worker2/Worker3 归档：`/data/rca-archives/ops-competition-2026/SF-010-ATTEMPT-07/{raw,outputs}/`。
- 原始归档：2,659 文件、5,834,704,872 bytes，双副本 SHA 与 Metrics/Logs/Traces 样本读回 PASS；源 Manifest SHA-256 `eeeba6f1dcad8026c3aeadaed1b8feeef997b2231a762ca8a2bac2f285232079`。
- 最终输出包：11 文件、41,313,713 bytes，双副本 SHA PASS；源 Manifest SHA-256 `7b43689fd3d65893b6de460aac091e39a746068b778be438a4379e5ac892a69c`。Worker2 完成 Public Parquet 全量读回；Worker3 完成 Parquet 首尾标志和 Manifest 读回。
- 一次输出归档曾误用原始 Manifest 文件名。Worker2 原始 Manifest 已由 Master 权威副本恢复，并再次完成 2,659 文件哈希与三模态读回；最终两个 Manifest 分别存放，原始记录保留。

最终可用空间（bytes）：Master 根盘 45,911,678,976 / 数据盘 233,558,507,520；Worker1 根盘 69,992,501,248 / 数据盘 50,704,625,664；Worker2 根盘 68,956,803,072 / 数据盘 49,794,150,400；Worker3 根盘 61,976,215,552 / 数据盘 73,534,246,912；VM1 根盘 87,396,855,808。均高于授权的十进制 5 GB 保护线。

ATTEMPT-01～06 各自保留失败证据，均未在正式故障前通过完整准入；本报告只将 ATTEMPT-07 记为 SF-010 的成功生产。未在比赛数据服务器执行导入或 API 测试，Private GT 尚未获人工批准。
