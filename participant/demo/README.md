# 院智能运维技能大赛 Participant Agent Starter Kit

这是一套可直接运行、也可完全改写的参赛 Agent 参考实现。默认使用真实 LLM 执行
Plan–Act–Observe–Verify 循环，模型会自主选择 Public API、统计检测器、Skill 和 MCP
工具。确定性 Baseline 仍完整保留，可用于对照、断网测试和算法开发。

Starter 只访问持队伍 Token 的只读 Public API，不读取 51 服务器磁盘、Staging、Private GT、注入计划，
也不提供任意 SQL 或写入能力。初次参赛请先阅读 `PARTICIPANT_HANDBOOK.md`。

## 新版比赛任务

- 半决赛查询题：读取已发布 Question，对真实 Public 数据分钟聚合，产生折线图和三个精确答案。每题 10 分，满分 100 分。
- 异常检测 Agent：原有 Baseline / LLM / Skill / MCP 链路完整保留，用于练习和决赛自主分析能力。
- 决赛 PK：只能读取组委会为本队和本机 IP 开放的轮次数据；未开放时显式拒绝。

练习查询题示例：

```bash
python main.py \
  --task qa \
  --mode practice \
  --case SF-001 \
  --question P-Q01 \
  --base-url http://192.168.51.51:8080 \
  --run-dir runs/P-Q01

python submit.py \
  --mode practice \
  --case SF-001 \
  --answer runs/P-Q01/answer.json \
  --server http://192.168.51.51:8080
```

运行会保存 `chart.png`、`chart_data.json`、`query_records.json`、`answer.json`
和 `summary.json`。`answer.json` 内嵌同一张 PNG 及 SHA-256，服务端会同时校验数据点、图片和三问答案。

当前场次开放后，可以不预先写题号，让 Starter 自动发现并连续完成本队可见的全部题目：

```bash
python main.py \
  --task qa \
  --agent baseline \
  --mode semifinal \
  --question all \
  --base-url http://192.168.51.51:8080 \
  --run-dir runs/semifinal-all \
  --submit
```

`--question all` 只处理 Question API 对本队实际返回的题目；题目未发布或场次未开放时不会绕过服务端权限。QA 模式默认每题最多 220 次 HTTP 尝试，以便在每页 1,000 条的限制下完整读取大数据题；可用 `--qa-request-budget` 调整。原异常检测 Agent 仍独立使用 `--request-budget`。

将 `--agent baseline` 换为 `--agent llm` 后，模型会先仅基于公开题面生成执行计划；图表点和三个精确答案仍由相同的确定性 Python 算法计算，不让 LLM 猜数值。

## 三种运行模式

| 模式 | 命令行 | LLM | Skill / MCP | 用途 |
|---|---|---:|---:|---|
| A Baseline | `--agent baseline --no-skills --no-mcp` | 否 | 否 | 确定性参考与对照 |
| B LLM Agent | `--agent llm --no-skills --no-mcp` | 是 | 否 | 验证 LLM 规划和原生 Tool Calling |
| C LLM + Skill + MCP | `--agent llm` | 是 | 是 | 默认推荐的完整模式 |

`--agent` 默认为 `llm`。如果模型不可用，程序会显式输出 `LLM_UNAVAILABLE`
并以非零状态退出，不会悄悄把 Baseline 冒充成 LLM Agent。

## 1. 配置选手 LLM

选手模型配置与组织方 Judge 完全独立。不要在源码、Prompt、日志、Git 或答案中写入
API Key。从示例创建受保护的配置：

```bash
sudo install -m 600 configs/agent-llm.env.example /etc/aiops-competition/agent-llm.env
sudo editor /etc/aiops-competition/agent-llm.env
```

```dotenv
AIOPS_AGENT_LLM_BASE_URL=https://provider.example/v1
AIOPS_AGENT_LLM_MODEL=your-model-id
AIOPS_AGENT_LLM_API_KEY=your-private-key
```

也可用同名环境变量覆盖文件。默认读取 `/etc/aiops-competition/agent-llm.env`，或使用
`--llm-env-file /path/to/file`。日志只记录模型 ID、耗时、选择的工具和 Token 用量，绝不记录 Key。

## 2. 快速开始

在参赛机 `192.168.51.50` 上：

```bash
cd /opt/aiops-competition/participant-starter
source /opt/aiops-competition/.venv/bin/activate
set -a
source /etc/aiops-competition/team.env  # 样板机测试身份；参赛机使用本队受保护的配置
set +a

python main.py \
  --agent llm \
  --mode practice \
  --case SF-001 \
  --base-url http://192.168.51.51:8080 \
  --question-file examples/question.txt \
  --skills-dir skills \
  --mcp-config configs/mcp_servers.json \
  --run-dir runs/my-llm-run
```

Baseline 对照：

```bash
python main.py \
  --agent baseline --no-skills --no-mcp \
  --mode practice \
  --case SF-001 \
  --base-url http://192.168.51.51:8080 \
  --question-file examples/question.txt \
  --run-dir runs/my-baseline-run
```

未指定 `--run-dir` 时，会在 `runs/` 下新建 UTC 时间戳目录。不要让多次运行共用同一
目录，否则 JSONL 日志会混合。

## 3. LLM Agent 工作流程

1. 读取自然语言题目、Case Metadata、压缩后的公开 Topology 和 Anomaly Taxonomy。
2. 调用模型制定初始调查计划，保存为 `llm_plan.md`。
3. 探测 OpenAI-compatible 模型是否支持原生 Tool Calling；不支持时使用经 Schema
   验证的结构化动作。
4. 模型在有界循环中决定 Service/Pod、UTC 窗口、Trace ID，以及何时运行检测器、
   Skill 或 MCP。
5. 程序执行工具，检查 Case ID，限制分页/时间/请求数，只把统计摘要和少量代表
   记录给模型。
6. 检测器把真实公开记录注册成 `cand-NNN`。LLM 最终只能选择已存在的候选和
   公开 Anomaly Taxonomy 枚举，不能填造证据，也不需要猜测注入机制或根因。
7. 程序组装 `answer.json`，并执行 v3 JSON Schema 和 Public 证据引用校验。

轮数、API 请求、分页、单次超时、总时间和返回长度都受限。完整模式将 Public API
总预算统一计入主 Agent 和 MCP 内部查询，默认上限 55（主 Agent 41 + MCP 预留 14）；
每次运行最多执行一次 Trace Diagnostics MCP。接近上限时程序强制
模型基于已验证候选收敛，不会无限循环。

半决赛 v3 最多可提交 16 个独立异常事件，与服务端 GT Schema 上限一致；每事件仍最多
20 条证据，答案文件大小限制不变。历史 v2 上限仍为 10，不做隐式扩容或转换。

## 4. Tool Registry

- 数据：`list_cases`、`get_case_metadata`、`get_topology`、`get_anomaly_taxonomy`、
  `query_metrics`、`query_logs`、`query_traces`、`get_trace`。
- 统计：`analyze_metric_anomaly`、`analyze_log_anomaly`、`analyze_trace_latency`、
  `correlate_anomalies`。
- 扩展：`run_skill`、MCP `analyze_edge_latency`。
- 收敛：`finalize_answer`。

每次模型选择的工具、参数、状态、耗时和结果摘要都会写入 `agent_tool_calls.jsonl`。

## 5. Skill 与 MCP

`skills/counter-rate-analysis/SKILL.md` 在启动时被发现。当 LLM 观测到 Counter 样本时，
它可选择 `run_skill`；真实 handler 会按标签分组计算速率、跳过 Reset 和异常间隔。
`skill_usage.json` 分别记录 discovered、selected 和 executed。

`mcp_servers/trace_diagnostics.py` 通过 stdio MCP 暴露 `analyze_edge_latency`，对真实父子 Span 边
比较基线和分析窗口的 median/p95。`mcp_calls.jsonl` 记录 discovery、参数、耗时和结果。
MCP 不可用时会记录失败，不会生成伪造结果。

## 6. 运行产物

- `answer.json`：半决赛 v3 最终答案，描述可观测异常现象、实体、时间和证据。
- `llm_plan.md`：模型初始调查计划。
- `llm_calls.jsonl`：LLM 调用、工具选择、Token 用量和重试。
- `agent_tool_calls.jsonl`：工具参数、状态、耗时和结果摘要。
- `api_requests.jsonl`：Public API 请求 ID、状态、耗时和重试。
- `skill_usage.json`、`mcp_calls.jsonl`：扩展真实发现/选择/执行记录。
- `evidence_validation.json`：证据请求 ID 和记录 SHA-256 回查。
- `summary.json`：模型、请求/工具数、耗时、Token、异常数和校验结果。
- `debug.json`、`execution.log`：调试状态和失败原因。

## 7. 修改与扩展

- LLM 循环、Prompt 和工具 Schema：`agent/llm_core.py`。
- OpenAI-compatible 客户端：`agent/llm_client.py`。
- Baseline：`agent/core.py`。
- Public API 工具：`agent/tool_registry.py`、`client/public_api.py`。
- Metrics / Logs / Traces 算法：`detectors/`。
- Skill：复制 `skills/counter-rate-analysis/`，修改 `SKILL.md` 的 `name`、`description`和 `handler`。
- MCP：在 `configs/mcp_servers.json` 中注册新 stdio Server。

不要在 Prompt 中告诉模型故障数量、实体、时间或类型。可替换模型、Prompt、算法和停止
策略，但建议保留请求预算、工具输入验证、证据回查和显式失败语义。

## 8. 测试与提交

```bash
PYTHONPATH=. /opt/aiops-competition/.venv/bin/python -m unittest discover -s tests -v
```

提交仍使用原有 `submit.py`：

```bash
python submit.py \
  --mode practice \
  --case SF-001 \
  --answer runs/my-llm-run/answer.json \
  --server http://192.168.51.51:8080
```

查询和提交都必须使用每队独立的 `AIOPS_TEAM_TOKEN`。`practice` 映射
`/v1/practice`，`semifinal` 映射 `/v1/semifinal`；默认为更安全的练习模式。
`PREVIEW_COMPLETED` 只代表使用
待审核草案 GT 的预评测，不是正式成绩，也不进入排行榜。历史 v2 答案仍可由 `submit.py`
原样提交，但不会自动转换为 v3。
