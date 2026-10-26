# SF-009 反序无故障时序演练阻断记录

**状态：`PRE_FORMAL_BLOCKED_COLLECTOR_REFUSAL`。未执行任何业务故障，未创建正式 Case、Public 包或 Private GT。**

独立演练 `/data/stage5-v3/ops-competition-2026/rehearsals/SF009-REVERSE-NOFAULT-01/` 计划从 2026-10-16 07:01:53.055851 UTC 开始，持续 1800 秒，以无害 Worker3 Marker 分别代表 T+600 的 HTTP 阶段和 T+960 的 Service 阶段。Marker 登记 Manifest SHA-256 `3d3057ec8fb57bfa538aabc1ec784b7697c55d2a445b97f26a9c11575a978655`；两个 Timer 的登记、READY 握手和 ARMED 预检通过，错误哈希和错误 Worker 负向检查均拒绝。实际在第一 Marker 前终止，因此没有证据称两阶段时序合同已通过。

采集器于 07:06:54 UTC 因 Collector receiver refused 计数增长中止，演练终态 `NOFAULT_REHEARSAL_FAILED`。初始 gRPC/HTTP refused 均为 0；07:08:42 UTC 只读复查分别为 662/476。Collector 日志在 07:06:41.139 UTC 明确写出 `Memory usage is above soft limit. Refusing data.`，当时报告 `cur_mem_mib=269`。最后一条采集安全样本（07:06:38 UTC）Collector 工作集为 337,362,944 bytes。两者时间接近，但尚未建立每条丢失 Span 与每次拒收的一一对应关系。Collector 配置 SHA-256 保持 `128ed286e8a959aacddc2ee6c00e2d6e8b0a3eb18ea43e5de730a20e3793af2d`，Pod Ready、零重启；Logs/Traces 查询仍新鲜。不得忽略拒收、重启 Collector 清零计数或将不完整窗口称为可用数据。

同一 Collector 的只读后续快照还显示 Logs refused gRPC/HTTP 为 569/69、Metrics refused gRPC/HTTP 为 1,098/587，初始值均为 0；OpenSearch/Jaeger exporter 队列分别仍有 2,092/1,449 项。Deployment 内存上限是 400 MiB，配置的 `memory_limiter` 为 80% limit、15% spike、5 秒检查，Metrics/Logs/Traces 管线均启用了 `debug` exporter。内存警告直接证明拒收，但不能仅凭这些队列与日志断定唯一上游原因；应在独立修复任务中按时间对齐队列、导出耗时、三信号吞吐与实际请求到最终 Span 身份，再选择最小可回滚修改。

采集器安全停止 Marker 控制器，Worker3 上 E01/E02 Timer/Service 均不活动。最终五主机 `HOST_RESIDUE_PASS`，Master `LIVE_ENVIRONMENT_PASS`。2026-10-16 07:09:32 UTC 数据盘可用空间分别为 Master 212,523,278,336、Worker1 45,481,463,808、Worker2 48,836,321,280、Worker3 50,026,881,024 bytes，均高于 5 GB 下限。

失败演练原始目录保留在 Master。独立源清单 `/data/stage5-v3/ops-competition-archive-index/SF009-REVERSE-NOFAULT-01/source-manifest.json` 的 SHA-256 为 `4fb079193c41191aaf351f907da177fffd769db9162034b255ae8e64552872a8`，覆盖 68 文件、7,976,080 bytes；Master 全文件 SHA 与恢复读回 PASS。正式 SF-009 Run Manifest **未生成**。本轮不更改 Collector 参数、原有安全阈值或已完成题目；下一步应独立诊断 Collector 的内存突发与指标/Trace 负载，在两次新鲜无故障完整窗口里验证零拒收和请求到最终 Span 的身份匹配后，才重新考虑正式生产。
