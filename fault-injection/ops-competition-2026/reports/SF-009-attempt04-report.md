# SF-009 ATTEMPT-04 正式生产结果

**状态：READY_FOR_IMPORT。** Public 尚未导入 51 或发布；Private GT 仅 `DRAFT_PENDING_HUMAN_REVIEW`，不用于正式评分。ATTEMPT-01/02/03 的失败记录永久保留。

冻结窗口为 2026-10-18 04:57:08.516775～05:27:08.516775 UTC，完整 1,800 秒。采用 LOW-STABLE 状态机 0.7 sessions/s，以及 Image、Cart、首页、推荐各 1 次/秒的固定普通业务请求。Run Manifest SHA-256 `cbff5e273e8f03b8c09c2f9278c8b9572f0e94744cec65a3faca48fc5b674a50`；独立无故障控制校准与 Admission V2 均在注入前锁定。

先执行首页 250 ms 请求延迟 60 秒，真实施加 102 条普通业务请求；再在 Worker3 将两个 Recommendation 进程整体暂停 30.000125 秒。Service 实际开始 05:13:08.764733 UTC，最坏启动偏差 0.256 秒；原两个 PID 恢复，独立清理状态 `COMPETITION_CLEANUP_ACTIONS_VERIFIED`。HTTP 与 Service 恢复、最终 POST 检查均 PASS，无故障残留。六段采集质量 PASS，15,177/15,177 条完成请求与 Proxy Span 逐 ID 对应，Collector refused 增量为 0。

Public 六文件包含 Metrics 40,734 行、Logs 195,753 行、Spans 354,327 行、Trace Index 54,268 行，Manifest SHA-256 `85c2d7f401939d62ad2cf76d24e19c5cb5250ad76c31e775293520f33a6bfd37`。Parquet 全量读回、UTC 纳秒 `[start,end)` 边界、Trace/Span 身份、Pod UID、30/30 分钟模态覆盖及泄漏扫描均 PASS。独立 Public 校验 SHA-256 `bfa783b1ab2115e0696758c3a9762ad2d8ef3d38293c421f7149f275aaf77750`。

预注册 Admission V2 所有项目 PASS，判定 SHA-256 `3ddd6011eceac5c3a61d09d820ab283093513a4973acad585c7cdf85fd8d12ec`。首页 Proxy p95 前/中/后为 14.233/267.373/16.340 ms；Recommendation 公开 Server 完成量相对故障前速率约为 0.031，正常无故障 VAL5/VAL6 约为 1.015/0.963。Image/Cart 的样本量、时延稳定与恢复均通过。Private 异常草案由 Public 证据生成，16 个候选归并为 6 个事件；GT 文件 SHA-256 `094b5aee5464259c98b70cc1d803639c5b49c6f853393602ca68311e17bb97f9`，未读取注入计划构造异常特征，仍待人工审核。

原始归档 3,853 文件/4,642,161,979 B，输出包 11 文件/32,379,853 B，在 Master＋Worker2 完成逐文件 SHA、恢复读回及 Parquet Header/Footer 校验；Manifest SHA-256 分别为 `2d79c79704acd96e7af5b5ed8de3f90077c2fa2ac34ff3f727f7e0da61d2f02c` 和 `9efafbebaa95944fd1163a53e7061dde2e82a72a7caee0098a00e01d53cab1b5`。归档后五主机健康、故障残留、竞赛 Timer/unit 及实际十进制 5 GB 磁盘底线 PASS。Worker2 不足以再承担相同大小的下一题归档，下一题须另选已验证的归档目标并重新做容量预检。

Master 原始目录：`/data/stage5-v3/ops-competition-2026/runs/SF-009-ATTEMPT-04/`；Public/Private 包：`/data/stage5-v3/ops-competition-2026/packages/SF-009-ATTEMPT-04/`；Worker2 归档：`/data/rca-archives/ops-competition-2026/SF-009-ATTEMPT-04/`；归档索引：`/data/stage5-v3/ops-competition-archive-index/SF-009-ATTEMPT-04/`。生产总账 CAS SHA-256 `694937cb16458c7c5d3c962467def53d515b3579ea39d4adbb3481223695c431`；当前正式题 7/10 READY。
