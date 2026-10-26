# SF-002、SF-003、SF-010 数据导入交接报告

执行日期：2026-10-16 UTC。目标：`192.168.51.51`。**状态：`PUBLIC_DATA_IMPORTED_TEAM_RELEASE_CLOSED`。** SF-002 已登记为 PRACTICE；Master 总账中唯一符合 `READY_FOR_IMPORT` 的 SF-003、SF-010 已登记为 SEMIFINAL 数据。没有开放 practice、AM/PM 或正式题清单，没有导入 Private GT，没有提交或评分。

## 权威范围与实际路径

Master 总账 `/root/ops-competition-2026/plans/SF-003-to-SF-012-production-v2-ledger.json` 只有 SF-003 ATTEMPT-02、SF-010 ATTEMPT-07 为 `READY_FOR_IMPORT`。SF-004、SF-005 的失败尝试、SF-009 无故障演练及其他未开始题目均未传输或登记。SF-002 另由 `/root/ops-competition-2026/reports/SF-002-production-report.md` 证实为 60 分钟、两次真实故障恢复、双副本校验通过的练习题。

| Case | 类型/Attempt | Master 原始 Public 六文件 | 51 root-only 原始六文件 | 51 导入 Case / Manifest |
|---|---|---|---|---|
| SF-001 | PRACTICE / 原有 | 本轮未访问源 | `/data/aiops-contest/staging/SF-001-incoming/` 原样保留 | `/data/aiops-contest/public/cases/SF-001/public-v1/`；`/data/aiops-contest/public/manifests/SF-001.json` 未变 |
| SF-002 | PRACTICE / SF-PILOT-02-ATTEMPT-01 | `/data/stage5-v3/ops-competition-2026/batch-sf002-sf009/SF-002/public/` | `/data/aiops-contest/staging/SF-002-incoming-20261016/` | `/data/aiops-contest/public/cases/SF-002/public-v1/`；`/data/aiops-contest/public/manifests/SF-002.json` |
| SF-003 | SEMIFINAL / ATTEMPT-02 | `/data/stage5-v3/ops-competition-2026/packages/SF-003-ATTEMPT-02/public/` | `/data/aiops-contest/staging/SF-003-incoming-20261016/` | `/data/aiops-contest/public/cases/SF-003/public-v1/`；`/data/aiops-contest/public/manifests/SF-003.json` |
| SF-010 | SEMIFINAL / ATTEMPT-07 | `/data/stage5-v3/ops-competition-2026/exports/SF-010-ATTEMPT-07/public/` | `/data/aiops-contest/staging/SF-010-incoming-20261016/` | `/data/aiops-contest/public/cases/SF-010/public-v1/`；`/data/aiops-contest/public/manifests/SF-010.json` |

传输只列出 `metrics.parquet`、`logs.parquet`、`traces.parquet`、`trace_index.parquet`、`topology.json`、`manifest.json`，由 Mac 上两段 SSH 加密 tar 流桥接 Master 与 51；Master→51 直连 SSH 不通。没有复制父目录。三份原始六文件保留在 `staging/SF-xxx-incoming-20261016/`（`0700 root:root`）。兼容包 `staging/SF-xxx-compat-public-v1-20261016/` 另保存原始 Manifest、Topology、Trace Index 和转换记录；导入器在 `public/cases` 中对三类 Parquet 稳定排序、Zstd 重编码并重建 Trace Index，`public/manifests` 嵌入 Public Topology、原始六文件哈希和源 Manifest SHA。没有删除或改写 Master 源包。

## 完整性与登记

| Case | 源 Manifest SHA-256 = 51 暂存 SHA-256 | 51 导入 Manifest SHA-256 | 窗口 | Metrics | Logs | Spans | Trace IDs | 结果 |
|---|---|---|---:|---:|---:|---:|---:|---|
| SF-001 | `8ad8c5eb25e48a33ebf776a40db0f3fbe62284ee7c26df3e4d04139532db0eaa` | `ffd207aa0fba0e2fa1819cd148635fcd332c33869dce58de66ef566aba324c56`，本轮前后相同 | 60 min | 80,358 | 567,346 | 817,529 | 119,091 | 原样保留 |
| SF-002 | `458645d012ff5c6ffac1d9676a0275a15ab9da5bac403c1d3f5b3afa5b11011f` | `5114842ca72ec70b5e4d15eae6fd7f2d693151e90349158b7c023082703e261b` | 60 min | 84,982 | 573,991 | 818,797 | 118,968 | PASS |
| SF-003 | `2dda9c2d1cb4dd912df3efe5e5d2dcc8fb9b6ae2505f49f24682d2a2d22b1b8e` | `8901c71a88fdae3b391c099c0353dd56d6951f288d4fc41ca8471addff707b7e` | 30 min | 41,160 | 298,746 | 433,676 | 62,528 | PASS |
| SF-010 | `6598702cfb9edd0cd5476de44e46c5c75191f12583c67c34c86e75a6e8d47a96` | `e5e22c24e5a68d2e11f06d3b4150cf6e33be9dd1c44b4378189aad255474a2a4` | 30 min | 41,581 | 295,980 | 432,558 | 62,868 | PASS |

Master 预传输审计 `/root/ops-competition-2026/import-to-51-20261016/SF-xxx-source-validation.json` 与 51 目标审计 `/opt/aiops-contest-server/reports/import-20261016/SF-xxx-transfer-validation.json` 均逐文件核对六文件 SHA/大小、UTC 纳秒时间字段、半开时间窗、60/30 个非空分钟、Trace/Span ID 唯一性及 Trace Index 聚合，三题 `PUBLIC_SOURCE_VALIDATED`。51 的 `SF-xxx-exchange-validation.json` 均 `PASS`，原始未解析父 Span 和 1 组源完全重复 Metrics 行作为可审计源特性保留；未删改观测。三题先后通过导入器 `validate-only`，实际原子登记后又用 `validate_case.py` 逐题从最终路径读回，均 `PASS`。51 当前仅有 `SF-001.json`、`SF-002.json`、`SF-003.json`、`SF-010.json` 四份 Case Manifest；未产生重复 Case。

Master 归档收据：SF-002 Public 在 Worker2/Worker3 均 `COMPETITION_ARCHIVE_SHA_AND_RESTORED_READ_PASS`；SF-003 原始与输出在 Worker3 PASS；SF-010 在 Worker2/Worker3 PASS。51 原始六文件暂存与兼容转换记录可用于重新导入；`import_case.py` 遇到已有目标会拒绝覆盖。目标 `/data` 导入后可用 `192,514,043,904` bytes，高于 5 GB 下限。新 Case 目录为 `0750 root:aiops-api`，Manifest 和 Parquet 为 `0440 root:aiops-api`；staging 不可由 API/组委会 Web 进程读取。

## 组委会可视化与队伍隔离

`aiops-competition-visualization.service` 保持 active，仅监听 `127.0.0.1:8502`，健康接口 200，不在 Nginx `:8080` 暴露。组织者通过受控 SSH 隧道访问。实际以 `aiops-api` 身份执行的 `/opt/aiops-contest-server/reports/import-20261016/organizer-validation.json` 为 `ORGANIZER_PUBLIC_VIEWS_PASS`：三题完整窗口行数、Metrics 曲线、实体筛选 Logs、Trace 列表/Waterfall、依赖边及 Pod 放置、同一五分钟窗口三模态均 PASS。浏览器实测 `/opt/aiops-competition-visualization/reports/import-20261016/` 保存 15 张 Overview/Metrics/Logs/Waterfall/Topology 截图，`BROWSER_IMPORTED_CASES_PASS`、页面错误 0。组委会 Web 只读 Public，不读取 Private GT。

内部导入器的 `publication_status=published` 表示**数据目录已登记**，不等于队伍赛题发布。真正的选手门禁 `/etc/aiops-contest/competition_state.json` 仍为：practice=`CLOSED`，练习题清单 `SF-001,SF-002`；semifinal 清单空，AM/PM 均 `NOT_OPEN`。SF-002 已准备在未来练习窗口进入 `/v1/practice/cases`，本轮未开放窗口。正式题 SF-003/SF-010 没有加入队伍发布清单。最终状态 SHA-256 `8c42b7af13079334d90d719bc6eb9e5826a2ea0b4303d4d7e45185360a992fd8`，修改前备份 `/opt/aiops-contest-server/backups/import-20261016/competition_state.before-SF002.json`。

真实 17 支队伍凭证只在 51 内存中使用、未写入报告。`team-access-isolation-final.json` 证明 17/17 对练习 CLOSED、正式 NOT_OPEN 和旧版 `/v1/cases` 路由均被拒绝；SF-002 metadata 403、SF-003 metadata 403、SF-010 实际 Trace ID 查询 403、匿名正式题 401、尝试直接取静态 Parquet 404。51 外部地址 `:8502` 不监听，Nginx 只代理受鉴权 API。API `/healthz` 与组委会 Web 健康接口均 200；SF-001 Manifest SHA 未变化。

## GT、样板 Agent 与变更边界

SF-002、SF-003、SF-010 在 Master 的 Private 异常 GT 状态均为 `DRAFT_PENDING_HUMAN_REVIEW`。本轮**只迁移 Public**；51 私有评分路径没有这三题的正式 GT，没有批准答案或提交。评分路由对 SF-002 明确选择 `PREVIEW`，answer.json v3 的 `SF-002` case_id 已通过 schema 静态验证；只读 `EvidenceVerifier.case_window("SF-002")` 从实际导入 Manifest 返回 3,600 秒和 `public-v1`。练习窗口保持 CLOSED，因此没有做真实提交或产生新成绩。

50 样板机的实时 Baseline/LLM Agent/Skill/MCP 对 SF-002 的端到端查询**未验证**：当前已知通用 root 登录方式在 `192.168.51.50` 认证失败，未尝试其他密码。51 的客户端按 Case ID 参数化，SF-002 与 SF-001 共用 `CONTEST_EXCHANGE_V2` 模态/字段契约；此前 SF-001 的 50 真机 Agent 闭环有历史报告，但不能替代 SF-002 的实际测试。完成 50 登录交接或经授权的练习测试窗口后，需单独做该项联调，不能据此宣称现在已能提交 SF-002。

兼容修订只涉及 51 的 `scripts/validate_exchange_v2.py`、`prepare_exchange_v2.py`、`case_utils.py`：练习题仍严格 3600 秒，正式题严格 1800 秒；SF-001 旧 CLI 不带新参数仍 `PASS`，原导入样例回归 `PASS`，新增时长测试 2/2 `PASS`。原文件备份 `/opt/aiops-contest-server/backups/import-20261016/`。未重启 Nginx/API/评分器，未改历史提交、Token、SF-001 数据、GT 或场次开放状态。

后续组委会可以通过本机受保护 Web 审核三道新导入题的数据；正式题仍需独立审批 GT 与发布时点。SF-002 实际 50 Agent 联调是本轮唯一未完成的附加验收。
