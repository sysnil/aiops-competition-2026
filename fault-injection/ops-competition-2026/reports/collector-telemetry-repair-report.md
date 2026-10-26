# Collector 数据拒收与 Trace 完整性专项修复

**结论：`COLLECTOR_PATH_READY_FOR_PILOT`。**

本轮只做采集链路诊断、一个 Collector 配置候选及无故障验证；没有注入 Service/Pod/Node/NetworkPath 故障，没有生成正式题目，也没有访问 192.168.51.50 或 192.168.51.51。

## 1. 实际链路与诊断结论

```mermaid
flowchart LR
  W[状态机及 Image/Cart 请求 ID] --> P[Frontend Proxy / Envoy]
  P --> A[Proxy access log 与 Trace context]
  P --> R[Collector OTLP Receiver]
  R --> M[memory_limiter / 共享进程内存]
  M --> B[资源处理与 batch]
  B --> F[压缩 file exporter]
  B --> J[持久化队列 / Jaeger OTLP]
  J --> O[Jaeger OpenSearch storage]
  O --> Q[精确时间窗及 Trace/Span ID 关联]
```

实际 Collector 为 0.156.0，容器限制 400 MiB，GOMEMLIMIT 320 MiB。原 limiter 为 80% hard / 25% spike，soft 约 220 MiB。近期观察到堆内存峰值约 261 MiB、working set 约 310 MiB；Trace 发送队列峰值约 100 KB，远未达到 8 GiB 字节队列容量。内存峰值与拒收时间相邻，但这不等于逐 Span 丢失因果证明。

Metrics、Logs、Traces 共享 gateway 进程与 memory_limiter；自身日志/指标也经 OTLP 回送。batch 未显式指定批大小；发送队列为持久化 file_storage、4 个 consumer，重试 enabled，max_elapsed_time=0。未发现网关 Trace pipeline 的 sampling/drop filter。未改变 gRPC message-size 配置；没有据此假定运行默认值无限大。

缺失的 exporter failed/retry 与 limiter 自遥测字段始终标 UNKNOWN，不补为零。队列未持续堆积和 OpenSearch green 不能单独证明每条 Span 均写入。

## 2. 对历史证据的纠正与逐请求核验

原定时采集器使用正确索引；后来单独执行的事后查询误用了 `jaeger-span-*`，选中零 shard。该旧查询不能作为不存在证据，原文件保留不覆盖。

新诊断又发现，仅用 Trace ID 跨时段查询会命中后续实验：旧资格实验的种子加控制流偏移后发生交叉重用。无时间约束查到的 47 条 Proxy Span 来自后续 Pod 实验，不是原 Service 窗口的迟到 Span。

最终改用真实索引 `jaeger-jaeger-span-2026-10-13`、正 shard 数检查和原始实验精确窗口：93 条请求中 Proxy Span 匹配 0 条；同窗口 Proxy access log 匹配 14 条。access log 证明到达与上下文关联，不证明 Span 已完成并成功导出。

已关闭 Collector 文件导出分卷扫描：80/93 条请求存在其他服务 Span，Proxy Server Span 为 0/93。源分卷 SHA-256：`bcb8a3d162c7e8c66ce231b84bf6ed66e9e9fee428df3bd0e2534722c0e0286f`。这个检查范围是该已关闭分卷，不宣称拥有所有历史 ACK/重试记录。

因此可以排除“仅是当前查询前缀错误”对原归档缺失的解释，但不能证明 memory_limiter 是全部缺失的唯一环节。缺少逐 ID 的 Receiver/Exporter 确认信息，Proxy 生成完成、发送失败及 Collector 前段拒收之间仍有不可辨识部分。

## 3. 最小修改、备份与回滚

| 字段 | 修改前 | 本次候选 |
|---|---|---|
| memory_limiter.spike_limit_percentage | 25 | 15 |
| 对应 soft limit | 约 220 MiB | 约 260 MiB |
| hard limit / 容器限制 / GOMEMLIMIT | 320 / 400 / 320 MiB | 均不变 |
| working set 停止线 | 380 MiB | 不变 |
| batch / queue / retry / sampling / 业务逻辑 | 原配置 | 不变 |

该选择针对观察到的堆内存与 soft limit 不匹配，并保留明确内存上限和节点余量。只调整这一类变量；未扩大节点资源或修改存储架构，未关闭 limiter、batch 或队列保护。

原 relay SHA-256：`b14e2ce22e701e627d792bafc559036e59ec0f054fe020ddb229fbfb9f197f97`。
候选 relay SHA-256：`128ed286e8a959aacddc2ee6c00e2d6e8b0a3eb18ea43e5de730a20e3793af2d`。

备份：`/data/stage5-v3/ops-competition-2026/collector-telemetry-repair-20261013/prechange-backup/`；变更计划：`/root/ops-competition-2026/collector-telemetry-repair-20261013/plans/change-plan.json`。

配置写入检查 ConfigMap/Deployment UID、resourceVersion 与预期原文，防止覆盖未知变更。独立 systemd 90 分钟回滚计时器在修改前激活；原状态的 systemd Kubernetes 访问/核验通过，不将这个 no-op 自检夸称本轮真实候选回滚。

实际保留候选：`True`。只有两个独立窗口与最终健康通过才允许取消计时器；失败则恢复原配置。

手动回滚命令（受原配置身份校验保护）：`python3 -B /root/ops-competition-2026/collector-telemetry-repair-20261013/scripts/change.py rollback --output /root/ops-competition-2026/collector-telemetry-repair-20261013/results/manual-rollback.json`。

## 4. 事前固定的验证方案

MEDIUM 1.4 sessions/s，保持原业务工作流配比；Image 1/s、Cart 2/s，不降频。有效原配置基线 5 分钟，候选两轮各 15 分钟。新种子命名空间分离；每个窗口另有固定 30 秒收尾观测，覆盖最后启动业务会话的请求，不在结果出来后延长窗口。

每 5 分钟左右分段采集三模态；+5、+60、+120、+240 秒分别保留不可变版本，完成上限固定为 +60、+120、+180、+300 秒。核心请求全量匹配；任意无法解释的缺失均不通过。真实 source timestamp 验证不使用查询时刻冒充新鲜度。

执行器 v1 因索引选择问题未启动负载；v2 因控制流输入缺少 duration 字段中止，可能已有少量状态机请求，不能算完整窗口。原记录及代码均保存。v3 修正接口、使用新的输出命名空间和基线种子，数值判据与候选配置未放宽。

Collector 滚动更换后，旧/新 `host_name` 标签在 Prometheus lookback 内暂时共存为八条节点序列。没有改“四节点、源数据小于 60 秒”的安全检查；等待旧序列过期并连续三次健康通过后才开始验证。这不是把整段时间声称为节点完全没有新指标。

验证计划 SHA-256：`bece01326ed9070552874bd8dda656fabb37297e6c8e5053ab288cf32ea0115b`。

## 5. 实际独立窗口结果

| 窗口 | Seed | 请求流 | 已发/记录请求 | 最终 Proxy 匹配 | 缺失 | 结果 |
|---|---:|---|---:|---:|---:|---|
| BASE | 930200 | business | 2668 | 2520 | 148 | NO_FAULT_VALIDATION_FAIL |
| BASE | 930200 | image_asset | 300 | 287 | 13 | NO_FAULT_VALIDATION_FAIL |
| BASE | 930200 | cart_view | 600 | 570 | 30 | NO_FAULT_VALIDATION_FAIL |
| VAL1 | 931100 | business | 7991 | 7991 | 0 | NO_FAULT_VALIDATION_PASS |
| VAL1 | 931100 | image_asset | 900 | 900 | 0 | NO_FAULT_VALIDATION_PASS |
| VAL1 | 931100 | cart_view | 1800 | 1800 | 0 | NO_FAULT_VALIDATION_PASS |
| VAL2 | 932100 | business | 8009 | 8009 | 0 | NO_FAULT_VALIDATION_PASS |
| VAL2 | 932100 | image_asset | 900 | 900 | 0 | NO_FAULT_VALIDATION_PASS |
| VAL2 | 932100 | cart_view | 1800 | 1800 | 0 | NO_FAULT_VALIDATION_PASS |

所有判定基于本轮最终入库的 Proxy Server Span，而不是仅看 accepted/refused。预期应追踪的请求不作事后采样排除；未匹配 ID 列表与逐请求状态完整保留。

| 窗口 | gRPC refused Δ | HTTP refused Δ | Envoy dropped Δ | working set 峰值 MiB | Logs/Traces 最大新鲜度延迟秒 |
|---|---:|---:|---:|---:|---|
| BASE | 2861.0 | 1633.0 | 0 | 294.81 | {'logs': 8.02811861038208, 'traces': 8.102521657943726} |
| VAL1 | 0.0 | 0.0 | 0 | 311.31 | {'logs': 2.519394874572754, 'traces': 4.114312410354614} |
| VAL2 | 0.0 | 0.0 | 0 | 310.66 | {'logs': 2.3167355060577393, 'traces': 4.586542129516602} |

计数增量覆盖记录中的监测区间（含起始检查、收尾与补齐审计），不是从 PromQL increase 外推的永久丢失数；不同长度窗口不直接比较总量。缺失计数器为 UNKNOWN，不能作零失败宣传。

表中 Logs/Traces 新鲜度是最新记录事件时间至查询时刻，不是每条记录的精确入库耗时。当前源记录没有可信的 ingest timestamp，因此精确逐条写入延迟为 UNKNOWN；四个不可变快照提供了到达与完成的有界证据，不能将窗口尾部等待时间冒充存储写入延迟。

### BASE

UTC：负载 2026-10-13T13:13:54.097641+00:00 → 2026-10-13T13:18:54.097641+00:00；固定观测结束 2026-10-13T13:19:24.097641+00:00。

Blockers：`['all_expected_sampled_requests_proxy_matched', 'receiver_refused_grpc_zero', 'receiver_refused_http_zero', 'no_refusal_or_export_timeout_logs']`；Collector 相关拒收/导出超时日志匹配 6 行。

Logs 50,575 条；最终 Spans 69,035 条；原始 Metric 查询样本 7,590 行（包含 query_range 共同端点）。

Trace 业务 Service 数 18；可见 Pod UID 数 11。详细 Service/Pod 覆盖在 assessment.json，未将其他服务的未知覆盖补为零。

| 分段 | 首次完成延迟 | +60 完成延迟 | +120 完成延迟 | +240 完成延迟 | 时限/完整性 |
|---|---:|---:|---:|---:|---|
| chunk-00 | 17.20s | 69.41s | 127.78s | 251.15s | True |

+240 新增 Span：0；分类与完整明细保留，未覆盖 +120 数据。

### VAL1

UTC：负载 2026-10-13T13:31:47.273709+00:00 → 2026-10-13T13:46:47.273709+00:00；固定观测结束 2026-10-13T13:47:17.273709+00:00。

Blockers：`[]`；Collector 相关拒收/导出超时日志匹配 0 行。

Logs 149,266 条；最终 Spans 218,998 条；原始 Metric 查询样本 19,696 行（包含 query_range 共同端点）。

Trace 业务 Service 数 18；可见 Pod UID 数 11。详细 Service/Pod 覆盖在 assessment.json，未将其他服务的未知覆盖补为零。

| 分段 | 首次完成延迟 | +60 完成延迟 | +120 完成延迟 | +240 完成延迟 | 时限/完整性 |
|---|---:|---:|---:|---:|---|
| chunk-00 | 19.05s | 70.51s | 128.09s | 252.41s | True |
| chunk-01 | 16.44s | 68.03s | 130.49s | 251.92s | True |
| chunk-02 | 19.91s | 68.34s | 128.06s | 251.91s | True |

+240 新增 Span：0；分类与完整明细保留，未覆盖 +120 数据。

### VAL2

UTC：负载 2026-10-13T13:53:59.202162+00:00 → 2026-10-13T14:08:59.202162+00:00；固定观测结束 2026-10-13T14:09:29.202162+00:00。

Blockers：`[]`；Collector 相关拒收/导出超时日志匹配 0 行。

Logs 150,968 条；最终 Spans 219,643 条；原始 Metric 查询样本 19,760 行（包含 query_range 共同端点）。

Trace 业务 Service 数 18；可见 Pod UID 数 11。详细 Service/Pod 覆盖在 assessment.json，未将其他服务的未知覆盖补为零。

| 分段 | 首次完成延迟 | +60 完成延迟 | +120 完成延迟 | +240 完成延迟 | 时限/完整性 |
|---|---:|---:|---:|---:|---|
| chunk-00 | 17.93s | 69.15s | 129.98s | 252.37s | True |
| chunk-01 | 18.41s | 68.73s | 130.82s | 252.24s | True |
| chunk-02 | 18.61s | 71.26s | 129.69s | 250.21s | True |

+240 新增 Span：0；分类与完整明细保留，未覆盖 +120 数据。

### Collector CPU、内存与队列统计

| 窗口 | CPU 峰值（核） | Heap 峰值 MiB | Working set 峰值 MiB | Trace 队列峰值 bytes | Logs 队列峰值 bytes | Metrics 队列峰值 bytes |
|---|---:|---:|---:|---:|---:|---:|
| BASE | 0.1734 | 247.77 | 294.81 | 17023.0 | 27070.0 | 0.0 |
| VAL1 | 0.2106 | 220.88 | 311.31 | 8173.0 | 12902.0 | 20832.0 |
| VAL2 | 0.1911 | 219.42 | 310.66 | 7851.0 | 29786.0 | 0.0 |

统计来自真实采样点，不宣称捕获所有瞬时峰值；Heap 与 Working set 是不同口径。三个表列队列均为已核验配置的 byte sizer，未混入 profiles 队列的其他单位。

| 窗口 | Receiver accepted gRPC Δ | Receiver accepted HTTP Δ | Exporter failed 计数 | Limiter refused 计数 |
|---|---:|---:|---|---|
| BASE | 59200.0 | 28852.0 | UNKNOWN_MISSING_OR_RESET_NOT_ZERO | OBSERVED |
| VAL1 | 165825.0 | 74850.0 | UNKNOWN_MISSING_OR_RESET_NOT_ZERO | UNKNOWN_MISSING_OR_RESET_NOT_ZERO |
| VAL2 | 165387.0 | 75374.0 | UNKNOWN_MISSING_OR_RESET_NOT_ZERO | UNKNOWN_MISSING_OR_RESET_NOT_ZERO |

Exporter retry 自遥测未暴露，保留 UNKNOWN。没有因其缺失宣称从未重试；完整性以最终存储逐请求匹配及有界补齐为依据。

## 6. 副作用、安全状态与结论边界

最终健康：`COMPETITION_ENVIRONMENT_PASS_WATCHDOG_PENDING`；实际环境 blockers：`[]`。五主机内存、磁盘、时钟及无残留检查有独立原始记录。

验证期间持续检查 Collector UID/container/restart、380 MiB 停止线、节点 Ready/Pressure、节点 CPU/内存余量、数据盘 UUID 与空间、OpenSearch green 和业务请求成功。配置更换的计划内 Pod 替换单独记录，不算成验证窗口内零重启证据。

前后对照包含一次为加载配置所必需的 Collector 重启，没有执行原配置重启对照或 ABA 回切试验，因此不能完全排除重启清理运行状态的贡献。结论以候选配置下两次独立真实请求到最终存储的结果为准，不宣称已证明全部历史缺失的唯一因果链。

本轮保留历史 Service/Pod 注入安全、持续时间和恢复证据，但旧配置下的观测不能自动证明新配置下的完整性。两轮无故障验证不覆盖恢复突发、故障期背压或整个一小时运行；下一轮可按审核决定进行受控观测回归或首题试生产，本轮不会自动开始。

## 7. 产物与审核入口

- 代码、规则、变更与运行结果：`/root/ops-competition-2026/collector-telemetry-repair-20261013/`。
- 配置快照及逐请求诊断：`/data/stage5-v3/ops-competition-2026/collector-telemetry-repair-20261013/diagnosis-v2/`、`/data/stage5-v3/ops-competition-2026/collector-telemetry-repair-20261013/historical-time-scoped-forensics/`。
- 三次有效执行窗口原始数据：`/data/stage5-v3/ops-competition-2026/collector-telemetry-repair-20261013/runs-v3/`；早期失败记录另外保留。
- 每请求匹配：各窗口 `per-request-match.json`；统计及失败原因：`assessment.json`。
- 新就绪建议：`/root/ops-competition-2026/collector-telemetry-repair-20261013/results/production-telemetry-readiness-v2.json`。
- 旧 `COMPETITION_PRODUCTION_TELEMETRY_BLOCKED` 记录保持原样；新文件作为人工审核依据，不自动解除正式题目执行闸门。

**COLLECTOR_PATH_READY_FOR_PILOT。本轮结束，等待人工审核；不输出正式题目 READY。**
