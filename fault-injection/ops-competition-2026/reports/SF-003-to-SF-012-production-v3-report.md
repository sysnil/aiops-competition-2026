# SF-003～SF-012 正式题生产 v3 执行报告

**本轮终态（2026-10-16 16:43 UTC）：3/10 `READY_FOR_IMPORT`，7/10 尚未 READY。** 本轮新增 SF-005；SF-003、SF-010 保持原有 READY。SF-009 完成真实双故障和完整采集，但正式 Admission 失败；SF-008 完成两个真实故障并恢复，却因尾段 OpenSearch Trace scroll `HTTP 429` 导致正式采集失败。两题均严禁导入。SF-004 的历史失败未重标。出现阻断性采集错误后已停止新的正式注入。没有访问 192.168.51.50/51，也没有开放比赛 API 或批准任何 GT。

## Collector 拒收修复

SF-009 旧无故障演练在第 5 分钟记录 memory_limiter `cur_mem_mib=269` 并产生 Spans、Logs、Metrics 拒收。旧 Collector 上限 400 MiB、memory_limiter 80%/15%/5s，四条 pipeline 均启用 `debug` exporter；内部日志还通过 OTLP 回送同一 Collector。旧 Pod 近 90 分钟有约 11,731 条 debug 输出。这构成可验证的额外负载/反馈路径；拒收与具体缺失 Span 之间仍不能逐条归因。OpenSearch/Jaeger 队列采用 `sizer: bytes`，历史记录把队列数值称为“项”不准确。

独立修复目录：`/root/ops-competition-2026/collector-debug-repair-20261016/`。先备份 ConfigMap、Deployment、Pod 及 SHA，再从 Metrics/Logs/Traces/Profiles pipeline 移除**非存储型** debug exporter；保留 file、Prometheus、OpenSearch、Jaeger、Firepit 导出，memory_limiter、batch、持久队列、重试、400 MiB 容器上限及 320 MiB Go 限额不变。原 relay SHA `128ed286e8a959aacddc2ee6c00e2d6e8b0a3eb18ea43e5de730a20e3793af2d`；现 relay SHA `ad42e84b2a1d25b6b9b716f2a119f8d3ab6c50a975b860f3a8eed939618dd05f`。独立 systemd 自动回滚定时器在修改前激活；两轮验证和五主机复查后停止，`retained.json` SHA `386bd723066f515e9a0555c80ef70d1bbc81e765e4b05b8ee206f068b8048991`。若需回滚，应在无实验运行时执行 `python3 -B /root/ops-competition-2026/collector-debug-repair-20261016/scripts/change.py rollback`，并重新验证采集；不可用重启清零计数冒充修复。

验证执行器的前两版留下原始失败记录：新旧 Collector 交替期间的 Prometheus 旧节点指标序列使一次预检失败；另一次 30 分钟调度触及旧采集器“不得提前超过 30 分钟”保护；随后完整诊断窗口暴露了队列**字节**单位误用及全量 Span 评估器在 Master 上的高内存问题。高内存评估进程在可用内存跌破 4 GB 时立即终止，Master 恢复；改用独立 v4 的逐文件/SQLite 有界内存评估后，旧窗口只作为 `DIAGNOSTIC_REPLAY`，没有充当正式两轮之一。失败证据未覆盖。

正式预注册计划：`plan-validation-v4.json` SHA `30af20371172b59fc0440369d4c98621d3d9fc6191e11996f7b5cec675801517`。MEDIUM 1.4 sessions/s、Image 1/s、Cart 2/s 不变；仅将未来对照并发槽从 2/4 提至 3/6，种子分别 741603、741604。两轮均为新的、独立的 1800 秒无故障窗口，六个 300 秒分段与不可变 +5/+60/+120/+240 秒 Trace 审计。

| 验证 | 业务请求 | Image / Cart | 最终 Spans | Proxy 身份缺口 | 三模态拒收增量 | Collector 工作集峰值 | 结果 |
|---|---:|---:|---:|---:|---:|---:|---|
| VAL1 | 16,024 | 1,800 / 3,600 | 425,300 | 0 | 0 / 0 / 0 | 338,644,992 B | `NO_FAULT_VALIDATION_PASS` |
| VAL2 | 15,986 | 1,800 / 3,600 | 434,130 | 0 | 0 / 0 / 0 | 342,392,832 B | `NO_FAULT_VALIDATION_PASS` |

两轮 Metrics、Logs、Traces 均 30/30 分钟有数据，所有请求到最终 Proxy Server Span 的 Trace ID 匹配通过，无重复 Span、Collector OOM/重启或持续导出积压。VAL1 OpenSearch/Jaeger 队列峰值约 43,968/20,278 **字节**且终点均为 0；VAL2 峰值约 30,170/12,546 字节，未超过预注册容量比例。Exporter failed/retry 的独立 Prometheus 序列未暴露时记 `UNKNOWN`，没有声称其绝对为零；最终存储逐 ID 和日志审计补足可见性。原始验证数据位于 Master 数据盘 `collector-debug-repair-20261016/runs-v6/`。

## 业务正常对照

SF-005 旧 ATTEMPT-02 在故障结束后遇到约 1～2 秒的 Image/Cart 共同请求时延，填满原 2/4 并发槽，各漏发 1 次；当时 Collector 拒收为 0。新 3/6 容量在两次独立无故障 1800 秒窗口以及 SF-005 ATTEMPT-04 正式窗口均达到 1,800/3,600、成功率 100%、漏发 0。频率、超时及正式零漏发门槛未降低，旧漏发请求未回填。

SF-004 旧 ATTEMPT-01 的 Cart 恢复期/基线 Proxy p95 比值 1.515，超过冻结 1.25。新无故障诊断中，第一轮 Image 为 1.633/1.261 ms（1.295，失败）、Cart 稳定；第二轮 Image/Cart 均通过。首版检查器因 User-Agent 不一致得到 0 样本，已保留该错误并另建 Trace ID 精确关联版本。两种种子尚不能证明原 120/60/120 秒正常对照稳定，因此**未重跑 SF-004、未调整其冻结阈值**；不能把其 HTTP 真实生效重新标成 READY。

## 真实题目与失败 Attempt

| Case | 当前状态 | 本轮实际结果 |
|---|---|---|
| SF-003 | `READY_FOR_IMPORT` | 原 ATTEMPT-02 保持不变；Frontend 单 Pod 32 MiB 内存压力。 |
| SF-004 | `FAILED_OBSERVABILITY_CONTROL` | 原 ATTEMPT-01 保留；Cart p95 比值 1.515 > 1.25，新对照跨种子仍有 Image 漂移。 |
| SF-005 | `READY_FOR_IMPORT` | 新 ATTEMPT-04，Recommendation 双副本 ServicePause30。ATTEMPT-03 因 Master→Worker3 临时 SSH socket 缺失在注入前终止，证据保留；恢复经授权的临时 SSH 复用通道后，以全新 ID 重做安全门。 |
| SF-006 | `NOT_STARTED` | Pod→Service 跨 Worker 双故障控制器及独立时序/清理资格尚未完成。 |
| SF-007 | `NOT_STARTED` | Service→Pod 跨 Worker 双故障控制器及独立时序/清理资格尚未完成。 |
| SF-008 | `FAILED_CAPTURE_INFRASTRUCTURE` | ATTEMPT-04 的 PodMemory60→HTTP 延迟60 均真实执行、恢复；最后一段 Jaeger OpenSearch Trace scroll 收到 HTTP 429，正式采集 FAIL，Public/GT 未生成。前三个独立 Attempt 均在注入前安全失败。 |
| SF-009 | `FAILED_ADMISSION` | 新 ATTEMPT-01 完成 HTTP→Service 两个真实故障和 1800 秒采集，但 `service_caller_failures`、`service_image_latency` 两项准入失败。 |
| SF-010 | `READY_FOR_IMPORT` | 原 ATTEMPT-07 保持不变；ServicePause+HTTP 延迟。 |
| SF-011 | `NOT_STARTED` | 三故障控制器、各阶段独立 Watchdog、1800 秒时间合同尚未资格验证。 |
| SF-012 | `NOT_STARTED` | 三故障控制器、各阶段独立 Watchdog、1800 秒时间合同及与 SF-011 的差异尚未验证。 |

SF-005 正式窗口 `2026-10-16T11:23:55.458636Z`～`11:53:55.458636Z`。ServicePause 实际 `11:33:55.715567Z` 启动，较计划晚约 0.257 秒，持续 30.000137 秒；两个原 PID 均恢复。正式准入的 14 项检查全 PASS。Public：Metrics 38,425、Logs 281,046、Spans 420,364、Trace Index 60,102，六文件 38,974,195 B，Manifest SHA `3bddfc396be4bb0a6002d1bbd959c44bf770a692e916dfd5c71da31fafd756f2`。Parquet schema、UTC 边界、Trace Index 与 Span ID 集合、每分钟三模态、逐文件 SHA、Public 泄漏扫描均独立通过。Private 异常草案从 Public 生成 24 候选、归并 7 事件，`DRAFT_PENDING_HUMAN_REVIEW`；历史无故障参考的 Collector SHA 与现环境不同，故统计可比性有限，不得当正式评分 GT。原始 2,560 文件 / 5,647,529,498 B，输出包 15 文件 / 39,395,463 B，Master+Worker2+Worker3 SHA 与恢复读回 PASS。原始/输出清单 SHA 分别 `d201d77a7da0403ad4cec3e3ac76fe70d4646ccf0e1e5c8728586894a961e739` / `a7e5057633fcfa518a250b4cced374b6562484a43590bb69a859881e8eaa7b82`。本轮唯一**新增待导入题**：SF-005 ATTEMPT-04；本任务未操作比赛服务器。

SF-009 先以 `SF009-REVERSE-NOFAULT-02` 完成 1800 秒双 Marker 演练，真实故障数 0，原始 2,637 文件 / 5,835,704,773 B 在 Master SHA 与恢复读回 PASS。正式 ATTEMPT-01 窗口 `2026-10-16T13:15:30.424948Z`～`13:45:30.424948Z`；首页 250 ms 延迟作用于 84 条真实请求，Public 首页 p95 14.192→267.983→14.857 ms。ServicePause 实际 `13:31:30.693650Z` 启动，较计划晚约 0.269 秒，持续 30.000245 秒，两个原 PID 恢复。正式 Public：Metrics 39,534、Logs 285,434、Spans 423,849、Trace Index 60,689，六文件 39,322,505 B，Manifest SHA `255c71702c37fe8c7a06682574509f52e2a04ef5b6ce13383fc25e91801d1cf4`，完整性和泄漏扫描 PASS。但是 Service 故障期 25 条公开调用 Trace 中失败 0 条，未达到冻结调用失败门槛；Service 阶段 Image 对照 p95 2.899/2.002 ms（1.448）高于 1.25。不得降低阈值或仅因服务器 Span 消失而代替调用失败。GT 不生成、服务器导入禁止。原始 2,604 文件 / 5,694,685,340 B，最终失败证据包约 39.34 MB，Master+Worker2+Worker3 SHA/读回 PASS；原始/输出清单 SHA `2810255d6839528c028aa1e810b94f0931d61b7353345e40fcffd15084e7568f` / `f09f9dd549c9aa7becfc410c3356f64b42ae9cbc9c067516eba92f24108a6b92`。

SF-008 先进行 Worker2 Pod→HTTP 的 `SF008-POD-HTTP-NOFAULT-02` 独立 1800 秒无害双 Marker 演练，两个本地 UTC Timer、五主机残留、六段采集和 21,282 条 Proxy 身份匹配 PASS；源 2,616 文件 / 5,929,142,881 B 在 Master SHA 与恢复读回 PASS，源清单 SHA `410fe0f91669b28b31844d078e6d2b611c9f7a3295720e46d35bdb5eb3059bc6`。同源 Parquet 转换 smoke 生成 Metrics 39,000、Logs 285,586、Spans 427,427、Trace Index 61,457，30/30 分钟三模态和泄漏扫描 PASS；窗口标记 `formal_case=false`，合并 Admission 对它明确拒绝。该 rehearsal 从未注入业务故障，不计题库。

正式 SF-008 有四个**永久保留的独立 Attempt**。ATTEMPT-01 在 ARMED 预检因缺 `SF008_RUN` 环境绑定、ATTEMPT-02 因五主机监控沿用旧“启动后 30 秒必须看到采集器”的假设、ATTEMPT-03 因旧观察器服务残留而在真实故障前失败；均无内存压力或 HTTP 延迟。ATTEMPT-03 揭示通用残留审计漏检 `ops-sf008-*`：现场 harmless sentinel 运行时通用审计错误 PASS，新增独立竞赛单元审计正确 FAIL；sentinel 停止后两项审计均 PASS。新版 ATTEMPT-04 在正式计划生成前必须提供两项同源、120 秒内新鲜的 PASS，运行期 preflight 只允许本 Run 控制器/观察器/采集器；50 项 Master 回归和现场负向测试 PASS。

ATTEMPT-04 的正式窗口 `2026-10-16T15:56:44.178044Z`～`16:26:44.178044Z`。Worker2 Frontend 单 Pod 32 MiB/60 秒压力于 `16:06:44.460920Z` 实际开始，较计划晚约 0.282 秒，持续 60.000099 秒并完成独立 Watchdog 清理，目标及 sibling 保持原 UID、无重启；故障前两副本分别有 1,485/1,652 条普通业务 Trace，CPU/内存各有 21 个样本。T+960 的首页延迟阶段记录 84 条真实 250 ms 请求，Pod 已先恢复。两个机制的执行/恢复及 POST 预检 PASS，Image 1,800/1,800、Cart 3,600/3,600、零漏发，Collector 三模态拒收始终为 0。**但**最后一个 5 分钟分段从 Jaeger 的 OpenSearch trace storage 滚动读取时收到 `HTTP 429 Too Many Requests`，`chunk-05-first` 退出 1，采集终态 `SF008_FORMAL_CAPTURE_FAILED`；仅前五段质量可用。没有按事后回溯补造尾段，没有生成正式 Public/GT，不进入题库。原始 2,250 文件 / 4,840,279,825 B 已在 Master+Worker1+Worker2 全文件 SHA 和恢复读回 PASS，源清单 SHA `35773cc6c9e98f0867901c70894d6f7179bf79a269112869a0a52440851a711c`，采集失败终态 SHA `1f8ee37de6440eae28cfa77744ff0e669e2e26db5cbb93ca73f1cfd8d1abc35b`。正式 Manifest SHA `328cf51c4e651cb199e3a624b021bc95c3a5ad67431cfacb0a9b78a83328eba1`；任何导入均禁止。

OpenSearch 当前集群健康为 green，四节点业务 Pod Ready，但这并不抹去失败窗口的 429。只读日志显示失败前后多次约 1 GB JVM heap 高使用量 G1GC，`_nodes/stats` 当前 parent circuit breaker 累计 `tripped=4`、search 线程池 `rejected=0`、最大开放 scroll 默认 500；因为未保留当次 HTTP 响应体，也没有失败前的 breaker 计数，**只能推断**存储侧内存压力可能相关，不能声称已唯一定位 429 根因。下一次正式采集前应先在独立无故障压力窗口复现并修复 OpenSearch trace scroll/read path，保留 429 响应体和各 breaker/scroll 计数，再做完整 1800 秒三模态验证；不得重跑 ATTEMPT-04 后替换其盲结果。

## 多样性、空间与当前阻断

已合格三题分别提供 Pod 资源曲线、Service 暂停错误传播、Service+HTTP 两事件，异常类型和模态不只是随机种子差异。SF-009 的 HTTP 延迟及 Service Server Span 消失是真实症状，但因冻结准入失败不计入题库。剩余候选如仅重排同一目标的相同三机制，可能产生高度重复的 Public 异常；必须先完成跨 Worker 双/三故障执行器、独立无故障时序验证，或对新目标/路径做真实机制资格验证，再审查实际传播与多模态差异。不得通过减少控制样本或提高故障强度掩盖缺口。

SF-008 失败归档后五主机 `HOST_RESIDUE_PASS`、独立 `COMPETITION_UNIT_RESIDUE_PASS`、Kubernetes 四节点 Ready、OpenSearch green、无活动故障 Timer/内存 holder。各文件系统仍高于十进制 5 GB 绝对下限；最新 Master 根盘/数据盘可用约 45.56/166.60 GB，Worker1 数据盘 30.86 GB、Worker2 31.44 GB、Worker3 33.09 GB，VM1 根盘 87.40 GB。Worker2 若独自承接后续十题，累计预测会突破下限；后续必须按单题实测把异机副本分散到容量合适的 Worker，不能删除历史归档或把 5 GB 当作可用预算。生产总账 SHA `bcaf9863423554d8052bba906221c940b52f18ad998935887cde4a2d79ebd82c`，旧账备份与 `status_changes` 均保留。

**本轮停止新题生产。** 首先修复并重新独立验证 OpenSearch trace scroll 429 采集阻断；在此之前 SF-006/007 的跨 Worker Pod+Service 执行器、SF-011/012 的三故障合同即使开发完成也不得注入。SF-004 的正常对照要先在不同种子下稳定满足原门槛；SF-009 的 Service 调用失败证据和 Image 对照问题需新的、前瞻性机制/负载资格版本；SF-008 ATTEMPT-04 必须永远保持失败。所有新 READY 只在 Master 总账列为待导入，本轮不访问 50/51。

最终独立复查证据位于 `/root/ops-competition-2026/sf008-safety-audit-20261016/results/`：`generic-residue-terminal.json`、`competition-units-terminal.json` 与 `storage-terminal.json` 均 PASS。Collector relay SHA 仍为 `ad42e84b2a1d25b6b9b716f2a119f8d3ab6c50a975b860f3a8eed939618dd05f`，Spans/Logs/Metric points 当前拒收计数均为 0，最新 Logs/Traces 入库年龄约 1.8/3.0 秒；这些恢复状态不改变 SF-008 的不可补救正式采集失败。
