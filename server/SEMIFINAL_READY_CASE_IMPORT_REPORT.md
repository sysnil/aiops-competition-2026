# 当前 READY 正式题导入记录

Master 总账中只有 SF-003 ATTEMPT-02 与 SF-010 ATTEMPT-07 为 `READY_FOR_IMPORT`。两题 Public 六文件分别从 Master 的 `/data/stage5-v3/ops-competition-2026/packages/SF-003-ATTEMPT-02/public/` 和 `/data/stage5-v3/ops-competition-2026/exports/SF-010-ATTEMPT-07/public/` 传到 51，源/目标 SHA-256 完全一致，均为连续 1800 秒。最终 Case 位于 `/data/aiops-contest/public/cases/SF-003/public-v1/`、`/data/aiops-contest/public/cases/SF-010/public-v1/`；两份 Manifest 位于 `/data/aiops-contest/public/manifests/`，导入器和最终读回校验均 `PASS`。

SF-003：Metrics 41,160、Logs 298,746、Spans 433,676、Trace IDs 62,528；源 Manifest `2dda9c2d1cb4dd912df3efe5e5d2dcc8fb9b6ae2505f49f24682d2a2d22b1b8e`。SF-010：Metrics 41,581、Logs 295,980、Spans 432,558、Trace IDs 62,868；源 Manifest `6598702cfb9edd0cd5476de44e46c5c75191f12583c67c34c86e75a6e8d47a96`。组委会 Web 的数据与浏览器联调均 PASS；正式题 `published_cases=[]`，AM/PM 均 `NOT_OPEN`，17/17 队伍访问隔离通过。Private GT 未迁移，均待人工审核。

完整逐文件校验、恢复路径、练习题 SF-002、SF-001 保持不变及剩余联调见同目录 `COMPETITION_CASE_IMPORT_REPORT.md`。
