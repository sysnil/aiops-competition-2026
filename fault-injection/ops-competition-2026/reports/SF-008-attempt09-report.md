# SF-008 ATTEMPT-09 正式生产结果

**状态：READY_FOR_IMPORT。** 仅表示 Public 包可待导入；Private GT 为 `DRAFT_PENDING_HUMAN_REVIEW`，未在 51 发布或评分。

本次独立运行 `SF-008-ATTEMPT-09` 采集窗口为 2026-10-17 14:33:30.521398～15:03:30.521398 UTC，完整 1,800 秒。LOW-STABLE 策略使用 0.7 sessions/s 状态机，以及 Image、Cart、首页、推荐各 1 次/秒固定普通业务请求。计划 SHA-256 为 `3860cfdd252801498b8a73fe272f1bd66b79f02d21de30ec25122ecd54a36582`，Run Manifest SHA-256 为 `c43462d225f256002a52a878caf67f8e4c0342d8a2f41994111703bca323b2df`。

Worker2 对 Frontend 单 Pod（UID `d4bb49b4-6fb8-4a07-aaf6-e2caff5df974`）执行真实 32 MiB、300 秒内存压力。14:43:30.798976 UTC 开始，最坏启动偏差 0.284 秒，实际持续 300.000137 秒；14:48:30.906821 UTC 清理。目标及正常副本 UID 均未变，无 OOM、重启或故障残留。目标 Pod before/during/after 内存中位数为 134,029,312 / 170,889,216 / 134,029,312 B；正常副本为 143,142,912 / 143,153,152 / 143,147,008 B。目标和正常副本的普通业务 Trace 样本分别为 996/983/1001 与 1180/1185/1192。冻结 Pod Admission 检查全部通过，判定文件 SHA-256 `4612df91b5797c24813aad20152beb6824d542f3102a788dbe6477422a0ee4fe`。

六段采集和 15,100/15,100 条 Public 窗口请求到 Proxy Span 的逐 ID 匹配通过。Public 数据包含 Metrics 40,560 行、Logs 187,619 行、Spans 346,964 行、Trace Index 52,853 行；Manifest SHA-256 `5182f70f54aa264746db2527f7da5b0a36d40f0328e1ab6003963b8cc7a59418`。六文件 SHA、Parquet 逐行读回、纳秒时间边界、Trace 身份、泄漏扫描及 30/30 分钟覆盖均通过；独立验证 SHA-256 `a28c84986f0cd512fb5fbb318eca0d0ca41277d610de94af1c51a2bcc355ff41`。

Private 异常 GT 草案来自 Public 证据，含 4 个候选、4 个归并事件，状态仍待人工审核，SHA-256 `4febc99c5a9e4efafb775a33cc837f2a6e64c3d05cbf5e2f320a5b82d7acedee`。原始归档 3,756 文件/4,538,148,982 B，输出归档 15 文件/142,195,813 B，在 Master 与 Worker2 分别完成 SHA 和恢复读回；Manifest SHA 分别为 `4f9a33a2bb5e90c2d06e670c6cce62b6f4701d1fb97101cd181a01140fc97a67` 和 `8cf9f2aa5dd9c68e18babb165e16c4df08b5273889362986e3c36a1dd139518c`。五主机残留、竞赛 Timer/unit、业务/Collector 健康及 5 GB 实际容量门禁均通过。

Master 原始数据：`/data/stage5-v3/ops-competition-2026/runs/SF-008-ATTEMPT-09/`。Public/Private 包：`/data/stage5-v3/ops-competition-2026/packages/SF-008-ATTEMPT-09/`。Worker2 归档：`/data/rca-archives/ops-competition-2026/SF-008-ATTEMPT-09/`。归档索引：`/data/stage5-v3/ops-competition-archive-index/SF-008-ATTEMPT-09/`。生产总账更新后 SHA-256 为 `bea5dc3dbce9cf4d8b100b108efbeaa519a4cfbdde3a521f0cbaed074873cb61`；SF-008 ATTEMPT-01～08 的失败记录保留。未访问比赛服务器。
