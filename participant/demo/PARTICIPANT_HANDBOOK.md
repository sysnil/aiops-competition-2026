# 院智能运维技能大赛选手参赛手册

版本：1.0  
适用环境：院智能运维技能大赛参赛样板机  
Starter Kit：`/opt/aiops-competition/participant-starter/`

> 本手册面向第一次接触智能运维、可观测性或 Agent 开发的选手。建议先完成“10 分钟快速开始”，确认环境正常，再阅读后面的概念和扩展章节。

---

## 1. 你需要完成什么

比赛会提供一个公开题目（Case）以及一段时间范围内的系统观测数据。你的程序需要：

1. 读取题目 Metadata 和服务拓扑。
2. 查询 Metrics、Logs、Traces 三类公开数据。
3. 从数据中发现异常候选。
4. 主动补充查询并验证证据。
5. 输出符合统一 JSON 格式的答案。

你提交的核心不是一段自然语言分析，而是一个可以被程序检查的 `answer.json`。每条异常都应说明：

- 哪个实体异常，例如 Service、Pod 或 MetricSeries。
- 异常类型，例如延迟升高、错误日志突增、Counter 速率异常。
- 异常发生的 UTC 时间范围。
- 支持结论的真实 Public API 证据。

Starter Kit 是参考实现，不是唯一答案。你可以修改规划逻辑、检测算法、证据排序、Skill、MCP Server，或者换成自己的 Agent 框架。默认 LLM 模式需要选手模型配置；没有 API Key 时可显式选择 `--agent baseline`。

### 1.1 新版半决赛：查询、图表与三问

半决赛每道题会明确给出数据集、目标实体、UTC 时间窗、聚合规则、图表要求和三个问题。选手程序必须从 Question API 读取实际定义，不要猜测目标 Pod、Service 或时间段。

每题建议评分结构为：图表 4 分，三个答案各 2 分。图表评分包括标题/实体、坐标轴/单位和数据点。系统采用确定性计算验证，不用 LLM 猜测数值是否正确。

```bash
cd /opt/aiops-competition/participant-starter
source /opt/aiops-competition/.venv/bin/activate

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

主要产物：

- `chart.png`：适合查看和投屏的折线图。
- `chart_data.json`：服务端可重算的数据点。
- `query_records.json`：本次查询得到的 Public 记录。
- `answer.json`：包含图表定义、PNG、SHA-256 和三问答案的正式提交文件。
- `summary.json`：请求数、记录数、图表点数和运行时间。

`P-Q01` 只是已发布练习题。正式题号必须以当时 Question API 返回的列表为准；未发布题不可见，也不能提交。

比赛场次开放后，可用下列命令自动读取 Question API 对本队可见的题目，逐题查询、绘图、回答三问并提交：

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

如需模型先解读题面并制定计划，将 `--agent baseline` 改为 `--agent llm`。精确统计、图表点和三问数值仍由确定性 Python 计算。默认的 `--qa-request-budget 220` 用于完整分页读取大数据题，不影响原异常检测 Agent 的 `--request-budget`。

---

## 2. 比赛环境和安全边界

### 2.1 两台服务器分别做什么

| 服务器 | 地址 | 用途 | 选手是否修改 |
|---|---|---|---|
| 参赛样板机 | `192.168.51.50` | 编写、运行和测试自己的 Agent | 可以在参赛工程内修改 |
| 比赛数据服务器 | `192.168.51.51:8080` | 提供 Token 认证的练习/正式 Public API | 不可以修改 |

参赛工程位于：

```text
/opt/aiops-competition/participant-starter/
```

Python 环境位于：

```text
/opt/aiops-competition/.venv/
```

### 2.2 可以做的事情

- 调用公开的只读 API。
- 修改 `participant-starter/` 中的代码和配置。
- 新增检测算法、Skill、MCP Server 和测试。
- 使用本地模型或自己合法配置的 LLM API。
- 保存自己的运行日志、调试信息和实验结果。

### 2.3 禁止做的事情

- 登录或修改比赛数据服务器内部文件。
- 读取 Parquet、DuckDB、Staging、Private GT 或故障注入计划。
- 绕过 API 直接访问数据文件。
- 对比赛服务器执行写入、删除或任意 SQL。
- 利用已知答案把故障实体、时间或数量硬编码进程序。
- 通过无限并发、无限重试等方式规避限流。
- 在源码、日志或提交文件中写入密码、Token、API Key。

所有题目查询和提交都需要本队 Bearer Token。队伍身份由服务端根据 Token 确定，
`X-Team-ID` 和来源 IP 都不能用来切换身份。不要在命令行、源码、日志或答案中显示 Token。

### 2.4 加载本队 Token 环境变量

组委会会在每台选手服务器上预置本队专用 Token，文件路径为：

```text
/etc/aiops-competition/team.env
```

选手不需要手工输入或复制 Token，也不应修改该文件。每次打开新的 Bash 终端后，执行：

```bash
if [[ -r /etc/aiops-competition/team.env ]]; then
  set -a
  source /etc/aiops-competition/team.env
  set +a

  if [[ -n "${AIOPS_TEAM_TOKEN:-}" ]]; then
    echo "本队身份凭据已加载"
  else
    echo "错误：凭据文件中没有 AIOPS_TEAM_TOKEN，请联系组委会"
  fi
else
  echo "错误：无法读取本队凭据文件，请联系组委会"
fi
```

`set -a` 会把配置文件中的变量导出给 Agent、`curl` 和 `submit.py` 等后续进程。该环境变量只在当前终端会话中有效；退出 SSH 或关闭终端后，下次登录需要重新加载。

出于安全原因，不要执行 `echo $AIOPS_TEAM_TOKEN`，也不要将 `team.env` 复制到参赛工程、Git 仓库、答案文件、日志或截图中。

---

## 3. 10 分钟快速开始

### 第一步：登录样板机

从比赛内网登录 `192.168.51.50`。用户名和密码以比赛组织方发放的信息为准，不要把密码写入脚本。

登录后进入工程目录：

```bash
cd /opt/aiops-competition/participant-starter
```

### 第二步：启用 Python 环境

```bash
source /opt/aiops-competition/.venv/bin/activate
python --version
```

如果不想激活环境，后续也可以直接使用：

```bash
/opt/aiops-competition/.venv/bin/python
```

### 第三步：加载 Token 并检查 API

```bash
set -a
source /etc/aiops-competition/team.env
set +a

curl -sS --fail-with-body \
  http://192.168.51.51:8080/healthz \
  | python3 -m json.tool

if [[ -n "${AIOPS_TEAM_TOKEN:-}" ]]; then
  curl -sS --fail-with-body \
    -H "Authorization: Bearer ${AIOPS_TEAM_TOKEN}" \
    http://192.168.51.51:8080/v1/practice/cases \
    | python3 -m json.tool
else
  echo "错误：未能加载本队 Token，请联系组委会"
fi
```

能看到健康状态和本队当前有权访问的题目列表，说明凭据、网络和 API 正常。如果返回 `AUTH_FAILED`，不要手工猜测或修改 Token，应重新加载 `team.env`；仍然失败时请向组委会提供响应中的 `request_id`。

### 第四步：先运行 Baseline

```bash
python main.py \
  --agent baseline --no-skills --no-mcp \
  --mode practice \
  --case SF-001 \
  --base-url http://192.168.51.51:8080 \
  --question-file examples/question.txt \
  --run-dir runs/my-first-run
```

说明：

- `--case`：题目 ID，不要把它写死在检测器中。
- `--agent`：`baseline` 为确定性对照，`llm` 为真实模型驱动。
- `--base-url`：公开 API 地址。
- `--question-file`：自然语言题目文件。
- `--run-dir`：本次运行的独立输出目录。
- `--mode practice|semifinal`：选择练习或正式命名空间，默认 `practice`。
- `--team-token`：显式凭证；推荐不填此项，而是使用 `AIOPS_TEAM_TOKEN`。
- `--team-id`：仅保留为本地运行标签，不参与服务端身份判定。

如果省略 `--run-dir`，程序会在 `runs/` 下创建 UTC 时间戳目录。

### 第五步：查看结果

```bash
python -m json.tool runs/my-first-run/answer.json
python -m json.tool runs/my-first-run/summary.json
python -m json.tool runs/my-first-run/evidence_validation.json
```

至少确认：

- `summary.json` 中 `status` 为 `complete`。
- `evidence_validation.json` 中 `valid` 为 `true`。
- `answer.json` 可以正常解析。
- 没有把示例实体或示例时间当成真实结果。

### 第六步：运行测试

```bash
PYTHONPATH=. python -m unittest discover -s tests -v
```

全部显示 `ok` 后，再开始修改代码。

---

## 4. 智能运维入门：先理解三类数据

智能运维不是“看到一个错误词就提交答案”，而是从多种观测数据中建立并验证异常假设。

### 4.1 Metrics：指标

Metrics 是随时间变化的数值，例如 CPU、内存、请求数、失败数、延迟等。一条指标通常包含：

- `timestamp`：采样时间。
- `metric_name`：指标名称。
- `value`：数值。
- `metric_type`：Gauge、Counter 或未知。
- `service`、`pod`、`node`：实体信息，可能为空。
- `labels`：区分不同序列的标签。

#### Gauge 是什么

Gauge 表示某个时刻的状态值，可以升高也可以降低，例如：

- 当前内存使用量。
- 当前温度。
- 当前队列长度。

Gauge 可以直接比较不同时间点的数值，但仍要结合历史基线、实体差异和持续时间。一个孤立高点不一定是故障。

#### Counter 是什么

Counter 是累计值，通常只增加，进程重启时可能归零，例如：

- 累计请求数。
- 累计错误数。
- 累计发送字节数。

不能因为后一分钟的累计值比前一分钟大，就判断异常。正确做法是计算速率：

```text
rate = (counter[t2] - counter[t1]) / (t2 - t1)
```

计算前要检查：

1. 两个样本是否属于同一条序列，标签必须一致。
2. 两个时间戳是否有效且 `t2 > t1`。
3. 数值是否为 NULL 或非有限值。
4. 是否发生 Counter Reset，即 `counter[t2] < counter[t1]`。
5. 采样间隔是否过长，不能跨大段缺失数据强行求速率。
6. 是否有多个正常窗口可以建立 Median/MAD 等稳健基线。

如果 `metric_type` 是 NULL 或未知，不要只凭指标名中的 `_total` 猜测类型。Starter Kit 会保留这类样本，但默认不把它们当 Gauge 或 Counter。

### 4.2 Logs：日志

Logs 是系统运行时输出的文本事件。一条日志可能包含：

- `timestamp`：发生时间。
- `service`、`pod`：来源实体，可能缺失。
- `severity`：ERROR、WARN、INFO 等，也可能为 NULL。
- `body`：日志正文。
- `trace_id`、`span_id`：与调用链关联的标识，可能缺失。
- `attributes`：额外结构化字段。

日志分析常见思路：

- 某服务 ERROR/WARN 数量是否突然增加。
- 某类错误消息是否首次出现或频率改变。
- 是否出现 timeout、connection reset、refused、unavailable 等模式。
- 日志密度是否在某个时间窗明显变化。
- 日志中的 `trace_id` 能否关联到异常调用链。

注意：

- `severity=NULL` 不等于正常，应继续检查正文和上下文。
- 单条 ERROR 不一定是根因，可能是可恢复重试或下游传播症状。
- 不能只搜索一个关键词；还要做宽采样、数量变化和跨模态关联。
- 同样的错误持续出现在所有时间窗，可能是背景噪声，而不是新异常。

### 4.3 Traces：分布式调用链

一次用户请求可能经过多个微服务。Trace 用一个 `trace_id` 串起整条调用链，每个操作是一个 Span。

常见字段：

- `trace_id`：整条请求链标识。
- `span_id`：当前 Span 标识。
- `parent_span_id`：父 Span 标识。
- `service`：当前操作所属服务。
- `parent_service`：公开索引推导的上游服务，可能为空。
- `operation_name`：操作名称。
- `start_time`：开始时间。
- `duration_ms`：持续时间。
- `status`：状态，可能缺失。

Trace 可以回答：

- 延迟从哪条调用边开始升高。
- 下游异常是否向上游传播。
- 某个 ERROR Span 的父子关系是否真实存在。
- 同一服务在正常窗口与可疑窗口的 p95 延迟是否明显不同。

注意：普通 Trace 的存在不等于异常；必须比较延迟、状态、调用量或上下游传播。缺失 `status` 时保留不确定性，不要补成成功或失败。

### 4.4 Topology：服务拓扑

Topology 描述有哪些 Service、Pod、Node，以及它们之间可能存在的调用关系。它的作用是帮助 Agent：

- 发现实体，而不是写死服务名。
- 选择需要查询的服务和调用边。
- 判断日志、指标和 Trace 是否属于同一实体。
- 分析上游与下游传播。

拓扑只是“系统结构与观测关系”，不直接说明哪里发生了故障。

---

## 5. Public API 使用方法

下面示例访问练习题只读 API。正式题将 `practice` 换为 `semifinal`，但只有在本队所属场次开放且题目已发布时才可访问。

```bash
BASE_URL=http://192.168.51.51:8080
API_ROOT="$BASE_URL/v1/practice"
CASE_ID=SF-001
```

### 5.1 查看题目和时间范围

```bash
curl -s -H "Authorization: Bearer $AIOPS_TEAM_TOKEN" "$API_ROOT/cases" | python -m json.tool
curl -s -H "Authorization: Bearer $AIOPS_TEAM_TOKEN" "$API_ROOT/cases/$CASE_ID/metadata" | python -m json.tool
curl -s -H "Authorization: Bearer $AIOPS_TEAM_TOKEN" "$API_ROOT/cases/$CASE_ID/topology" | python -m json.tool
```

先从 Metadata 读取 `start_time` 和 `end_time`，不要假设每道题都是固定日期或固定 60 分钟。

### 5.2 查询 Metrics

```bash
curl -sG -H "Authorization: Bearer $AIOPS_TEAM_TOKEN" "$API_ROOT/cases/$CASE_ID/metrics" \
  --data-urlencode "start=<UTC开始时间>" \
  --data-urlencode "end=<UTC结束时间>" \
  --data-urlencode "service=<服务名>" \
  --data-urlencode "aggregation=raw" \
  --data-urlencode "page=1" \
  --data-urlencode "page_size=100" | python -m json.tool
```

常用过滤条件包括 `metric_name`、`service`、`pod`、`node`、时间范围和聚合方式。参数以实际 API 返回和 Starter Tool Registry 为准。

### 5.3 查询 Logs

```bash
curl -sG -H "Authorization: Bearer $AIOPS_TEAM_TOKEN" "$API_ROOT/cases/$CASE_ID/logs" \
  --data-urlencode "start=<UTC开始时间>" \
  --data-urlencode "end=<UTC结束时间>" \
  --data-urlencode "service=<服务名>" \
  --data-urlencode "keyword=error" \
  --data-urlencode "page=1" \
  --data-urlencode "page_size=100" | python -m json.tool
```

可按服务、Pod、severity、trace_id 和关键词筛选。关键词查询适合补充证据，不应成为唯一日志分析方法。

### 5.4 查询 Trace 列表和详情

```bash
curl -sG -H "Authorization: Bearer $AIOPS_TEAM_TOKEN" "$API_ROOT/cases/$CASE_ID/traces" \
  --data-urlencode "start=<UTC开始时间>" \
  --data-urlencode "end=<UTC结束时间>" \
  --data-urlencode "service=<服务名>" \
  --data-urlencode "page=1" \
  --data-urlencode "page_size=100" | python -m json.tool
```

取得真实 `trace_id` 后再展开：

```bash
TRACE_ID=<从查询结果中取得的真实TraceID>
curl -s -H "Authorization: Bearer $AIOPS_TEAM_TOKEN" "$API_ROOT/cases/$CASE_ID/traces/$TRACE_ID" | python -m json.tool
```

Trace Detail 用于核验父子 Span、代表性调用链和证据是否真实存在。

### 5.5 API 限制

当前环境需要考虑：

- 单页最多 1,000 行。
- 每逻辑队伍每分钟最多 60 次请求。
- 单请求时间窗口最多 3,600 秒。
- 服务端查询有超时限制。
- 服务端有并发上限。

建议：

- 先按五分钟或十分钟窗口扫描，再对可疑实体缩小范围。
- 优先使用服务、时间、指标名等服务端过滤条件。
- 给每轮分析设置总请求预算。
- 对 429 做有限退避，不要无休止重试。
- 记录分页和采样范围；只拿到一页时必须承认是样本。

---

## 6. Starter Kit 目录说明

```text
participant-starter/
├── README.md                    # 快速技术说明
├── PARTICIPANT_HANDBOOK.md      # 本参赛手册
├── REPORT.md                    # 已完成的构建和实验报告
├── config.yaml                  # 扫描、阈值、预算等配置
├── main.py                      # 命令行入口
├── agent/
│   ├── core.py                  # 状态机、查询计划、分析和输出
│   ├── tool_registry.py         # Agent 可调用的稳定工具名
│   ├── skill_loader.py          # Skill 自动发现与动态加载
│   ├── mcp_client.py            # MCP stdio 客户端
│   └── evidence.py              # 证据生成和校验
├── client/
│   └── public_api.py            # 分页、重试、预算和请求日志
├── detectors/
│   ├── metrics.py               # Gauge 与 Counter Rate 检测
│   ├── logs.py                  # 日志异常检测
│   ├── traces.py                # Trace 延迟检测
│   └── common.py                # 时间、分位数、稳健统计工具
├── skills/
│   └── counter-rate-analysis/
│       └── SKILL.md             # 可被程序实际加载的 Skill
├── mcp_servers/
│   └── trace_diagnostics.py     # Trace Diagnostics MCP Server
├── configs/
│   └── mcp_servers.json         # MCP Server 启动配置
├── schemas/
│   └── answer.schema.json       # 最终答案 JSON Schema
├── examples/                    # 三种运行示例
├── tests/                       # 单元测试
└── runs/                        # 每次运行的独立产物
```

修改前建议先复制一份自己的分支或目录，并保留每次运行结果。不要删除组织方提供的历史记录。

---

## 7. Agent 是怎样分析题目的

新版默认是 LLM Agent：模型负责规划、选工具和收敛决策，程序负责权限、预算、
工具执行和证据验证。`--agent baseline` 保留原有固定扫描流程作为对照。

### 7.1 Discover：发现题目

Agent 先调用：

- `list_cases()`
- `get_case_metadata(case_id)`
- `get_topology(case_id)`
- `get_fault_taxonomy()`

如果 Case 未发布或不存在，应停止并报告，不要换用本地假数据继续生成答案。

### 7.2 Plan：制定查询计划

LLM 根据真实时间范围和公开拓扑制定计划，不会被告知真实故障数量、目标或时间。
它自主决定：

- 每个窗口查询多少 Metrics。
- 日志宽采样与关键词采样如何交替。
- 根据日志信号选择哪些服务深入查询 Trace。
- 保留多少请求给主动回查。

一个好的计划应兼顾覆盖率、请求预算和分析深度。不要一开始下载所有数据，也不要只看一个时间点。

### 7.3 Collect：收集有界样本

工具层会记录每次请求：

- 请求路径和参数。
- 请求时间和耗时。
- HTTP 状态码。
- 返回记录数。
- 重试次数和错误。

这些内容写入 `api_requests.jsonl`。如果一页已经达到 `page_size`，说明结果可能被截断，不能把该页当作全量数据。

### 7.4 Analyze：生成异常候选

候选只是“值得进一步调查”，不是最终答案。默认检测包括：

- Gauge 的稳健离群检测。
- Counter Skill 启用后的速率偏离检测。
- 错误日志数量和时间窗变化。
- Trace p95 延迟相对基线的变化。

### 7.5 Verify：主动验证

Agent 会对高分 Trace 候选查询完整 Trace Detail。启用 MCP 时，还会：

1. 从异常 Span 读取真实 `parent_service` 和目标服务。
2. 自动选择基线窗口与分析窗口。
3. 查询调用边相关 Trace。
4. 用 `parent_span_id` 在 Trace Detail 中验证父子关系。
5. 比较 median、p95、样本数和覆盖限制。

数据不足时应返回 `INSUFFICIENT_EVIDENCE`，工具失败时返回 `UNKNOWN`，不能用 0 代替缺失值。

### 7.6 Output：生成答案

程序对候选排序、去重并限制结果数量，然后执行：

- JSON Schema 校验。
- 证据与 API 请求 ID 关联检查。
- 总耗时和请求数汇总。

只有完成验证的运行才应进入提交候选。

---

## 8. 三种推荐运行方式

每次实验都使用新目录和不同的逻辑运行 ID，便于比较和复核。

### 8.1 A：Baseline

```bash
python main.py \
  --agent baseline --no-skills --no-mcp \
  --case SF-001 \
  --base-url http://192.168.51.51:8080 \
  --question-file examples/question.txt \
  --run-dir runs/team-baseline \
  --team-id team-baseline
```

基础模式不会执行 Counter Rate Skill，也不会启动 MCP。明确为 Counter 的样本会被保留，但不会按绝对值当作 Gauge 异常。

### 8.2 B：LLM Agent（不启用 Skill/MCP）

```bash
python main.py \
  --agent llm --no-skills --no-mcp \
  --case SF-001 \
  --base-url http://192.168.51.51:8080 \
  --question-file examples/question.txt \
  --run-dir runs/team-llm \
  --team-id team-llm
```

运行后检查：

```bash
python -m json.tool runs/team-llm/summary.json
sed -n '1,20p' runs/team-llm/agent_tool_calls.jsonl
```

重点看：

- `selection_mode` 是否为 `native_tool_calling`。
- `llm_calls` 和 `llm_tool_calls` 是否大于 0。
- 模型是否真正决定了查询服务、时间窗和分析器。
- `schema_valid` 和 `evidence_valid` 是否为 `true`。

### 8.3 C：LLM Agent + Skill + MCP（默认推荐）

```bash
python main.py \
  --agent llm \
  --case SF-001 \
  --base-url http://192.168.51.51:8080 \
  --question-file examples/question.txt \
  --run-dir runs/team-full \
  --team-id team-full \
  --skills-dir skills \
  --mcp-config configs/mcp_servers.json
```

运行后检查：

```bash
python -m json.tool runs/team-full/skill_usage.json
sed -n '1,20p' runs/team-full/mcp_calls.jsonl
python -m json.tool runs/team-full/evidence_validation.json
```

不要只看 MCP 是否启动，还要检查：

- discovery 是否真的发现 `analyze_edge_latency`。
- `call` 记录是否为 `ok: true`。
- `source_service` 与 `target_service` 是否来自异常证据。
- `baseline_count`、`analysis_count` 是否足够。
- `coverage_warnings` 是否提示有界采样。
- `supported_conclusion` 是什么。

---

## 9. 如何阅读运行产物

### 9.1 answer.json

这是最终候选答案。例如下面只是格式演示，实体和时间均为虚构值：

```json
{
  "case_id": "DEMO-001",
  "anomalies": [
    {
      "entity_type": "Service",
      "entity_id": "demo-service",
      "anomaly_type": "trace_latency_shift",
      "start_time": "2030-01-01T00:10:00Z",
      "end_time": "2030-01-01T00:15:00Z",
      "evidence": [
        {
          "modality": "traces",
          "request_id": "req-0012",
          "record_sha256": "示例摘要",
          "record": {
            "trace_id": "示例TraceID",
            "duration_ms": 123.4
          }
        }
      ]
    }
  ]
}
```

不要复制示例中的实体、时间或数值作为真实答案。

### 9.2 debug.json

适合回答：

- 实际扫描了几个窗口。
- 每种模态拿到多少样本。
- 哪些服务被选择做 Trace 深查。
- 候选数量是多少。
- 有多少 Counter 或未知类型样本被保留。
- 是否出现覆盖限制或警告。

### 9.3 api_requests.jsonl

每行是一个 JSON 对象。可以快速统计状态码：

```bash
python - <<'PY'
import collections, json
path = "runs/team-full/api_requests.jsonl"
rows = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
print("请求数:", len(rows))
print("状态码:", collections.Counter(row.get("status") for row in rows))
print("总返回记录:", sum(row.get("item_count") or 0 for row in rows))
PY
```

重试请求也计入预算，因此请求行数可能大于逻辑查询数。

### 9.4 evidence_validation.json

`valid: true` 表示最终答案的证据都能关联到真实成功请求或真实 MCP 结构化结果。它不是正式正确率，只说明证据链完整。

### 9.5 summary.json

包含：

- 最终状态。
- Case ID。
- 输出异常数。
- 主 Agent 请求数。
- 总执行时间。
- 证据检查结果。

MCP Server 自己发出的额外 API 请求记录在 MCP 返回的 `api_request_count` 中，应在性能比较时单独加上。

---

## 10. 如何判断一个异常是否值得提交

建议对每个候选依次回答以下问题：

### 10.1 这个现象是否相对基线发生了变化

不要只看绝对值。应比较：

- 同一实体的其他时间窗。
- 同类型实体之间的差异。
- 正常窗口与可疑窗口的 median、p95 或速率。

### 10.2 是否持续或重复出现

一个孤立点可能来自采样抖动。检查：

- 是否连续多个采样异常。
- 是否多个请求或 Trace 都出现类似现象。
- 是否在相邻时间窗仍然存在。

### 10.3 是否有第二种模态支持

例如：

- Metric 显示错误率上升，Logs 同时出现错误突增。
- Logs 中包含 trace_id，Trace Detail 显示下游延迟升高。
- Trace 显示某调用边变慢，相关服务的资源 Gauge 同时发生变化。

第二种模态不是强制条件，但通常能提高证据质量。

### 10.4 它是根因还是传播症状

上游服务可能因为等待下游而变慢。可以分别记录有独立异常判据的实体，但不要把同一条传播链重复提交成很多无独立证据的事件。

### 10.5 数据是否足够

如果只有一个样本、分页明显截断、字段缺失或基线为空，应降低置信度或拒绝候选。不要把“不知道”写成“正常”。

---

## 11. 修改检测算法

### 11.1 修改阈值

默认配置在 `config.yaml`：

```yaml
detection:
  robust_z_threshold: 5.0
  counter_ratio_threshold: 2.0
```

降低阈值会增加候选，也会增加噪声；提高阈值会减少误报，也可能漏掉弱异常。每次修改都应新建运行目录并比较：

- 候选数量。
- 最终事件数。
- 证据有效比例。
- 请求数和耗时。
- 哪些事件新增、消失或改变。

没有 Private GT 时，不要把候选更多或更少直接称为准确率提升。

### 11.2 新增检测器

可以在 `detectors/` 新建模块，例如：

```python
def detect_something(rows, config):
    candidates = []
    # 1. 校验字段
    # 2. 按实体和时间分组
    # 3. 建立基线
    # 4. 生成带真实记录引用的候选
    return candidates
```

候选至少应包含：

- `modality`
- `entity_type`
- `entity_id`
- `anomaly_type`
- `start_time`
- `end_time`
- `score`
- `summary`
- `evidence_records`

然后在 `agent/core.py` 的分析阶段调用，并补充单元测试。

### 11.3 改造查询规划

默认策略适合入门，但不是唯一方案。可以尝试：

- 第一轮低成本全局扫描。
- 按异常分数选择 Top-K 实体。
- 为每个候选生成“还缺什么证据”。
- 按剩余请求预算决定是否继续查询。
- 对同一候选多轮更新置信度。
- 达到停止条件后提前结束。

无论使用状态机、ReAct 还是 LangGraph，都要保留请求预算和证据校验。

---

## 12. 创建自己的 Skill

Skill 适合封装“在特定条件下应采用的一套专业分析步骤”。它不只是提示词文件，还必须能改变实际程序行为。

### 12.1 创建目录

```bash
mkdir -p skills/my-skill
```

### 12.2 编写 SKILL.md

```markdown
---
name: my-skill
description: 当什么数据条件出现时使用这个能力
handler: detectors.my_detector:analyze
version: 1.0.0
triggers:
  metric_type: gauge
---

# My Skill

说明适用场景、分析步骤、工具、误判和证据格式。
```

名称建议使用小写字母、数字和连字符。`handler` 必须指向可导入的 Python 函数。

### 12.3 实现 handler

在 `detectors/my_detector.py` 中实现相应函数。函数应接收真实 API 数据，返回可验证候选，不能在内部写死题目答案。

### 12.4 验证 Skill 真正生效

分别运行不开启和开启 Skill 的两组实验，比较：

- `skill_usage.json` 中是否被发现、选择和执行。
- 候选数量或类型是否变化。
- 证据格式是否变化。
- 请求数和耗时是否变化。
- 是否引入新的误判风险。

如果只出现 `discovered`，但没有 `selected` 和 `executed`，说明 Skill 没有参与本次分析。

---

## 13. 创建自己的 MCP Server

MCP 适合把独立工具以标准协议提供给 Agent，例如复杂 Trace 分析、统计模型或内部知识工具。

### 13.1 MCP Server 必须遵守的原则

- 运行在参赛样板机。
- 只使用 Public API。
- 输入、输出结构明确。
- 设置超时、最大页数和最大样本数。
- 数据不足时返回 UNKNOWN 或 INSUFFICIENT_EVIDENCE。
- 记录真实调用，不伪造成功。

### 13.2 参考现有实现

查看：

```text
mcp_servers/trace_diagnostics.py
```

它使用官方 Python MCP SDK 和 stdio，提供 `analyze_edge_latency`。

### 13.3 配置 Server

在 `configs/mcp_servers.json` 增加配置。可以使用：

- `${PYTHON}`：当前 Python 解释器。
- `${PROJECT_ROOT}`：Starter 根目录。
- `${BASE_URL}`：当前 Public API 地址。

不要在配置中放置明文密钥。

### 13.4 验证 discovery 和调用

```bash
PYTHONPATH=. python -m unittest tests.test_mcp -v
```

实际 Agent 运行后检查 `mcp_calls.jsonl`。Server 能启动只代表 discovery 成功；还必须看到真实 `call` 记录和结构化证据。

### 13.5 MCP 失败怎么办

Starter Kit 会记录错误并降级为 `UNKNOWN`，基础 Agent 继续运行。你的代码也应采用相同原则：外部工具失败不能被解释为“系统正常”，更不能生成虚假证据。

---

## 14. 使用自己的 LLM

默认模式已是真实 LLM Agent，支持 OpenAI-compatible `/chat/completions`。模型负责：

- 规划下一步查询。
- 解释候选之间的关系。
- 选择需要补充的证据。
- 主动调用统计、Skill 和 MCP 工具。
- 从公开 Taxonomy 选择故障类型并决定何时停止。

不建议让模型在没有数据约束的情况下直接生成异常答案。

安全要求：

1. API Key 只能从环境变量或受保护配置读取。
2. 不把密钥写入 Git、日志、Prompt 记录或 `answer.json`。
3. 对模型输出执行 JSON Schema 校验。
4. 模型引用的每条证据必须回查 Public API。
5. 模型不可访问 Private GT 或已知故障答案。
6. 模型不可用时必须显式输出 `LLM_UNAVAILABLE`；需要 Baseline 时由选手显式选择。

推荐单独配置文件：

```bash
sudo install -m 600 configs/agent-llm.env.example /etc/aiops-competition/agent-llm.env
sudo editor /etc/aiops-competition/agent-llm.env
```

文件中配置 `AIOPS_AGENT_LLM_BASE_URL`、`AIOPS_AGENT_LLM_MODEL` 和
`AIOPS_AGENT_LLM_API_KEY`。这些名称与组织方 Judge 的变量完全不同，不得复用 Judge Key。
启动时会实测原生 Tool Calling；不支持时才使用 Schema 验证的结构化动作。

---

## 15. 测试建议

修改代码后至少检查以下场景：

1. 能读取题目文本、Metadata 和 Topology。
2. Metrics、Logs、Traces 都有真实查询。
3. Counter 不会按累计值直接当 Gauge。
4. `metric_type=NULL` 不会被名称猜测为 Counter 或 Gauge。
5. Counter Reset、NULL 和异常时间间隔被正确处理。
6. `severity=NULL` 的日志不会被忽略。
7. `status=NULL` 或 Pod UID 缺失不会导致 Trace 检测崩溃。
8. 普通 Trace 不会因为存在就被判异常。
9. 429 会有限退避，并计入请求预算。
10. 查询失败不会生成“正常”结论。
11. Skill 能被发现、选择和执行。
12. MCP 能 discovery、真实调用并在失败时降级。
13. 最终 JSON Schema 通过。
14. 所有最终证据校验通过。

运行全部现有测试：

```bash
PYTHONPATH=. python -m unittest discover -s tests -v
```

增加新功能时，应增加相应测试，而不是只修改算法代码。

---

## 16. 常见问题和排错

### 16.1 无法连接 API

检查：

```bash
ping -c 2 192.168.51.51
curl -v --max-time 5 http://192.168.51.51:8080/healthz
```

如果网络不通，先确认比赛 VPN 和内网路由。不要通过扫描其他端口或绕过认证解决。

### 16.2 返回 404

常见原因：

- Case ID 拼写错误。
- Case 尚未发布。
- Trace ID 不存在。
- API 路径写错。

先调用 `/v1/cases` 确认公开题目，不要自动换成其他 Case。

### 16.3 返回 422

表示参数校验失败。检查：

- UTC 时间格式。
- `start < end`。
- 参数名称是否与 API 一致。
- page、page_size、aggregation 是否在允许范围。

### 16.4 返回 429

说明超过逻辑队伍限流。应：

- 等待 `Retry-After`。
- 减少不必要请求。
- 降低并发。
- 复用已经获得的数据。
- 检查是否误写了无限重试循环。

不要不停更换虚假 Team ID 规避限流。

### 16.5 查询超时

缩小时间范围或增加实体过滤，不要盲目增大客户端超时。优先把 60 分钟拆成小窗口，并只对可疑服务做深查。

### 16.6 返回空数组

空数组不等于系统正常。依次检查：

- 时间是否在 Metadata 范围内。
- 时区是否为 UTC。
- 服务名、Pod 名、指标名是否真实存在。
- 过滤条件是否过多。
- 当前模态是否在 `available_modalities` 中。

### 16.7 Skill 没有效果

检查 `skill_usage.json`：

- 没有 `discovered`：目录或 frontmatter 错误。
- 有 `discovered` 没有 `selected`：触发条件未满足。
- 有 `selected` 没有 `executed`：handler 导入或执行失败。
- 已执行但候选不变：可能真实数据没有满足阈值，这不是程序错误。

### 16.8 MCP Server 启动失败

检查：

- 当前 Python 是否安装 `mcp`。
- `PYTHONPATH` 是否包含 Starter 根目录。
- `configs/mcp_servers.json` 路径是否正确。
- Server 是否向 stdout 输出了非 MCP 内容；普通日志应写到 stderr。
- `mcp_calls.jsonl` 中的错误信息。

基础 Agent 应继续完成，不要因为 MCP 失败而伪造工具成功。

### 16.9 evidence_validation 为 false

检查最终异常是否：

- 没有 evidence。
- 引用了不存在的 request_id。
- 使用手工编造的 Trace ID。
- 把 Debug 文字误当成数据证据。
- 在输出前丢失了原始记录引用。

### 16.10 候选太多

可以：

- 提高稳健阈值。
- 要求最小样本数和连续性。
- 增加多模态支持条件。
- 按实体和相近时间归并。
- 区分根因和传播症状。
- 限制每个实体最多提交的事件数。

### 16.11 候选为零

不要立即降低所有阈值。先检查采样是否覆盖完整时间、是否因过滤条件漏掉数据、是否所有页面都为空，以及候选是否被验证阶段正确拒绝。零候选可以是合法结果，但必须有完整查询和诊断记录支持。

---

## 17. 推荐的比赛工作方法

### 17.1 第一阶段：先跑通

- 不改代码运行 Baseline。
- 保存运行目录。
- 确认测试、Schema 和证据校验通过。
- 阅读 `debug.json` 和请求日志，理解数据规模。

### 17.2 第二阶段：建立自己的观察

- 列出各服务和 Pod。
- 查看每种 Metrics 类型和主要指标名。
- 观察日志 severity 缺失比例和常见模板。
- 查看 Trace status 缺失情况和主要调用边。
- 记录采样覆盖，而不是预设答案。

### 17.3 第三阶段：一次只改一个变量

例如只调整 Gauge 阈值，然后运行新目录比较。不要同时更换查询窗口、阈值、排序和输出格式，否则难以判断是哪项修改产生了影响。

### 17.4 第四阶段：补充证据

对高分候选提出问题：

- 还缺什么数据才能支持或拒绝它？
- 能否用另一个时间窗建立基线？
- 是否需要完整 Trace Detail？
- 是否能在 Logs 中找到同一 trace_id？
- 是否只是上游传播症状？

### 17.5 第五阶段：冻结提交版本

提交前停止继续调阈值，重新运行测试和正式题目，保存：

- 源码版本。
- `config.yaml`。
- `answer.json`。
- `summary.json`。
- API、Skill、MCP 和证据日志。

不要用手工编辑破坏 `answer.json` 与证据日志之间的一致性。

---

## 18. 团队分工建议

如果多人参赛，可以参考：

| 角色 | 主要工作 |
|---|---|
| 数据与 API | 熟悉字段、分页、采样、限流和查询计划 |
| Metrics | Gauge、Counter、资源指标和稳健统计 |
| Logs | 日志模板、错误聚类、密度和 Trace 关联 |
| Traces | 调用边、父子 Span、延迟与传播分析 |
| Agent 工程 | 状态机、工具选择、预算、Skill/MCP 和输出 |
| 验证与提交 | 测试、Schema、证据回查和运行记录 |

人数较少时可以合并角色，但应明确谁负责最终证据检查。

---

## 19. 提交前检查清单

### 环境

- [ ] 使用的是已发布 Case ID。
- [ ] API 健康，Metadata 时间范围读取成功。
- [ ] 未访问 Private GT、Staging 或服务器磁盘。
- [ ] 未修改数据服务器和已有 SDK。

### 分析

- [ ] 覆盖 Metrics、Logs、Traces。
- [ ] Counter 使用速率或明确保留，不按累计值误判。
- [ ] 未知 metric_type 没有被名称强行推断。
- [ ] NULL severity/status 没有导致错误结论。
- [ ] 普通 Trace 没有仅因存在而被判异常。
- [ ] 有界样本和分页截断已经标记。
- [ ] 没有硬编码故障时间、服务或数量。

### 证据

- [ ] 每条异常至少包含一条真实 Public 证据。
- [ ] Trace ID 可以通过 Trace Detail 回查。
- [ ] MCP 结果包含样本数、覆盖告警和结论状态。
- [ ] `evidence_validation.json` 为 `valid: true`。

### 输出

- [ ] `answer.json` 是合法 JSON。
- [ ] JSON Schema 校验通过。
- [ ] Case ID 和 UTC 时间正确。
- [ ] Debug 信息没有混入正式答案结构。
- [ ] `summary.json` 状态为 `complete`。
- [ ] 保留本次运行的全部日志和配置。

---

## 20. 常用命令速查

```bash
# 进入工程
cd /opt/aiops-competition/participant-starter

# 启用环境
source /opt/aiops-competition/.venv/bin/activate

# API 健康检查
curl -s http://192.168.51.51:8080/healthz

# 查看题目
curl -s http://192.168.51.51:8080/v1/cases | python -m json.tool

# 基础运行
python main.py --case SF-001 \
  --base-url http://192.168.51.51:8080 \
  --question-file examples/question.txt \
  --run-dir runs/my-run \
  --team-id my-team

# 启用 Skill 和 MCP
python main.py --case SF-001 \
  --base-url http://192.168.51.51:8080 \
  --question-file examples/question.txt \
  --run-dir runs/my-full-run \
  --team-id my-team-full \
  --skills-dir skills \
  --mcp-config configs/mcp_servers.json

# 测试
PYTHONPATH=. python -m unittest discover -s tests -v

# 查看答案与验证结果
python -m json.tool runs/my-full-run/answer.json
python -m json.tool runs/my-full-run/summary.json
python -m json.tool runs/my-full-run/evidence_validation.json
```

---

## 21. 最后提醒

智能运维分析最重要的不是输出更多异常，而是形成一条可复核的推理链：

```text
发现变化 → 提出假设 → 查询补充证据 → 支持或拒绝假设 → 输出可回查结果
```

当数据不足时，诚实地标记 `UNKNOWN` 或 `INSUFFICIENT_EVIDENCE`，比根据经验猜一个答案更可靠。把每次查询、每个阈值和每条证据都保留下来，你的 Agent 才能被复现、比较和持续改进。
