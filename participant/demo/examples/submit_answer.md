# 自动提交与评分使用教程

这份教程面向第一次参加智能运维比赛的选手。半决赛只要求识别 Public Metrics、Logs、Traces 中能观察到的异常现象，不要求判断注入机制或根因。自动评测接收 Agent 生成的结构化答案，核验公开证据，并在组织方已有审核 GT 时给出正式成绩。

## 1. 进入环境

```bash
cd /opt/aiops-competition/participant-starter
source /opt/aiops-competition/.venv/bin/activate
```

比赛方会把每队独立提交凭证放入受保护配置。不要在命令行输入密钥，不要把配置提交到 Git，也不要把它复制到 `runs/`。默认位置是：

```text
/etc/aiops-competition/submission.env
```

如果组织方使用其他位置，可通过 `--credential-file` 指定。也可以临时使用环境变量 `AIOPS_SUBMISSION_TOKEN`，但不要将它写入脚本。

## 2. 运行基础 Agent

```bash
python main.py \
  --case SF-001 \
  --base-url http://192.168.51.51:8080 \
  --run-dir runs/my-first-run \
  --team-id my-team-agent
```

完成后重点查看：

```bash
python -m json.tool runs/my-first-run/answer.json
python -m json.tool runs/my-first-run/evidence_validation.json
```

`answer.json` 顶层应包含 `schema_version=3.0` 和
`task_type=SEMIFINAL_ANOMALY_DETECTION`。每条异常包含实体类型、实体标识、公开
Anomaly Taxonomy 中的 `anomaly_type`、现象描述、UTC 起止时间，以及至少一条从
Public API 获得的 Metrics/Logs/Traces 证据。不要填写 `suspected_fault_type`。

如果现象确实不属于公开枚举，可使用 `OTHER_OBSERVABLE_ANOMALY`，但该类型需要人工复核，不能替代认真分类。

## 3. 本地校验与提交

`submit.py` 会在发出网络请求前执行 JSON Schema 校验，因此本地格式错误不会消耗正式提交次数：

```bash
python submit.py \
  --case SF-001 \
  --answer runs/my-first-run/answer.json \
  --server http://192.168.51.51:8080 \
  --output runs/my-first-run/grading-result.json
```

脚本会：

1. 检查文件大小和 JSON；
2. 根据 `schema_version` 校验 v3 或历史 v2 Schema、Case ID、时间和公开枚举；
3. 对规范 JSON 计算 SHA-256；
4. 使用受保护凭证提交；
5. 自动生成幂等键；
6. 有限重试网络错误和 429/502/503/504；
7. 轮询评测状态；
8. 在终端显示结果，并保存完整公开结果 JSON。

同一答案重复运行默认使用同一个幂等键，服务端返回原任务，不会重复评分。若只是想继续查询，可再次执行同一命令。

## 4. 理解状态

- `COMPLETED`：使用已审核 GT 完成正式评分；命令行显示 40+20+20+20 分项和总分。
- `PREVIEW_COMPLETED`：使用隔离的待审核草案 GT 完成预评测；不是正式成绩，不进入排行榜。
- `PENDING_GT_APPROVAL`：题目还没有人工审核通过的 GT，没有正式成绩。
- `PENDING_JUDGE`：LLM Judge 暂不可用、超时或返回无效结果，没有被自动记为 0 分。
- `PENDING_REVIEW`：需要人工复核，没有最终分。
- `REJECTED`：格式或证据真实性不合格，请按公开错误修改。
- `FAILED`：服务内部故障，本次没有正式成绩，联系裁判。

客户端等待超时不等于 0 分；任务可能仍在服务器中执行。

## 5. 修改后再次运行

启用自己的 Skill：

```bash
python main.py \
  --case SF-001 \
  --base-url http://192.168.51.51:8080 \
  --skills-dir skills \
  --run-dir runs/skill-v3
```

启用 MCP：

```bash
python main.py \
  --case SF-001 \
  --base-url http://192.168.51.51:8080 \
  --skills-dir skills \
  --mcp-config configs/mcp_servers.json \
  --run-dir runs/skill-mcp-v3
```

分别提交两个运行目录中的 `answer.json`，再比较 `grading-result.json` 的四个分项。只有 `COMPLETED` 才是正式结果；`PREVIEW_COMPLETED` 只能用于验证流程。正式提交次数有限，先比较 `debug.json`、`api_requests.jsonl` 和 `evidence_validation.json`，确认确有改进后再提交。

## 6. 历史 v2 答案

`submit.py` 会按答案自身的 `schema_version` 选择校验器。历史 v2
`suspected_fault_type` 答案可原样提交和查询，v3 `anomaly_type` 答案走半决赛异常检测规则；两者不会被静默互转。新作答请始终使用 v3。

## 7. 常见错误

- `SUBMISSION_AUTH_REQUIRED/FAILED`：凭证缺失或错误；不要改用 `X-Team-ID`。
- `ANSWER_HASH_MISMATCH`：文件在计算哈希后发生变化，重新提交当前文件。
- `EVIDENCE_NOT_FOUND`：摘要无法在本题 Public 数据中找到。
- `EVIDENCE_RECORD_MISMATCH`：证据字段被修改或引用错记录。
- `EVENT_OUTSIDE_CASE_WINDOW`：事件时间超出 Metadata 的题目窗口。
- `ANOMALY_TYPE_NOT_ALLOWED`：`anomaly_type` 不在公开 Anomaly Taxonomy 中。
- `DUPLICATE_EVIDENCE`：同一事件重复引用同一监控记录。
- `SUBMISSION_LIMIT_REACHED`：正式评分次数已用完；本地校验和 DEMO 不计入该次数。

任何状态下都不要尝试访问服务器文件、提交 SQL、猜测其他队伍的 submission ID 或获取 Private GT。
