# SF-001 私有 GT 草案交接

状态：`GT_DRAFT_TRANSFERRED`。本次仅传输 SF-001 的异常 GT 草案，未启用正式评分。

| 项目 | 实际结果 |
|---|---|
| 源文件 | Master `/data/stage5-v3/ops-competition-2026/runs/SF-PILOT-01-ATTEMPT-04/private/anomaly-gt-draft.json` |
| 目标文件 | `root@192.168.51.51:/etc/aiops-private-grader/gt-staging/SF-001/anomaly-gt-draft.json` |
| 字节数 | 1,224,875 |
| 源与目标 SHA-256 | `ff48d969e676d01ff1134809d23ec225f0f1e9893709e928f9f707be04fd6f9a`，独立校验一致 |
| 内容状态 | `DRAFT_PENDING_HUMAN_REVIEW`；58 条候选、13 个归并事件；原文件的公开证据引用校验为 PASS |
| 权限 | 暂存目录 `root:aiops-grader 0750`，文件 `root:aiops-grader 0640`；`aiops-grader` 可读，`aiops-api` 不可读 |

文件经 Master 和比赛服务器的授权 SSH 连接、由 Mac 内存管道传输。目标端先验证完整长度、SHA-256 和草案状态，再原子命名；Mac 未保存 GT 副本。未复制注入计划、执行记录或其他私有文件。Public 目录、数据库、API 服务和评分器配置均未改动。

当前评分器只从 `/etc/aiops-private-grader/gt/<case_id>.json` 读取 `review_status=APPROVED` 的 GT。暂存的原始草案没有顶层 `case_id`，事件字段也不符合评分器的 `fault_type`、`observable_start_time` 等结构；草案有 13 个归并事件，而当前匹配器每侧最多支持 12 个。正式目录中没有 `SF-001.json`。因此本次交接**不能**直接得出 SF-001 的正式评分；需要人工审核事件、确定评分标签，并在批准后处理格式及事件数限制。不得为了通过现有限制而静默丢弃事件或把草案标为已批准。
