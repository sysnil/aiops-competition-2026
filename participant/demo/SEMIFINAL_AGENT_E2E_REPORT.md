# 半决赛默认 Agent 全链路验收报告

## 1. 结论

`192.168.51.50:/opt/aiops-competition/participant-starter/` 已适配新版半决赛查询、绘图与三问作答流程。默认 Starter 可以：

1. 从 51 号机 Question API 自动发现本队可见题目。
2. 仅通过真实 Public API 查询 Metrics / Logs / Traces。
3. 按冻结口径生成真实 PNG、Chart Data 和三个答案。
4. 生成 QA v1 `answer.json`，完成 Schema 与图片 SHA-256 校验。
5. 调用现有 `submit.py` 提交，并获取确定性预览评分。

实测结果：

- EX-P01、EX-P02 均为 10/10。
- Baseline 连续运行 Q01～Q10：10/10 题通过，总分 100/100。
- LLM Agent 连续运行 Q01～Q10：10/10 题通过，总分 100/100。
- 22 组 Agent/Skill/MCP/QA 单元测试全部通过。
- 正式题测试使用绑定 50 号机的组委会隔离身份，结果均为 `PREVIEW_COMPLETED`，不进入正式排行榜。

## 2. Agent 实现

新增 `--task qa` 与两种执行方式：

- `--agent baseline`：确定性 Python 查询、聚合、绘图和作答。
- `--agent llm`：模型先仅读取公开 Question 生成执行计划，然后由同一套确定性 Python 计算数值。LLM 不会猜测图表点或答案。

`--question all` 会读取 Question API 列表并连续运行所有可见题目。每题使用独立目录、独立 API Client 和独立提交文件，防止题目串数据、图表覆盖和请求计数污染。

对大数据题，QA 模式的默认单题请求预算为 220，原异常检测 Agent 仍保持独立的 55 次默认预算。HTTP 429 使用有界 5/10/20/30 秒退避，最多 8 次重试，不通过无限请求掩盖限流。

## 3. 关键统计算法修复

- 所有时间使用 UTC，窗口左闭右开。
- 以 Question API 返回的 `bucket_origin` 为 60 秒桶起点，不再默认使用自然整分钟。
- p95 使用 `ceil(0.95*n)` 的 nearest-rank 元素，不再使用 linear percentile。
- CPU Counter 按实体与 Labels 分组后差分，Reset 负增量被跳过。
- 计数题空桶填 0；有效样本不足的统计桶保持 `null`。
- 内存换算 MiB；调用边使用 Parent Span / Service 关系。
- 多实体题分别生成独立 Series，比较时使用绝对差与最早并列时刻。

上述规则均来自 Public Question 字段和通用算法，代码中没有 Q01～Q10 的固定答案、私有 GT 数值或隐藏时间。

## 4. 练习题实测

| Question | Case | Public 记录 | 图表点 | 得分 | Submission ID |
|---|---|---:|---:|---:|---|
| EX-P01 | SF-001 | 240 | 60 | 10/10 | `SUB-CE411CF2B27F44DC9931` |
| EX-P02 | SF-002 | 195 | 60 | 10/10 | `SUB-9872055965864727AEF3` |

产物路径：

- `/opt/aiops-competition/participant-starter/runs/semifinal-e2e/ex-p01/`
- `/opt/aiops-competition/participant-starter/runs/semifinal-e2e/ex-p02/`

## 5. Baseline 十题连续运行

| Question | Case | 记录 | 点 | HTTP 尝试 | 退避重试 | 计算耗时/s | 得分 |
|---|---|---:|---:|---:|---:|---:|---:|
| Q01 | SF-003 | 120 | 30 | 2 | 0 | 0.867 | 10 |
| Q02 | SF-003 | 120 | 30 | 2 | 0 | 0.417 | 10 |
| Q03 | SF-003 | 240 | 60 | 3 | 0 | 0.574 | 10 |
| Q04 | SF-005 | 9,993 | 30 | 11 | 0 | 3.650 | 10 |
| Q05 | SF-005 | 9 | 30 | 2 | 0 | 0.407 | 10 |
| Q06 | SF-005 | 58 | 30 | 2 | 0 | 0.444 | 10 |
| Q07 | SF-010 | 36,413 | 30 | 42 | 4 | 78.926 | 10 |
| Q08 | SF-010 | 117,779 | 30 | 126 | 7 | 128.382 | 10 |
| Q09 | SF-010 | 18,946 | 30 | 20 | 0 | 6.158 | 10 |
| Q10 | SF-010 | 154,232 | 60 | 167 | 11 | 181.597 | 10 |

总分 100/100，题内计算耗时合计 401.422 秒。所有提交均为 `PREVIEW_COMPLETED`。

完整产物：`/opt/aiops-competition/participant-starter/runs/semifinal-e2e/baseline-all-10/`

## 6. LLM Agent 十题连续运行

实际模型 ID：`deepseek-v4-flash`。每道题在查询前发起一次成功的公开题面规划调用。

| Question | 记录 | 点 | HTTP 尝试 | 退避重试 | 计算耗时/s | Token | 得分 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Q01 | 120 | 30 | 3 | 0 | 0.920 | 1,965 | 10 |
| Q02 | 120 | 30 | 3 | 0 | 0.432 | 3,677 | 10 |
| Q03 | 240 | 60 | 4 | 0 | 0.579 | 4,324 | 10 |
| Q04 | 9,993 | 30 | 12 | 0 | 3.616 | 1,512 | 10 |
| Q05 | 9 | 30 | 3 | 0 | 0.478 | 3,043 | 10 |
| Q06 | 58 | 30 | 3 | 0 | 0.435 | 2,126 | 10 |
| Q07 | 36,413 | 30 | 39 | 0 | 13.529 | 2,095 | 10 |
| Q08 | 117,779 | 30 | 126 | 6 | 106.728 | 2,053 | 10 |
| Q09 | 18,946 | 30 | 24 | 3 | 6.076 | 2,981 | 10 |
| Q10 | 154,232 | 60 | 165 | 8 | 146.001 | 2,977 | 10 |

模型服务商返回的真实 usage 合计：

- prompt tokens：7,845
- completion tokens：18,908
- total tokens：26,753
- 成功规划调用：10
- 超时后成功重试的失败尝试：2（Q01、Q09）
- 模型 API 调用累计耗时：347.565 秒
- 题内确定性计算耗时合计：278.794 秒
- 总分：100/100

`summary.json` 的 `elapsed_seconds` 仅计算题内查询、处理和绘图，不包含前置 LLM 规划；因此报告将两部分分开呈现，没有将它们误报为单一总墙钟时间。

完整产物：`/opt/aiops-competition/participant-starter/runs/semifinal-e2e/llm-all-10/`

## 7. 分页、Schema 与图片完整性

- Q01～Q10 的原始查询记录使用规范 JSON 摘要去重检查，每题重复数均为 0。
- 大数据题 Q08 读取 117,779 条，Q10 读取 154,232 条，完整分页后仍获得满分。
- 练习、Baseline 和 LLM 共 22 份 `answer.json` 均通过 QA v1 JSON Schema。
- 22/22 张 `chart.png` 均具有合法 PNG 文件头，文件 SHA-256 与 `answer.json` 一致，Base64 解码后与磁盘文件字节完全一致。
- 22/22 份答案均恰好包含 3 个问题结果。

## 8. 连续运行与隔离

Baseline 和 LLM 各在同一进程中连续完成十题。每题的下列文件位于独立子目录：

- `answer.json`
- `chart.png`
- `chart_data.json`
- `query_records.json`
- `api_requests.jsonl`
- `summary.json`
- `submission.json`
- `submit.log`
- LLM 模式额外保存 `llm_qa_plan.json` 和 `llm_calls.jsonl`

实测未出现题目串数据、图表覆盖、跨题请求计数累积或重复提交冲突。

## 9. 运行方法

默认 Baseline 连续执行本队可见的全部半决赛题：

```bash
cd /opt/aiops-competition/participant-starter
source /opt/aiops-competition/.venv/bin/activate
set -a
source /etc/aiops-competition/team.env
set +a

python main.py \
  --task qa \
  --agent baseline \
  --mode semifinal \
  --question all \
  --base-url http://192.168.51.51:8080 \
  --run-dir runs/semifinal-all \
  --submit
```

LLM 规划对照只需将 `--agent baseline` 替换为 `--agent llm`。若场次未开放或本队无可见题目，程序会显式返回鉴权/场次错误或 `NO_VISIBLE_QUESTIONS`，不会绕过 51 号机权限。

## 10. 主要修改文件

- `main.py`
- `qa_solver.py`
- `client/public_api.py`
- `schemas/qa-answer-v1.schema.json`
- `tests/test_qa_solver.py`
- `tests/test_api_retry.py`
- `scripts/qa_negative_fixtures.py`
- `README.md`
- `PARTICIPANT_HANDBOOK.md`

原 Baseline、LLM、Skill、MCP 和异常检测 Agent 代码均保留。QA 模式不会自动调用面向异常检测/决赛的 Skill 或 MCP，以免将不相关推理混入精确统计题；对应模块的现有单元测试仍全部通过。

## 11. 当前限制与下一步

- 正式题和 GT 尚未人工批准，所有 100 分结果都是组委会隔离内部预览成绩。
- Practice 仍为 CLOSED，Semifinal AM/PM 仍为 NOT_OPEN。
- 本轮只部署到 50 号测试机，未向 17 台选手机复制。
- 开赛前应在正式发布后，使用一支普通测试队的真实场次身份再做一次“可见列表 → 十题 → 正式次数 → 排行榜”开场验收。

验收结束时 50 号机内存 15 GiB，可用约 14 GiB，系统盘可用约 81 GiB。
