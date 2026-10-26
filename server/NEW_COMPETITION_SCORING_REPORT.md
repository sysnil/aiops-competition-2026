# 新版半决赛与决赛评分系统改造报告（51 服务器）

验收日期：2026-10-17  
服务器：`192.168.51.51`  
项目：`/opt/aiops-contest-server/`  
结论：`NEW_COMPETITION_SCORING_DEMO_READY`

> 该状态表示题目层、问答协议、确定性评分、练习题端到端和决赛轮次权限框架已可演示；不表示正式题已批准或决赛已可开场。实际配置仍为：Practice `CLOSED`，Semifinal AM/PM `NOT_OPEN`，正式发布 Case 为空，Final `TEST-R01=NOT_OPEN` 且未绑定 Case。

## 1. 基线、备份与回滚

改造前已核验 API、Private Grader、Nginx、资源、Case 目录和提交库，并建立备份：

`/root/aiops-backups/new-competition-20261017T1400CST/`

包含：

- `aiops-contest-server.tgz`：改造前项目。
- `etc-aiops-contest/`：比赛状态、队伍注册表和密钥配置备份。
- `var-lib-aiops-grading/`：SQLite、WAL 和历史答案。
- `nginx-aiops-contest.before`：Nginx 改造前配置。

备份库有 35 条历史提交；当前库有 37 条，新增的 2 条均为 P-Q01 练习验收记录，历史数据未重写或清空。回滚时应停止 API/Worker，同时恢复项目、`/etc/aiops-contest`、评分数据库与 Nginx 配置，再逐项校验所有者和权限。

## 2. 实际修改文件

主要新增/修改：

- `app/questions.py`：可热加载 Question Catalog，只输出已发布题目的公开字段。
- `app/final_rounds.py`：决赛轮次、双队身份、来源 IP、时间窗与 Case 隔离校验。
- `app/main.py`：Question API 和 `/v1/final` 只读数据 API。
- `config/questions.json`：P-Q01 练习题与 Q01–Q10 未发布模板。
- `config/final_rounds.example.json`：可配置决赛轮次结构。
- `grading/submission_api/models.py`、`router.py`、`store.py`：QA v1 协议、提交、幂等、持久化和十题汇总。
- `grading/schemas/qa-answer-v1.schema.json`：问答绘图答案 Schema。
- `grading/private_grader/qa_scoring.py`：4+2+2+2 确定性评分。
- `grading/private_grader/evidence_verifier.py`、`service.py`、`worker.py`：数据点/PNG/来源窗口校验与 QA 私有 GT 分支。
- `grading/scripts/build_qa_preview_gt.py`：直接从 Public Parquet 独立重算练习 GT。
- `scripts/manage_final_rounds.py`：组委会离线配置/开放/关闭轮次工具，原子写入并记录审计 JSONL。
- `config/nginx-aiops-contest.conf`：QA 专用路由允许 3 MiB；旧提交路由仍为 128 KiB。
- `grading/tests/test_qa_scoring.py`、`tests/test_questions.py`、`tests/test_final_rounds.py`：新增评分、隔离和可达满分回归。

运行时配置：

- `/etc/aiops-contest/questions.json`：服务端题库。
- `/etc/aiops-contest/final_rounds.json`：决赛轮次，`0640 root:aiops-api`。
- `/etc/aiops-private-grader/qa-gt-preview/P-Q01.json`：练习预评测 GT，`0640 root:aiops-grader`。

## 3. 新旧 API 和 Schema 对照

| 用途 | 原有 | 新增/调整 | 兼容状态 |
|---|---|---|---|
| Case 和观测数据 | `/v1/{practice|semifinal}/cases/...` | 不改数据契约 | 保留 |
| 半决赛题面 | 无 | `GET /v1/{mode}/questions` | 新增，继承 Token/场次/Case 权限 |
| 单题题面 | 无 | `GET /v1/{mode}/questions/{question_id}` | 未发布题统一 404 |
| 提交 | `POST /v1/{mode}/submissions` | 识别 `task_type`；QA 推荐 `POST .../submissions/qa` | 旧 v2/v3 路径保留 |
| 半决赛汇总 | 无 | `GET /v1/{mode}/scores/semifinal` | 10 题满分 100 |
| 决赛轮次 | 无 | `GET /v1/final/rounds/current`、`/{round_id}` | 身份/IP/时间窗校验 |
| 决赛数据 | 无 | `/v1/final/rounds/{round_id}/case/{metadata|topology|metrics|logs|traces}` | 仅当轮两队 |
| 异常检测答案 | v2/v3 | 不覆盖 | 历史评分保留 |
| 问答绘图答案 | 无 | `task_type=SEMIFINAL_QUERY_VISUALIZATION`, `schema_version=1.0` | 独立分支 |

QA v1 包含 Question/Case、图表标题与坐标轴、1–4 条时序数据、PNG base64 与 SHA-256、3 个结构化答案、Public 请求 ID、窗口/过滤条件和处理规则。PNG 限 1.5 MiB，base64 限 2,000,000 字符，QA 请求限 2.5 MB。

## 4. Question 模板和 Case 映射

| 题号 | 模板 | Case | 状态 |
|---|---|---:|---|
| P-Q01 | checkout Pod 每分钟平均工作集内存 | SF-001 | `PRACTICE_PUBLISHED` |
| Q01 | 指定 Pod 内存使用曲线 | 未映射 | `TEMPLATE_NOT_DATA_VALIDATED` |
| Q02 | 指定 Pod CPU 使用曲线 | 未映射 | 同上 |
| Q03 | Service 每分钟请求量 | 未映射 | 同上 |
| Q04 | Service 每分钟 p95 延迟 | 未映射 | 同上 |
| Q05 | Service 每分钟请求错误率 | 未映射 | 同上 |
| Q06 | Service 每分钟 ERROR 日志数 | 未映射 | 同上 |
| Q07 | Trace 操作每分钟 p95 耗时 | 未映射 | 同上 |
| Q08 | 调用边每分钟 p95 延迟 | 未映射 | 同上 |
| Q09 | 调用边每分钟失败数 | 未映射 | 同上 |
| Q10 | 两个 Service 延迟对比 | 未映射 | 同上 |

实际数据目录仅有 `SF-001`、`SF-002`、`SF-003`、`SF-010`。SF-001/002 是练习数据；SF-003/010 仍未正式发布。不能在未检查有效 CPU、ERROR、Trace/调用边样本并冻结标准答案前将它们填入 Q01–Q10。

## 5. 练习题端到端评分

P-Q01 时间窗：`2026-10-14T09:12:25.156656Z` 至 `09:42:25.156656Z`，目标 Pod：`checkout-6767884cf8-v57xw`，指标：`container_memory_working_set_bytes`，60 s 分桶取 mean，Bytes 转 MiB，NULL 丢弃且不插值。

独立 GT builder 直接从 Parquet 重算得到 31 个图表点；Starter 经 Public API 读取 120 条原始记录并产生同样 31 个点。完成提交：

- Submission：`SUB-67766E360F7043B3B261`
- 状态：`PREVIEW_COMPLETED`
- 图表：4.0/4.0
- 问题 1/2/3：2.0/2.0/2.0
- 总分：10.0/10.0
- Grader：`semifinal-query-visualization-grader-1.0`
- GT：`P-Q01-preview-public-recompute-v1`
- 消息：练习预览分数不进入正式排行榜。

首次边界验证提交 `SUB-38C3A875E79C4F28979A` 因分钟桶起点早于带秒数的原始窗口而被拒绝。修复时只对“首个分钟桶可早于窗口不超过一个 bucket”做显式兼容，原始数据窗口仍严格校验，没有放宽跨题数据访问。

## 6. 评分满分可达性

确定性 rubric：

- 图表身份（类型、标题、序列名）：0.75。
- 坐标轴、UTC 和单位：0.75。
- 数据点、时间和数值容差：2.50。
- 三个结构化答案：各 2.00。

自动测试证明：单题精确答案为 10.0；错一问为 8.0；篡改图表数据失去 2.5 数据分；10 道独立题可得 `10 × 10 = 100`，不通过缩减 GT 或自动补分获得满分。

## 7. 决赛 PK 轮次与权限

Final controller 要求：

1. Team Token 必须包含 `FINAL` 模式。
2. Token 对应队伍必须是该轮擂主或挑战者。
3. Nginx 传入的可信来源 IP 必须等于轮次为该队绑定的 IP。
4. 轮次必须为 `OPEN`，且当前 UTC 位于开始/截止时间内。
5. 该 Case 不能已发布到 practice/semifinal。
6. 两队经同一 Round ID 解析到完全相同的 Case。

单元测试已覆盖两队同 Case、第三队拒绝、错误 IP 拒绝、时间窗拒绝、重用已发布 Case 拒绝。本次没有为测试生成第二个真实队伍 Token，也没有在只有 4 套数据的情况下假装完成真实双机 PK。

轮次可配置时间限制、评委分项权重和 RCA 附加分上限，当前都是待组委会确认的配置值，未标记为正式规则。

## 8. 可信计时与 Token 统计

- 51 已有轮次 `opens_at`/`closes_at`、原子状态更新和组委会审计日志，可作为官方时间边界。
- 50 Web 的本地耗时和模型 `usage` 可用于展示，但并非官方计量。
- 当前 LLM 流量未经过组委会统一可观测网关，51 无法独立核实所有队伍 Token 用量。正式 Token 排名仍是 `PENDING_TRUSTED_GATEWAY`，不能使用选手自报数字。

## 9. 回归与安全结果

| 检查 | 结果 |
|---|---|
| 51 API / Private Grader / Nginx | 全部 `active` |
| 服务端非 Live 自动测试 | 75 项通过（含 63 项 grading 回归） |
| 50 旧 practice cases/metadata/metrics | HTTP 200 |
| 无 Token 访问 practice | HTTP 401 |
| Semifinal 正式列表 | HTTP 403（NOT_OPEN） |
| Final current | HTTP 404（无对本机开放轮次） |
| P-Q01 Question | 只对 50 组委会测试身份可见 |
| Q01–Q10 | 未发布，不出现在 API |
| 旧 v2/v3 历史行 | 35 条原数据保留 |
| 任意 SQL/服务器路径 | 新协议不提供 |
| Private GT | 仅 Grader 组可读，Question API 不输出 |

`tests/test_live_api.py` 中 4 个历史用例仍假设匿名 API，而当前正式契约已为 Bearer Token，因此这 4 个用例未列入通过数；实际无 Token=401 是预期安全行为，不是本次回归失败。

## 10. 尚未解决的阻断条件

1. 需经数据工程和业务人工审核后，将 3 套真实半决赛数据映射到 Q01–Q10，独立重算并批准 10 份 Private GT。
2. 目前导入数据仅 4 套，其中 2 套已用于练习。如 SF-003/010 最终用于半决赛，仍至少需生产 `1 套半决赛 + 8 套决赛 = 9 套`新独立数据。
3. 需两个真实队伍 Token、两台独立来源 IP 和一套从未发布的测试 Case，才能完成真实双机 PK 验收。
4. 需部署可信 LLM 网关或确定服务商 usage 核验方案，才能将 Token 计量用于官方评判。
5. 需组委会批准决赛时长、评委权重、RCA 附加分上限、轮次表和正式审计流程。

上述条件全部完成且经真实队伍提交验收前，不应将状态升级为正式 READY，也不应改变当前闭场状态。
