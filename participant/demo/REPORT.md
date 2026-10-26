# Participant Agent Starter Kit 完成报告

生成日期：2026-10-14  
部署位置：`/opt/aiops-competition/participant-starter/`（192.168.51.50）  
Public API：`http://192.168.51.51:8080`  
状态：`PARTICIPANT_STARTER_READY`

## 1. 交付与边界

已交付默认确定性 Agent、Public API Tool Registry、Counter Rate Skill、官方 Python
MCP SDK stdio Server/Client、Trace 父子边诊断工具、JSON Schema、示例、测试、中文
README 和三组真实运行记录。未使用外部 LLM 或 API Key。

本轮仅在 192.168.51.50 新建 Starter 目录。192.168.51.51 的 API、Parquet、Nginx、
Kubernetes 靶场均未修改，未读取 Private GT、Staging、注入计划或服务器磁盘数据。
既有 SDK 与样板 Agent 的 SHA-256 仍为：

- `competition_client.py`：`13f3319b413ff88d71189080b64cef83b6164e572071e54d7ef6a5d392ee8edf`
- `sf001_public_agent.py`：`981134dc1f88d517c2085a3b350282f278aa3be97e91c5a0afd9e875d361a060`

检测计划仅由 Metadata、Topology 和实时 Public 数据推导，没有硬编码故障数量、时间或服务。

## 2. 实现摘要

- 12 个五分钟窗口覆盖 Metadata 给出的完整 60 分钟范围。
- API 客户端记录参数、状态、记录数、时延、错误与重试；支持分页、429 退避、超时和预算。
- 每个窗口采一页 Metrics；Logs 在宽采样和错误关键词采样之间交替；Trace 服务由日志证据排序产生。
- Gauge、Counter、日志、Trace 分开检测；Counter 基线不按绝对值判异常。
- 最强 Trace 候选执行 Trace Detail 主动回查。
- Counter Skill 通过 `SKILL.md` frontmatter 被扫描、选择并动态加载 handler。
- MCP Client 完成 stdio discovery 和真实 tool call；失败返回 UNKNOWN，不影响基础流程。
- 最终答案通过 Draft 2020-12 JSON Schema，证据引用与请求日志完成关联检查。

所有模态采用有界采样，不代表全量数据；覆盖警告保存在 Debug/MCP 结果中。

## 3. 三组真实实验

共同条件：SF-001、相同题目、相同 `config.yaml`、相同 55 次主 Agent 请求预算、相同
五分钟窗口、每页 500、确定性算法。每组主流程均获得 6,000 Metrics、3,651 Logs、
12,000 Trace 样本，动态选择 `recommendation` 和 `frontend` 两个 Trace 深查服务。

| 指标 | Baseline | +Skill | +Skill+MCP |
|---|---:|---:|---:|
| 输出异常事件数 | 5 | 5 | 5 |
| 候选数（Metrics/Logs/Traces） | 1/8/2 | 21/8/2 | 21/8/2 |
| 有效证据引用比例 | 13/13（100%） | 12/12（100%） | 13/13（100%） |
| Counter 样本保留但未误当 Gauge | 1,730 | 1,730 | 1,730 |
| 类型未知样本保留但不推断类型 | 3,260 | 3,260 | 3,260 |
| Counter 误用数 | 0 | 0 | 0 |
| Skill 派生有效速率 | 0 | 1,657 | 1,657 |
| Trace MCP 核验通过 | 不适用 | 不适用 | 1 |
| 主 Agent API 请求数 | 52 | 52 | 52 |
| MCP 额外 API 请求数 | 0 | 0 | 14 |
| 总 API 请求数 | 52 | 52 | 66 |
| Agent 内计时 | 9.015 s | 9.543 s | 12.089 s |

### 3.1 Skill 的可观察影响

Baseline 正确拒绝把 1,730 个 Counter 样本当 Gauge，因此本次没有观察到 Counter
“误用数下降”；这不是准确率结论。启用 Skill 后，程序实际发现 73 条 Counter 系列并
生成 1,657 个有效相邻速率，Metrics 候选从 1 增至 21。另有 3,260 条类型为 NULL 的
Metrics 被明确保留但不按名称推断为 Gauge 或 Counter。最终 Top-5 中出现基于两条
原始记录推导的 Counter rate 候选，证明 Skill 改变了分析行为与证据形态。代价约为
0.528 秒，无新增 Public API 请求。是否提高正式正确率无法在无 Private GT 条件下判断。

### 3.2 MCP 的真实结果

MCP discovery 成功，`analyze_edge_latency` 被实际调用。调用边由代表性异常 Span 的
真实 `parent_service` 推导为 `frontend → recommendation`，不是预置目标。MCP 对两个
窗口各回查 6 条 Trace Detail，并验证 `parent_span_id`：

- 基线 p95：4.428 ms。
- 分析窗口 p95：25.45825 ms。
- p95 倍率：5.749379。
- 返回结论：`SUPPORTED`。
- MCP tool 耗时：1,939.615 ms。
- 覆盖限制：每窗口最多验证 6 条 Trace，结果是有界样本而非全量边统计。

MCP 为相同 Trace 异常增加了一条结构化、父子关系已核验的证据；总运行时间相对 +Skill
增加约 2.546 秒，额外使用 14 次 API 请求。没有把普通调用链的存在直接当作异常。

## 4. 测试与验收

测试命令：

```bash
PYTHONPATH=. /opt/aiops-competition/.venv/bin/python -m unittest discover -s tests -v
```

覆盖主状态机、JSON 输出、429 有限重试、证据关联、Counter/Gauge 隔离、NULL/Reset、
缺失日志 severity、缺失 Span status、普通 Trace 不判异常、Skill discovery/handler、
MCP stdio discovery、MCP 失败降级、未知 Metrics 类型隔离（共 10 个单元测试）。真实 A/B/C 又覆盖题目读取、完整公开 Metadata/Topology、
三模态扫描、Trace Detail、真实 MCP 调用、Schema 和全部证据检查。

正式实验的三个 `evidence_validation.json` 均为 `valid: true`。API 查询失败会抛错或记录
UNKNOWN，不会用 0 或空数据伪造正常结论。工具层的 Case ID 是参数，因此可复用于其他
已发布 Case；本轮仅存在并实测 SF-001。

## 5. 文件与运行结果

- `README.md`：选手使用和扩展说明。
- `main.py`：CLI 入口。
- `agent/core.py`：状态机、计划、验证与输出。
- `agent/tool_registry.py`：稳定工具接口。
- `skills/counter-rate-analysis/SKILL.md`：实际加载的 Counter Skill。
- `mcp_servers/trace_diagnostics.py`：标准 MCP stdio Server。
- `configs/mcp_servers.json`：MCP 配置。
- `schemas/answer.schema.json`：最终答案 Schema。
- `runs/experiment-a-baseline/`：Baseline 全部产物。
- `runs/experiment-b-skill/`：+Skill 全部产物。
- `runs/experiment-c-skill-mcp/`：+Skill+MCP 全部产物。
- `runs/prechecks/`：开发期预检查保留记录，未删除既有运行。

每个正式运行目录至少包含 `answer.json`、`execution.log`、`api_requests.jsonl`、
`skill_usage.json`、`evidence_validation.json`、`debug.json`、`summary.json`；C 另含
`mcp_calls.jsonl`。

## 6. 已知限制与后续扩展

- 当前是资源受限的有界采样，不计算全量 Span 统计；可在服务端聚合能力增强后替换工具实现。
- 日志聚类仍是规则与稳健计数，不做模板挖掘或语义向量聚类。
- Candidate 排名尚未使用历史题目或正式 GT；Top-5 不是正式评分结果。
- LLM、LangGraph/ReAct、自动提交和评分服务均为可选扩展，当前未启用也未声称已部署。
- MCP 每窗只验证 6 条 Trace，结果已明确带覆盖告警。

结论：Starter Kit、Counter Skill、MCP Server/Client、三组真实实验和证据链验收均已完成，
可供选手直接运行和自由改写。
