# 半决赛查询绘图题服务端全链路验收报告

## 1. 结论

本轮已在 `192.168.51.51:/opt/aiops-contest-server/` 完成 2 道练习题与 10 道正式题的受控导入、Question API、确定性评分、内部预览和与 50 号机的真实端到端验收。

验收结果：

- 12/12 道 Question 可由授权测试身份读取完整公开题面。
- 12/12 道题均从真实 Public API 数据独立复算成功。
- 36/36 个问题答案与冻结 GT 匹配。
- Q01～Q10 正确提交均为 10/10，十题合计 100/100。
- 错误数值、实体、时间、单位和篡改图表数据均会实际扣分。
- 正式题未发布；AM/PM 场次均为 `NOT_OPEN`；正式提交记录为 0。
- 全部新 GT 仍为 `DRAFT_PENDING_HUMAN_REVIEW`，未被自动批准。

因此当前状态为：**SEMIFINAL_QA_INTERNAL_PREVIEW_READY**。这不等于正式发布；只有完成 GT 人工批准、赛前备份与场次开放后，才可进入正式运行状态。

## 2. 交付校验与备份

导入前逐项重新校验交付 Manifest：

| 区域 | 校验结果 | Manifest SHA-256 |
|---|---:|---|
| Public delivery | 19/19 通过 | `16175007e51c17c3b4608f30384859b4c078501a1b6f6ab0b76e5a0ccf4fc9a9` |
| Public question manifest | 通过 | `f90225c4e46c369b97eccb893fb8de3ab579ff230d5197f96fcb40a226442112` |
| Private delivery | 115/115 通过 | `7d32aa21733e0d51bfaf63a1715d22877d39bae8524296f132be4a80c1ff6da0` |
| Private source SHA manifest | 来源链一致 | `3027e15e37fd34595b920fd9e36f3d6eb7d62ea9954b838e81734f401e2bab8d` |

导入前已保留备份：

- `/root/aiops-backups/semifinal-qa-20261017T1235Z`
- `/root/aiops-backups/semifinal-qa-import-20261017T1238Z`

导入脚本在正式题已发布或 AM/PM 场次已开放时会拒绝运行，避免在比赛期间替换题面或 GT。

## 3. Question 导入结果

| Question | Case | 模态 / 口径 | 状态 |
|---|---|---|---|
| EX-P01 | SF-001 | Metrics / Pod 内存 MiB | `PRACTICE_PUBLISHED` |
| EX-P02 | SF-002 | Logs / Service 错误日志计数 | `PRACTICE_PUBLISHED` |
| Q01 | SF-003 | Metrics / Pod 内存 MiB | `SEMIFINAL_PREVIEW` |
| Q02 | SF-003 | Metrics / CPU Counter 速率 | `SEMIFINAL_PREVIEW` |
| Q03 | SF-003 | Metrics / 两 Pod 内存比较 | `SEMIFINAL_PREVIEW` |
| Q04 | SF-005 | Traces / Service Span 计数 | `SEMIFINAL_PREVIEW` |
| Q05 | SF-005 | Logs / Service 错误日志计数 | `SEMIFINAL_PREVIEW` |
| Q06 | SF-005 | Traces / 调用边错误计数 | `SEMIFINAL_PREVIEW` |
| Q07 | SF-010 | Traces / Service p95 | `SEMIFINAL_PREVIEW` |
| Q08 | SF-010 | Traces / Service Span 计数 | `SEMIFINAL_PREVIEW` |
| Q09 | SF-010 | Traces / 调用边 p95 | `SEMIFINAL_PREVIEW` |
| Q10 | SF-010 | Traces / 两 Service p95 比较 | `SEMIFINAL_PREVIEW` |

原 `P-Q01` 练习题与历史提交均保留，未被覆盖。

## 4. 实际 API 和提交接口

已部署并验证：

- `GET /v1/{practice|semifinal}/questions`
- `GET /v1/{practice|semifinal}/questions/{question_id}`
- `GET /v1/{practice|semifinal}/cases`
- `GET /v1/{practice|semifinal}/cases/{case_id}/metadata`
- `GET /v1/{practice|semifinal}/cases/{case_id}/topology`
- `GET /v1/{practice|semifinal}/cases/{case_id}/{metrics|logs|traces}`
- `POST /v1/{practice|semifinal}/submissions/qa`
- `GET /v1/{practice|semifinal}/submissions/{submission_id}`
- `GET /v1/{practice|semifinal}/scores/semifinal`

内部预览的 Case 列表已改为使用请求级授权集：绑定 50 号机 IP 的组委会测试身份可见 SF-003/SF-005/SF-010，普通队伍仍只能看到已发布且所属场次已开放的 Case。

## 5. 统计与评分口径

服务端按冻结题面实施如下确定性规则：

- 时间统一为 UTC，查询窗口左闭右开。
- 60 秒桶从 Case 精确起点开始，不按自然整分钟重定位。
- 内存按题面换算 MiB。
- CPU Counter 先按唯一时序分组再求速率，Reset 产生的负增量不计入。
- 计数题空桶为 0；样本不足的统计桶保持 `null`。
- p95 使用 nearest-rank，不使用 linear percentile。
- 调用边按真实 Parent Span / Service 关系过滤。
- 图表 4 分，三问各 2 分，单题 10 分。PNG 作为可视化产物和辅助校验，核心得分依据为 Chart Data、实体、指标、时间和单位。

## 6. 12 道题独立复算与评分

| Question | Public 记录数 | 图表点 | 三问 | 预览得分 |
|---|---:|---:|---:|---:|
| EX-P01 | 240 | 60 | 3/3 | 10/10 |
| EX-P02 | 195 | 60 | 3/3 | 10/10 |
| Q01 | 120 | 30 | 3/3 | 10/10 |
| Q02 | 120 | 30 | 3/3 | 10/10 |
| Q03 | 240 | 60 | 3/3 | 10/10 |
| Q04 | 9,993 | 30 | 3/3 | 10/10 |
| Q05 | 9 | 30 | 3/3 | 10/10 |
| Q06 | 58 | 30 | 3/3 | 10/10 |
| Q07 | 36,413 | 30 | 3/3 | 10/10 |
| Q08 | 117,779 | 30 | 3/3 | 10/10 |
| Q09 | 18,946 | 30 | 3/3 | 10/10 |
| Q10 | 154,232 | 60 | 3/3 | 10/10 |

结论：12/12 图表复算通过，36/36 个问题匹配，Q01～Q10 合计 100/100。所有评分均为 `PREVIEW_COMPLETED`，未进入正式排行榜。

## 7. 错误答案扣分回归

以 Q01 的真实正确答案构造 Schema 合法的单变量错误：

| 错误类型 | 得分 | 结果 |
|---|---:|---|
| 篡改一个 Chart Data 点 | 7.5 | 图表扣分 |
| 问题数值错误 | 8.0 | 对应问题 0/2 |
| 图表实体错误 | 9.25 | 图表实体项扣分 |
| 图表时间点偏移 1 秒 | 7.5 | 图表时间/数据项扣分 |
| 问题单位错误 | 8.0 | 对应问题 0/2 |

该测试证明满分可达与错误可扣分同时成立，未通过放宽 GT 或容差获得满分。

## 8. 权限、GT 与历史兼容回归

- Practice 仍为 `CLOSED`，只有绑定测试身份可执行验收。
- Semifinal `published_cases=[]`，AM/PM 均为 `NOT_OPEN`。
- SF-003/SF-005/SF-010 仅在 `internal_preview_cases` 中，不属于正式发布集。
- 假 Token 访问返回 401；HTTP 尝试读取 Private GT 路径返回 404。
- Question API 响应不包含 `expected_answers`、`tolerance`、`private_gt`、`reference_chart` 或评分细节。
- 新 QA 的半决赛正式评分记录为 0，内部预览不消耗正式次数。
- 原 v2/v3 异常检测历史记录仍在原库中；新路由仅增加 QA v1，未删除或改写历史答案。

服务端回归：83 项测试收集，80 通过，3 项需真实 Team Token 的 live-data 断言在 51 号机上按设计跳过；它们已由 50 号机的真实鉴权 E2E 覆盖。

## 9. 资源与运行状态

验收结束时：

- 51 号机内存 15 GiB，可用约 14 GiB，Swap 未使用。
- 系统盘 96 GiB，可用 81 GiB；数据盘 196 GiB，可用 180 GiB。
- `aiops-contest-api`、`aiops-private-grader-demo`、`nginx` 均为 `active`。

## 10. 主要修改文件

- `app/questions.py`
- `app/access_control.py`
- `app/main.py`
- `grading/submission_api/router.py`
- `grading/submission_api/models.py`
- `grading/submission_api/worker.py`
- `grading/private_grader/qa_scoring.py`
- `grading/schemas/qa-answer-v1.schema.json`
- `scripts/import_semifinal_qa_bundle.py`
- `tests/test_questions.py`
- `tests/test_live_api.py`
- `grading/tests/test_qa_scoring.py`

## 11. 尚未完成的发布条件

1. 组委会尚未对 12 道题的 Private GT 完成人工审核与签批。
2. 正式题尚未从 `SEMIFINAL_PREVIEW` 切换为可发布状态。
3. AM/PM 场次尚未配置实际开放时间并启用。
4. 本轮未向 17 台选手机下发，也未开放正式排行榜，符合任务边界。

正式发布前建议仅执行“人工审核 → 签名备份 → 受控切换状态 → 使用非组委会测试队做一次开场验收”，不需要重新设计当前系统。
