# SF-012 ATTEMPT-02 导出失败报告

**终态：`FAILED_PUBLIC_EXPORT_BOUNDARY`，不可导入。** 本次 1,800 秒真实采集完成，单 Pod 32 MiB/300 秒内存压力按计划执行并恢复；六段采集质量均为 `CHUNK_QUALITY_PASS`，15,094/15,094 条业务请求匹配 Proxy Span。最终健康检查通过，Collector gRPC/HTTP refused 计数维持 0。

冻结导出器 SHA-256 `1fe150fb70197a73d9c757bafbed9d1aadf6eab6ba012c5602cbc040c9df273b` 在导出日志时拒绝了 1 条越界记录：OpenSearch 索引 `otel-logs-2026-10-18`，文档 `KXSTTaEBeYeCCoAm-tv-`，时间 `2026-10-18T05:55:05.388100573Z`，比冻结 T0 早 202,427 ns。只读审计确认其他日志均在对应采集窗口内。原转换规则对这条记录不作边界排除，因此不能事后修改该 Attempt 已绑定的代码并宣布成功。

原始运行保留在 `/data/stage5-v3/ops-competition-2026/runs/SF-012-ATTEMPT-02/`；3,809 文件、4,595,323,337 B 已在 Worker1 `/data/rca-archives/ops-competition-2026/SF-012-ATTEMPT-02/raw/` 完成 SHA-256 和 Metrics/Logs/Traces 恢复读回。原始归档 Manifest SHA-256 为 `8b1157cd548ef443213e80ad43d57aba51af40d0bdeb79efd5c0af0ebc8a5050`，采集终态 SHA-256 为 `3fde7287fb63c1b5d219ae40836c06376392d4f4fd2599783b4c0f103f770a01`。失败记录位于 `/root/ops-competition-2026/sf012-finalization-v1-20261018/failure-record.json`，SHA-256 `76c1fa666dab672fb984b09d83d695d07d1097ff06bd1e4a9e4729675ede22aa`。

Public 导出只留下不完整中间文件，未形成六文件 Manifest；Private GT 未创建，比赛服务器导入禁止。生产总账已以 CAS 方式更新，当前 SHA-256 `3736e5ab32d2ebf5c2286265f19119a796f663e1c3db7e617114769d74ef79c0`，READY 仍为 7/10。

后续独立候选版本在导出阶段按 `[start,end)` 精确筛选 OpenSearch 查询边界附近 1 ms 内的邻接记录，明确计数；超过查询精度的越界记录继续拒绝。该候选已通过合成边界测试和下述只读日志诊断，但仍须在新 Attempt 前完成独立验证、注册并冻结，不得用于追认 ATTEMPT-02。

候选版本随后对原始日志页做了只读诊断：191,607 条在窗口内、1 条属于 1 ms 内查询边界邻接记录、更远的越界记录 0。诊断 SHA-256 `170d4c227eac3145edeb0136f5e6ec681ba0992b5828fd896a1938f82e5661c2`，保存在 `/root/ops-competition-2026/sf012-boundary-fix-draft-20261018/real-log-partition-diagnostic.json`，明确标记 `DIAGNOSTIC_ONLY_NOT_OFFICIAL_PUBLIC`；不改变本 Attempt 失败终态。
