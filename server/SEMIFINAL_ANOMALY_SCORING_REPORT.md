# 半决赛多模态异常检测评分系统改造报告

生成日期：2026-10-15（Asia/Shanghai）  
最终状态：`SEMIFINAL_SCORING_DEMO_READY`

> 本状态表示 v3 提交、真实 Public 证据回查、LLM-as-Judge 和合成 GT 百分制评分闭环已经通过。SF-001 的 13 个事件仍是待人工审核草案，因此本报告中的 SF-001 分数仅为隔离预评测，不是正式成绩，不进入排行榜。当前环境没有已发布的 SF-002 数据或草案，也没有任何已审核的半决赛正式 GT，故不能标记为 `SEMIFINAL_SCORING_READY`。

## 1. 改造结论

半决赛任务已从“识别故障注入类型/根因”改为“识别 Metrics、Logs、Traces 中的独立可观测异常事件”。选手需要回答四件事：异常现象、异常实体、异常时间和 Public 证据；不要求判断 ServicePause、PodMemoryStress 等注入机制。

本次实际完成：

- 新建独立 Anomaly Taxonomy，保留 Fault Taxonomy 给历史 v2 和未来决赛 RCA。
- 新建 answer v3 和 anomaly GT v1 Schema；v3 不接受 `suspected_fault_type`。
- 改造确定性匹配、40+20+20+20 评分、多报扣分和审计版本。
- 将 LLM Judge 限定为只评价证据质量，不允许修改另外三个维度。
- 升级 50 机 Baseline、LLM、Skill、MCP、`submit.py` 和选手文档。
- 升级 51 机 Taxonomy API、提交路由、异步 Worker、私有评分服务和 SQLite 迁移。
- 使用合成 GT 完成真实 100 分闭环；使用 SF-001 草案完成明确标记的 PREVIEW。
- 保留旧 Public API、旧 v2 Schema、Fault Taxonomy 和已有 v2 提交记录。

## 2. 新异常类别 Taxonomy

版本文件：`grading/schemas/anomaly-taxonomy-v1.json`  
公开接口：`GET /v1/anomaly-taxonomy`  
版本：`1.0`，任务类型：`SEMIFINAL_ANOMALY_DETECTION`

最终发布 11 个边界相对清晰的可观测类别：

| 类型 ID | 中文名称 | 主要实体 |
|---|---|---|
| `REQUEST_LATENCY_INCREASE` | 请求延迟升高 | Service |
| `REQUEST_ERROR_INCREASE` | 请求错误增多 | Service |
| `REQUEST_TIMEOUT_INCREASE` | 请求超时增多 | Service、CallEdge |
| `CPU_USAGE_INCREASE` | CPU 使用升高 | Service、Pod、Node、MetricSeries |
| `MEMORY_USAGE_INCREASE` | 内存使用升高 | Service、Pod、Node、MetricSeries |
| `POD_RESTART_INCREASE` | Pod 重启增多 | Pod、Service |
| `LOG_ERROR_BURST` | 错误日志突增 | Service、Pod、Node |
| `TRACE_LATENCY_INCREASE` | Trace 延迟升高 | Service |
| `CALL_EDGE_LATENCY_INCREASE` | 调用边延迟升高 | CallEdge |
| `CALL_EDGE_ERROR_INCREASE` | 调用边错误增多 | CallEdge |
| `OTHER_OBSERVABLE_ANOMALY` | 其他可观测异常 | 全部公开实体类型 |

在用户建议的九类基础上，结合 SF-001 的 Trace/Span 与调用边字段，补充了调用边延迟、调用边错误两个类别，避免把边级异常强行压成 Service 级。每个类型均包含 ID、中文名、定义、可接受的观测证据和允许实体类型。`OTHER_OBSERVABLE_ANOMALY` 要求具体量化描述且设置 `requires_human_review=true`，不能作为不确定分类的兜底答案。

原 `grading/schemas/fault-taxonomy-v1.json` 和 `GET /v1/fault-taxonomy` 未删除、未覆盖，也未在半决赛默认 Prompt 中暴露。

## 3. answer.json v3 Schema

文件：`grading/schemas/answer-v3.schema.json`；50 机对应 `schemas/answer.schema.json`。核心字段：

```json
{
  "case_id": "CASE-ID",
  "schema_version": "3.0",
  "task_type": "SEMIFINAL_ANOMALY_DETECTION",
  "anomalies": [
    {
      "anomaly_type": "LOG_ERROR_BURST",
      "anomaly_description": "在给定窗口内观察到错误日志突增",
      "entity_type": "Service",
      "entity_id": "example-service",
      "start_time": "2030-01-01T00:10:00Z",
      "end_time": "2030-01-01T00:15:00Z",
      "evidence": []
    }
  ]
}
```

仍保留并强制执行：Public evidence、`record_sha256`、`request_id`、UTC/时区、Case 时间窗、事件数/证据数/字段长度、120000 字节客户端限制、128 KiB 网关限制、重复证据拒绝和 Public Parquet 逐字段回查。v3 明确拒绝 `suspected_fault_type`，也不会把 v2 字段自动转换为 `anomaly_type`。

## 4. 新异常 GT Schema 与审核边界

文件：`grading/schemas/anomaly-gt-v1.schema.json`。事件字段包括：

- `gt_event_id`
- `anomaly_type` 与 `accepted_anomaly_types`
- `entity_type`、`entity_id`、`accepted_entity_aliases`
- 可选的上层 Service 半分规则
- `observable_start_time`、`observable_end_time`
- `evidence_rubric`
- 事件与文件级 `review_status`
- `gt_version`、`rubric_version`、`matching_rule_version`
- 可冻结的 `time_tolerance_seconds`

正式评分只读取 `review_status=APPROVED` 的正式 GT。SF-001 源草案保持在原 staging 位置，不被覆盖；只读构建脚本 `grading/scripts/build_sf001_preview_gt.py` 将其转换到独立 preview 目录，并强制写入：

- `classification=DRAFT_PREVIEW_ONLY`
- `review_status=DRAFT`
- 英文警告：不可用于正式分数或排行榜
- 源草案 SHA-256

校验值：

- 源草案 SHA-256（构建前后相同）：`ff48d969e676d01ff1134809d23ec225f0f1e9893709e928f9f707be04fd6f9a`
- 隔离预览 GT SHA-256：`71b040e0d5f52595b667bc1cdc89770572ea951212d1b28548ff33753f71c0d1`
- Public Manifest SHA-256（复核时）：`ffd207aa0fba0e2fa1819cd148635fcd332c33869dce58de66ef566aba324c56`

预览 GT 保留 13 个独立可观测事件，没有强制压缩为两个故障级事件。它仍需按“异常真实性、背景波动、重复合并、实体、时间、Public 证据、等价类型、Rubric”进行人工双人审核后，才能冻结成正式版本。

## 5. 匹配与评分实现

v3 采用可复现的确定性最大权重一对一匹配：

- 实体身份、允许的实体别名/上层关系和时间邻近是主要匹配依据。
- 异常类型只作为同等候选时的 1 分弱排序，不是匹配门槛。
- 分类错误但实体、时间和证据正确时，后三项仍可得分。
- 一个提交最多匹配一个 GT，一个 GT 最多匹配一个提交。
- 完全重复候选去重且不重复扣分。
- 同实体、相近时间但不同异常现象不被错误去重。
- 匹配顺序改变不影响结果。

v3 评分公式已按要求实现：

```text
raw_score = Σ(异常类别 40 + 实体 20 + 时间 20 + 证据 20) / GT事件数N
penalty = 未匹配且非重复的独立多报数 × 5
final_score = max(0, min(100, raw_score - penalty))
```

实体命中允许的上层 Service 得 10/20；时间两个边界分别计 10 分，容差从 GT 冻结字段读取，当前预览为 ±120 秒。证据等级为 FULL/PARTIAL/NONE，分别计 20/10/0。内部 `grading_mode`、SQLite、对外 breakdown 与 CLI 使用一致字段；v2 仍走历史故障类型评分字段。

## 6. LLM-as-Judge 改造

Judge System Prompt 现在明确：

- 只判断已验证 Public 证据是否支持提交的可观测异常现象。
- 检查实体、时间、异常变化、对照/统计依据和描述一致性。
- 不推断注入机制、根因、最初传播节点或真实故障数量。
- 只返回 FULL/PARTIAL/NONE、固定比例、理由、不确定性和人工复核标志。
- 不得修改异常类别、实体或时间的确定性得分。

原有 JSON 结构校验、比例一致性校验、Prompt Injection 隔离、长度限制、超时、有限重试和 `PENDING_JUDGE` 失败语义均保留。真实合成测试中，单条 ERROR 日志被 Judge 判为 PARTIAL，而不是错误地判定为“日志突增”；修正合成结论为具有显式 deadline-exceeded Trace 的 `REQUEST_TIMEOUT_INCREASE` 后获得 FULL，证明 Judge 实际检查语义而不是固定给满分。

## 7. 50 机 Agent Starter 更新

部署目录：`/opt/aiops-competition/participant-starter/`

- Baseline、纯 LLM、LLM+Skill+MCP 均输出 v3。
- System Prompt 和题目说明只要求异常现象、实体、时间和证据。
- Tool Registry 新增 `get_anomaly_taxonomy`；`get_fault_taxonomy` 代码保留给未来 `FINAL_ROOT_CAUSE_ANALYSIS`。
- LLM 最终工具参数改为 `anomaly_type` 和 `anomaly_description`，并按公开枚举校验。
- Baseline 将指标、日志、Trace 检测结果映射到可观测类型；Counter 不按普通 Gauge 处理。
- LLM 仍自主选择查询、统计检测器、Skill、MCP 和停止时机。
- 模型不可用仍显式返回 `LLM_UNAVAILABLE`，不会把 Baseline 冒充为 LLM Agent。
- `submit.py` 根据显式 `schema_version` 选择 v3 或历史 v2 校验器，不做语义转换。
- README、题目文本和提交教程已同步更新。

LLM 配置仍与 51 机组织方 Judge 完全隔离，报告和代码中没有写入任何 API Key。

## 8. 51 机 API、Worker 与提交客户端更新

部署目录：`/opt/aiops-contest-server/`

- 新增匿名只读 `GET /v1/anomaly-taxonomy`；现场返回 11 个类型。
- `POST /v1/submissions` 同时识别 v3 与历史 v2，必须显式声明版本。
- v3 的 SF-001/SF-002 在无正式 GT 时进入隔离 `PREVIEW`；结果为 `PREVIEW_COMPLETED`。
- 合成 DEMO 使用独立 GT，结果消息明确“不是正式比赛成绩”。
- `GET /v1/submissions/{id}` 返回与版本一致的 `task_type`、breakdown、penalty、审计版本和公开状态，不返回事件级 GT、正确时间或匹配明细。
- SQLite 以迁移方式新增 `grading_mode` 与 `penalty`，没有重建或覆盖历史表。
- Worker 对 Judge 失败返回 `PENDING_JUDGE`，不自动记 0 或满分。
- CLI v3 标题和四个分项已改成“异常发现与现象识别、异常实体定位、异常时间定位、监控证据质量”，并显示多报扣分。

## 9. 单元与回归测试

51 机命令：

```bash
PYTHONPATH=. .venv/bin/python -m unittest discover -s grading/tests -v
```

结果：`45 tests, OK`，耗时 0.077 秒。覆盖：

- v3 正确答案、非法类型、缺描述、v2 故障字段拒绝、时区/越窗、字段/大小限制。
- 真实与伪造证据、错误摘要、重复证据、路径/SQL 字段攻击。
- 100 分、分类错但其他维度得分、上层实体半分、单边界时间、PARTIAL、漏报、多报、重复、13 个 GT、同实体多现象、顺序不变。
- Judge FULL/PARTIAL/NONE、比例越界、Prompt Injection、非法 JSON、超时、连接失败、错误实体/时间、v3 Prompt 不含故障字段。
- 团队隔离、DEMO 次数隔离和未完成任务恢复。

50 机命令：

```bash
PYTHONPATH=. /opt/aiops-competition/.venv/bin/python -m unittest discover -s tests -v
```

结果：`16 tests, OK`，耗时 1.089 秒。覆盖 Baseline v3、API 429 有界重试、检测器安全、证据引用、独立模型配置、LLM 不可用、Skill 和 MCP。

## 10. 三种 Agent 真实运行

三组均在 192.168.51.50 对真实 SF-001 Public API 运行，均通过 v3 Schema 与 Public 证据回查；没有读取 Private GT 或注入计划。

| 模式与目录 | 状态 | 模型/调用 | Public API | Skill/MCP | 耗时 | 输出 |
|---|---|---:|---:|---:|---:|---|
| Baseline `runs/semifinal-v3-baseline-accepted` | complete | LLM 0 | 52 | 0/0 | 8.560s | 5 个事件 |
| LLM `runs/semifinal-v3-llm` | complete | deepseek-v4-flash；13 次 LLM、30 次工具 | 27 | 0/0 | 151.693s | 1 个 `LOG_ERROR_BURST` |
| LLM+Skill+MCP `runs/semifinal-v3-full-accepted` | complete | deepseek-v4-flash；14 次 LLM、31 次工具 | 41（主 Agent 27 + MCP 14） | 1/1 | 193.918s | 1 个 `LOG_ERROR_BURST` |

LLM 模式使用原生 Tool Calling。纯 LLM 使用 179,913 tokens；完整模式使用 207,934 tokens。供应商未提供单价，故只记录 Token，不伪造成本。

完整模式的真实扩展记录：

- LLM 发现并主动选择 `counter-rate-analysis`；handler 实际执行。当前缓存样本中无可计算 Counter 序列，结果如实为 0，没有伪造候选。
- LLM 主动调用 MCP `analyze_edge_latency(frontend → product-catalog)`；MCP 查询真实 Public Trace，比较基线/分析窗口，得到 p95 比率 0.377334，并返回 `NOT_SUPPORTED`。LLM 没有把不支持的延迟假设写入最终答案。

这说明 Skill/MCP 不只是固定流程调用，也不会为了展示调用次数而制造异常。

## 11. 合成 GT 百分制闭环

使用 50 机 `submit.py` 经过 51 机真实 Nginx、认证、Schema 校验、Public 证据回查、异步队列、私有 Judge 和 SQLite 完成：

- 结果文件：`/opt/aiops-competition/participant-starter/runs/demo-v3-grading-result-100.json`
- 状态：`COMPLETED`，模式：`DEMO`
- 异常发现 40/40、实体 20/20、时间 20/20、证据 20/20
- 多报扣分 0，最终 100/100
- 公告明确：合成演示分数不是正式比赛成绩

另一次刻意使用“单条日志声称日志突增”的真实调用得到 90/100，其中证据为 10/20。这一结果用于验证 PARTIAL 路径，之后没有通过放宽 Prompt 获得满分，而是修正合成异常语义与证据。

## 12. SF-001 草案预评测及限制

以下均使用 13 事件 `DRAFT_PREVIEW_ONLY` GT，不是正式准确率：

| Agent 答案 | 状态 | 预览分数 | 说明 |
|---|---|---:|---|
| 纯 LLM | `PREVIEW_COMPLETED` | 6.922 | 仅表示与未审核草案的当前匹配结果 |
| LLM+Skill+MCP | `PREVIEW_COMPLETED` | 0.769 | 仅表示与未审核草案的当前匹配结果 |
| Baseline | `PENDING_JUDGE` | 无 | 5 个候选中的 Judge 处理未完整成功；保护逻辑没有自动给 0 |

CLI 对两个完成的预览均显示：“该分数不是正式比赛成绩，不进入排行榜。”这些分数不能用于宣称某种 Agent 更准确；草案审核、事件合并、Rubric 和容差冻结都可能改变结果。Baseline 的 `PENDING_JUDGE` 是当前真实外部模型稳定性/批量处理限制，应在正式比赛前通过任务级重试、可观测错误码和压力测试进一步验证，而不是降低安全校验。

## 13. 旧版兼容、安全与数据隔离

数据库迁移后历史 v2 记录仍完整存在：

- v2 `COMPLETED`：1
- v2 `PENDING_GT_APPROVAL`：4
- v2 `PENDING_JUDGE`：3

历史 v2 模型、Fault Taxonomy、评分字段和结果查询均保留；没有把 v2 答案改写成 v3。新产生的 v3 记录与其并存。

现场安全复核：

- `aiops-contest-api`、`aiops-private-grader-demo`、Nginx 均为 active。
- Public API 仅由 Nginx 监听 `192.168.51.51:8080`；FastAPI 为 `127.0.0.1:8000`，私有 grader 为 `127.0.0.1:9001`。
- GT、预览 GT、Judge 环境均为 `root:aiops-grader` 且目录 0750、文件 0640。
- 以 `aiops-api` 用户读取预览 GT 实测返回 `Permission denied`。
- SQLite 为 `aiops-api:aiops-api 0640`；答案历史未清空。
- Public Manifest 哈希已记录，未改写 Public 数据。
- 代码与文档扫描未发现硬编码 `sk-...` 密钥。
- 公开结果不包含 GT 事件、隐藏时间、私有 Rubric、Judge Prompt 或模型密钥。

部署前备份：

- 51 机：`/opt/aiops-contest-server/backups/semifinal-scoring-20261015/`
- 50 机：`/opt/aiops-competition/participant-starter/backups/semifinal-v3-20261015/`

## 14. 阻断、后续工作与正式就绪条件

当前阻断不是代码闭环，而是正式赛务数据：

1. SF-001 的 13 个草案事件尚未人工双人审核，不能转为 `APPROVED`。
2. 当前环境没有 SF-002 Public Case/GT 草案，无法执行其审核和练习题验收。
3. SF-003～SF-012 尚未发布，需按 30 分钟 Case 窗口逐题导入、审核和冻结 GT。
4. ±120 秒对短异常可能过宽，正式冻结前应按题目做敏感性验证。
5. Baseline 多事件预览出现一次 `PENDING_JUDGE`，正式赛前需完成 Judge 并发、超时和任务级重试压测。
6. 应建立签字/审计流程：两名审核人、GT SHA-256、版本、Rubric、容差、批准时间和回滚版本。

达到 `SEMIFINAL_SCORING_READY` 的最低条件：至少一个真实 Case 的异常 GT 完成人工批准和版本冻结；使用队伍凭证完成 v3 正式提交；返回 `COMPLETED`；复核四个分项、扣分、历史、权限、并发和不泄露 GT。完成这些条件之前，系统应继续保持 `SEMIFINAL_SCORING_DEMO_READY`。

## 15. 关键路径

51 机：

- 报告：`/opt/aiops-contest-server/SEMIFINAL_ANOMALY_SCORING_REPORT.md`
- Taxonomy：`/opt/aiops-contest-server/grading/schemas/anomaly-taxonomy-v1.json`
- answer v3 Schema：`/opt/aiops-contest-server/grading/schemas/answer-v3.schema.json`
- GT Schema：`/opt/aiops-contest-server/grading/schemas/anomaly-gt-v1.schema.json`
- 预览构建器：`/opt/aiops-contest-server/grading/scripts/build_sf001_preview_gt.py`
- 运维说明：`/opt/aiops-contest-server/grading/README.md`

50 机：

- Starter：`/opt/aiops-competition/participant-starter/`
- v3 Schema：`/opt/aiops-competition/participant-starter/schemas/answer.schema.json`
- 历史 v2 Schema：`/opt/aiops-competition/participant-starter/schemas/answer-v2.schema.json`
- 三组运行结果：`/opt/aiops-competition/participant-starter/runs/semifinal-v3-*`
- 合成满分结果：`/opt/aiops-competition/participant-starter/runs/demo-v3-grading-result-100.json`

