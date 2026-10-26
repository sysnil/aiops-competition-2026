# SF-001 选手 LLM Agent 升级与真实运行报告

日期：2026-10-15  
执行位置：`192.168.51.50:/opt/aiops-competition/participant-starter/`  
数据来源：`http://192.168.51.51:8080` 匿名只读 Public API  
最终状态：`LLM_AGENT_READY`

## 1. 交付结论

Starter 已从“Python 固定扫描 + 规则决策”升级为真实 LLM 驱动的有界 Agent：

- LLM 实际参与初始计划、服务/时间窗选择、工具调用、证据补充、故障类型判断和停止。
- 已实测模型原生 Tool Calling，不是“固定检测后只让模型总结一次”。
- 统计检测器作为 Agent Tools 保留；Counter Skill 和 Trace Diagnostics MCP 由 LLM 选择。
- 最终事件只能引用程序注册的真实候选，故障类型只能来自公开 Taxonomy。
- `answer.json` 已执行 v2 JSON Schema 和 Public 证据回查，密钥未写入项目或运行日志。
- Baseline 完整保留；LLM 不可用时显式返回 `LLM_UNAVAILABLE`，不会无提示降级冒充。

## 2. LLM 配置与调用证明

选手模型使用独立变量：

```text
AIOPS_AGENT_LLM_BASE_URL=https://yyt.s-flag.com/v1
AIOPS_AGENT_LLM_MODEL=deepseek-v4-flash
AIOPS_AGENT_LLM_API_KEY=<redacted>
```

实际文件：`/etc/aiops-competition/agent-llm.env`，权限 `0600 root:root`。示例文件：
`configs/agent-llm.env.example`，不含真实 Key。这些变量与 Judge 配置完全分离。

连接验证：

- `/v1/models`：HTTP 200，返回 `deepseek-v4-flash`。
- 原生 Tool Calling 能力探测：HTTP 200，`finish_reason=tool_calls`，模型选择
  `capability_probe`。首次实测耗时 3.189 s，用量 450 tokens。
- 运行证明：每次调用记录在各自 `llm_calls.jsonl`；不记录 Authorization 或 Key。

## 3. Agent 架构

```text
题目 + Metadata + 压缩 Topology + Fault Taxonomy
                         |
                         v
                 LLM 初始调查计划
                         |
                         v
       Plan -> Act -> Observe -> Verify 有界循环
          |        |             |
          |        |             +-- Candidate + 真实记录引用
          |        +-- Public API / Detector / Skill / MCP
          +-- LLM 选择工具和参数
                         |
                         v
           finalize_answer 受控收敛
                         |
                         v
        answer.json v2 -> Schema -> Evidence Validation
```

安全与资源控制：

- 默认 Public API 统一请求预算 55，其中开启 MCP 时为 MCP 预留 14 次；主 Agent 最多 41 次。分页最多 2 页，单页最多 500。
- Trace Diagnostics MCP 每次 Agent 运行最多执行 1 次、每个时间窗最多验证 6 条 Trace，防止子工具绕过总预算。
- LLM 最多 14 轮、48 次工具调用，为 `finalize_answer` 预留一次。
- 单次 LLM 超时 60 s，有限重试；总任务绝对截止时间 480 s。
- 工具结果输入 LLM 前截断并摘要，不传输数十万行原始记录。
- 拓扑从约 155 KB 原始内容压缩为服务边、Pod/节点和重启摘要，完整原文不重复进入每轮。
- 日志内容按不可信数据处理；工具实际执行由程序控制。
- 普通 Trace 不会仅因“存在”而判异常；定向采样兜底要求显式 `ERROR`
  且延迟至少 10 s。

## 4. Tool Registry

| 分类 | 工具 |
|---|---|
| Public 发现 | `list_cases`, `get_case_metadata`, `get_topology`, `get_fault_taxonomy` |
| Public 查询 | `query_metrics`, `query_logs`, `query_traces`, `get_trace` |
| 统计分析 | `analyze_metric_anomaly`, `analyze_log_anomaly`, `analyze_trace_latency`, `correlate_anomalies` |
| Skill | `run_skill` |
| MCP | `analyze_edge_latency` |
| 最终收敛 | `finalize_answer` |

每次 LLM 选择的工具、参数、状态、耗时和结果摘要记录于
`agent_tool_calls.jsonl`。程序校验 Case ID，不接受任意 SQL 或服务器文件路径。

## 5. 三组 SF-001 真实实验

三组均在 50 机运行，数据只来自 51 机 Public API；未读取 Private GT，未调用 Judge。

| 指标 | A Baseline | B LLM | C LLM + Skill + MCP |
|---|---:|---:|---:|
| 运行目录 | `runs/compare-baseline` | `runs/compare-llm-accepted` | `runs/compare-full-budgeted` |
| 模型 | 无 | `deepseek-v4-flash` | `deepseek-v4-flash` |
| LLM API 调用 | 0 | 15 | 14 |
| LLM 工具调用 | 0 | 38 | 42 |
| Skill 执行 | 0 | 0 | 1 |
| MCP 调用 | 0 | 0 | 1 |
| Public API 请求 | 52 | 55 | 55（主 Agent 41 + MCP 14） |
| 总耗时 | 8.838 s | 389.477 s | 223.550 s |
| 异常事件数 | 5 | 3 | 1 |
| 故障类型分布 | `UNKNOWN: 5` | `DependencyFailure: 3` | `ServiceCrash: 1` |
| Schema 校验 | 通过 | 通过 | 通过 |
| Public 证据校验 | 通过 | 通过 | 通过 |
| Token 用量 | 0 | 245,609 | 225,271 |
| 估算成本 | 0 | 未知 | 未知（供应商未提供价格） |

B 组耗时超过旧版 360 s 上限，原因是最终调用两次 60 s 供应商超时后第三次成功。
后续已把总时限实现为跨重试绝对截止时间，并将最终候选上下文压缩。

## 6. Skill 真实选择与执行

C 组开始时发现 `counter-rate-analysis`。LLM 自主选择
`run_skill(skill_name="counter-rate-analysis")`，程序动态载入
`detectors.metrics:detect_counter_rates`。`discovered`、`selected`、`executed` 三个阶段都保存在
`runs/compare-full-budgeted/skill_usage.json`。

最终受预算 C 组在调用 Skill 时，当轮已缓存指标中没有带
`metric_type=counter` 的序列，因此真实返回 0 个序列、0 个速率候选；程序没有按指标名猜测类型，也没有伪造结果。
为验证 handler 的正向速率路径，补充运行 `runs/compare-full-accepted/skill_usage.json`
已使用 20 个明确 Counter 序列产生 215 个有效相邻速率和 20 个候选，
0 次 Reset、0 个无效间隔、`counter_misuse_count=0`。该补充运行不作为最终 C 组的性能数据。

## 7. MCP 自主调用

`trace-diagnostics` 通过 stdio 成功 discovery，工具名 `analyze_edge_latency`。

LLM 生成了 `recommendation -> product-catalog` 的边和基线/分析时间窗，
MCP 真实查询两个时间窗各 6 条 Trace：基线 p95=1.34 ms，分析 p95=13.65375 ms，
比率 10.189366，返回 `SUPPORTED`。本次 MCP 耗时 1.958 s，内部使用 14 次 Public API 请求，
加上主 Agent 41 次后恰好为统一预算 55。

程序保留 MCP 参数、耗时、基线/分析样本、p95 比率、Trace ID、父子 Span 参考和
覆盖警告。如果 MCP 返回 `NOT_SUPPORTED`，LLM 不得把该边写成已验证延迟根因。

## 8. 答案与校验路径

- A：`runs/compare-baseline/answer.json`
- B：`runs/compare-llm-accepted/answer.json`
- C：`runs/compare-full-budgeted/answer.json`
- B 证据校验：`runs/compare-llm-accepted/evidence_validation.json`
- C 证据校验：`runs/compare-full-budgeted/evidence_validation.json`

最终答案包含 `case_id`、`schema_version=2.0`、`anomalies`、实体、Taxonomy 故障类型、UTC 时间和
带 `request_id + record_sha256` 的真实证据。

C 组本次在 `default` Service 的 Calico API Server 错误日志上输出 1 个
`ServiceCrash` 事件。Schema 和证据回查通过只能证明引用的 Public 记录真实存在；
在正式 GT 审核完成前，不把该故障标签视为已证明正确。

## 9. 运行和修改

完整模式：

```bash
cd /opt/aiops-competition/participant-starter
/opt/aiops-competition/.venv/bin/python main.py \
  --agent llm \
  --case SF-001 \
  --base-url http://192.168.51.51:8080 \
  --question-file examples/question.txt \
  --skills-dir skills \
  --mcp-config configs/mcp_servers.json \
  --run-dir runs/my-llm-run
```

Baseline：

```bash
/opt/aiops-competition/.venv/bin/python main.py \
  --agent baseline --no-skills --no-mcp \
  --case SF-001 \
  --base-url http://192.168.51.51:8080 \
  --question-file examples/question.txt \
  --run-dir runs/my-baseline-run
```

更换模型只修改独立 env 文件；改 Prompt/循环编辑 `agent/llm_core.py`；改模型适配编辑
`agent/llm_client.py`；改算法编辑 `detectors/`；新 Skill 通过 `SKILL.md` frontmatter 注册；
新 MCP Server 在 `configs/mcp_servers.json` 注册。

## 10. 测试

```bash
PYTHONPATH=. /opt/aiops-competition/.venv/bin/python -m unittest discover -s tests -v
```

结果：16 项全部通过。覆盖 429 有限重试、NULL 日志严重性、普通 Trace 不误报、
超长 ERROR Trace 候选、证据链、Skill 语义、MCP 发现/失败降级、LLM 配置分离和拓扑压缩。

## 11. 局限与后续建议

1. SF-001 正式 GT 尚未人工审核；本报告不宣称准确率提升，只验收可查询、自主工具调用和可校验输出。
2. 供应商偶发 60 s 读取超时；已保留有限重试和绝对任务截止时间。
3. 有界 API 采样不是全量扫描；输出只表示在当次预算内获得的已验证候选。
4. 当指标未声明 `metric_type=counter` 时，Skill 不按名称猜测类型；建议 Public API 始终保留指标类型。
5. 可继续加入消息压缩、跨轮摘要和供应商成本表，但不应放宽证据守卫或请求预算。

## 12. 未触碰范围

本次没有修改 51 服务器、Public 数据、评分器、Judge 模型配置或 SF-001 Private GT；
也没有重新实现 `submit.py`。
