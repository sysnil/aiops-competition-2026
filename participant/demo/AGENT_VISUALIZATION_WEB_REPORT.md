# AIOps Agent 比赛可视化 Web 交付报告

生成时间：2026-10-16（Asia/Shanghai）  
部署目标：`192.168.51.50`  
Web 地址：`http://192.168.51.50:8088/`

## 1. 交付结论

已在 50 测试机完成 FastAPI + React + ECharts 独立 Web 应用的开发、部署和浏览器验收。现有 Baseline/LLM Agent、Skill、MCP、Public API Client、`answer.json` 和 `submit.py` 均未被替换；Web 启动真实 Agent 时直接调用原 `/opt/aiops-competition/participant-starter/main.py`。

应用服务、实时 SSE、历史运行、4× 回放、Metrics/Logs/Trace 图表、证据派生拓扑、答案卡片、实际 Token usage、深浅色主题、全屏入口和决赛 RCA 占位界面均已实现。

当前组委会场次及测试身份状态为：

| 模式 | 51 返回 | 结论 |
|---|---|---|
| practice（普通队伍） | HTTP 403 `PRACTICE_NOT_OPEN` | 全局仍为 CLOSED |
| practice（50 的 `ORGANIZER-TEST`） | HTTP 200 | 仅绑定 IP `192.168.51.50` 的闭场测试通道 |
| semifinal | HTTP 403 `SESSION_UNASSIGNED` | 队伍场次未分配，不绕过 |

经管理员明确批准，51 使用既有 `is_organizer_test` 和 `assigned_ip` 字段启用了严格限定的闭场练习测试：只有 `ORGANIZER-TEST` 且 Nginx 记录的真实来源 IP 与 `192.168.51.50` 完全一致时可以访问；普通队伍、错误来源地址和 semifinal 仍然被拒绝。测试 Token 已轮换并安装在 50 的 root-owned 配置中，未发送到浏览器。

启用后已重新跑通一场真实 SF-001 Baseline Web 全流程：运行 `web-20261016T123618Z-852898` 完成 55 次 Public API 请求、64 条实时事件、13 个异常、Schema 与 Public 证据校验，耗时 18.819 秒。随后通过原 `submit.py` 提交 practice，得到 `PREVIEW_COMPLETED`；预评测 20.77/100，不进入正式排行榜。完整 LLM+Skill+MCP 展示仍可回放既有真实运行。

## 2. 架构与数据流

```text
浏览器 React/ECharts
   │ POST /api/runs；GET SSE /events
   ▼
FastAPI Web（aiops-web 低权限账户）
   │ asyncio.create_subprocess_exec（无 shell）
   ▼
现有 main.py → 现有 Tools / Skill / MCP → 51 Public API
   │
   ├─ 原生 answer.json / summary.json / validation.json
   └─ API、Tool、LLM、Skill、MCP JSONL
          │ 增量读取与脱敏摘要
          ▼
   web_events.jsonl → SSE → 实时界面 / 赛后回放
```

实时执行使用 `delivery_mode=live`；历史运行使用 `delivery_mode=replay`。前端不会显示模型 Prompt、Messages、隐藏推理、Team Token、LLM Key、Judge 凭证或 Private GT。

## 3. 主要界面与交互

- 适配 1920×1080 大屏的三栏布局：运行控制、调查证据、最终结论/工具流。
- 半决赛：practice/semifinal、Case ID、Baseline/LLM、Skill/MCP、诊断问题、启动、停止。
- 决赛：预留根因实体、原因、证据、执行过程、计时和官方 Token 接口位置；当前启动按钮禁用，API 返回 409 `FINAL_RCA_NOT_IMPLEMENTED`。
- 实时调查：步骤、LLM 规划、工具参数、API 路径/状态/耗时、Skill 生命周期、MCP 诊断、证据验证。
- 多模态区：Metrics 折线、Logs 真实代表记录、Trace Waterfall、异常与证据。
- 拓扑：只从真实 Trace 代表记录和答案证据推导，并标记 `PUBLIC EVIDENCE DERIVED`。
- 历史：运行列表、可信摘要、4× 完整回放、从头回放。
- 主题：现代深色大屏和浅色主题；提供浏览器全屏按钮。

## 4. 真实 SF-001 回放证据

用于验收的历史运行：

`/var/lib/aiops-agent-web/runs/demo-real-sf001-llm-skill-mcp/`

来源是既有真实运行 `runs/compare-full-accepted` 的只读可视化副本。统计如下：

| 指标 | 实际值 |
|---|---:|
| 模型 | `deepseek-v4-flash` |
| LLM API 调用 | 14 |
| Agent Tool Call | 35 |
| Public API 请求 | 40 |
| Skill 执行 | 1（`counter-rate-analysis`） |
| MCP 调用 | 3（`analyze_edge_latency`） |
| 运行耗时 | 235.957 s |
| 最终异常 | 3 |
| Schema | 通过 |
| Public 证据校验 | 通过，6/6 引用有效 |
| Input Tokens | 179,528 |
| Output Tokens | 15,463 |
| Total Tokens | 194,991 |

Token 来自实际模型响应 `usage` 累计，没有价格时不估算成本；无 usage 的运行显示“未提供”，不显示伪造的 0。

Web 从真实工具输出的 `representative_records` 得到 56 条 Metrics、20 条 Logs、10 条 Trace 代表记录，用于有界可视化；没有向浏览器发送几十万原始记录。

## 5. 实时事件 JSONL

文件：`/var/lib/aiops-agent-web/runs/<run-id>/web_events.jsonl`

```json
{
  "seq": 3,
  "ts": "2026-10-16T04:54:07.858408Z",
  "type": "api.request",
  "phase": "OBSERVE",
  "status": "403",
  "source": "agent",
  "delivery_mode": "live",
  "payload": {
    "request_id": "req-0001",
    "method": "GET",
    "path": "/v1/practice/cases",
    "params": {},
    "latency_ms": 53.827,
    "status": 403
  }
}
```

事件类型包括：`run.started`、`process.started`、`llm.call`、`tool.call`、`api.request`、`skill.lifecycle`、`mcp.call`、`evidence.validation`、`diagnosis.final`、`run.finished`。SSE 支持 `after` 续读以及 `replay/speed` 参数。

## 6. 安全与兼容性

- 监听 `192.168.51.50:8088`，未绑定公网地址，未修改防火墙。
- Team Token 与选手 LLM 配置只由后端读取；env 文件为 `0640 root:aiops-web`，目录为 `0750 root:aiops-web`。
- 不读取或暴露 Private GT、Judge Key、故障注入计划。
- 不修改 51 API、评分器、Public 数据或数据库。
- Agent 命令由参数数组启动，不使用 Shell；Case、模式、问题长度和请求预算经过 Schema 校验。
- 一次只允许一个活动 Agent；停止操作覆盖整个进程组。
- systemd 使用专用 `aiops-web` 用户、`NoNewPrivileges`、`ProtectSystem=strict`、只读项目/配置和独立可写状态目录。
- 同源部署，无 CORS；已启用 CSP、`X-Frame-Options`、`X-Content-Type-Options`、`Referrer-Policy`。
- `answer.json` 完全由原 Agent 生成，Web 只读展示，格式与 CLI 兼容。

## 7. 实际验收结果

| 项目 | 结果 |
|---|---|
| systemd 服务 | PASS，active/running |
| `/api/health` | PASS；Team Token/LLM 配置均检测为已配置，但不返回内容 |
| React/ECharts 静态资源 | PASS；本地固化，无运行时 CDN 依赖 |
| Web 真实启动原 Agent | PASS；新运行完成 55 次真实 API 请求 |
| SSE 实时事件 | PASS；启动、API、完成状态逐步出现 |
| 历史完整回放 | PASS；98 个结构化事件，最终 `replay_complete` |
| Skill 展示 | PASS；发现、LLM 选择、handler 执行结果均可见 |
| MCP 展示 | PASS；3 次真实 `analyze_edge_latency` 可见 |
| Metrics 图 | PASS；真实 Public 证据折线 |
| Logs | PASS；真实代表日志可见 |
| Trace Waterfall | PASS；真实 Span 时延与操作名 |
| 服务拓扑 | PASS；由 Trace parent/service 关系推导 |
| 最终答案 | PASS；3 个事件、故障类型、时间与证据可见 |
| Token usage | PASS；input/output/total 分开展示 |
| 深浅色 | PASS |
| 决赛占位保护 | PASS；UI 禁用启动，后端 409，不虚构 |
| 1080P 浏览器验证 | PASS；以 1920×1080 浏览器视口执行测试 |

50 测试机的 practice 全流程已经通过。普通队伍的 practice 全局状态仍为 CLOSED，semifinal 仍未分配；测试通道不代表正式比赛开放。

## 8. 路径、文件与命令

项目目录：`/opt/aiops-competition/participant-starter/webapp/`

主要新增文件：

- `webapp/backend.py`：FastAPI、进程控制、事件采集、SSE、历史与可视化摘要。
- `webapp/static/index.html`：应用入口。
- `webapp/static/app.js`：React UI、控制、SSE、回放和 ECharts 配置。
- `webapp/static/styles.css`：大屏与主题样式。
- `webapp/static/vendor/`：本地 React/ReactDOM/ECharts。
- `webapp/deploy/aiops-agent-web.service`：服务单元。
- `webapp/README.md`：运行、事件与复制说明。
- `AGENT_VISUALIZATION_WEB_REPORT.md`：本报告。

运行目录：`/var/lib/aiops-agent-web/runs/`

```bash
sudo systemctl start aiops-agent-web
sudo systemctl restart aiops-agent-web
sudo systemctl status aiops-agent-web --no-pager
sudo journalctl -u aiops-agent-web -f
curl http://192.168.51.50:8088/api/health
```

部署前备份：`/opt/aiops-competition/participant-starter/backups/visualization-web-20261016T045010Z/`

## 9. 截图

服务器保存目录：`/opt/aiops-competition/participant-starter/reports/agent-visualization-web/`

- `AGENT_WEB_REAL_RUN_1080P.png`：真实工具调用回放中。
- `AGENT_WEB_FINAL_RESULT_1080P.png`：最终异常与证据。
- `AGENT_WEB_METRICS_1080P.png`：Metrics 趋势。
- `AGENT_WEB_TRACE_1080P.png`：Trace Waterfall 与证据派生拓扑。

## 10. 向 17 台队伍服务器复制

建议将 `webapp/` 制作为只读版本包并记录 SHA-256，然后逐台执行：

1. 复制到各机相同项目路径，保留每台机器原 Agent、Token 和选手 LLM 配置。
2. 把 unit 的 `--host` 设置为该机获批的比赛内网地址；不得使用公网地址。
3. 创建独立 `aiops-web` 系统账户和 `/var/lib/aiops-agent-web/runs`。
4. 只给 env 文件最小组读取权限，不把 Key 写进应用包。
5. 启用 systemd，并逐台检查 health、场次授权、SSE、完整 Agent、停止和回放。
6. 通过负载均衡或统一入口前，先确认 17 台网络边界、Host 策略和组委会规则；本轮没有擅自新增网络开放。

复制 Web 不会复制 Private GT、Judge 配置、51 数据或历史答案。正式比赛可选择不预装演示历史记录，只保留各队本机产生的运行。

## 11. 后续接入点

- 可使用 50 专用测试身份补做一场新的在线 LLM+Skill+MCP Web 全流程截图；该身份不得复制到参赛队伍服务器。
- 决赛服务端可沿用现有事件 envelope，新增 `rca.hypothesis`、`rca.action`、`rca.result` 和官方计量事件。
- 官方 Token/计时接入时，应由组委会服务端签名记录；前端现有 KPI 可直接消费，不把客户端值当正式计量。
- 如需跨网段访问，应由管理员审批 Nginx/TLS/防火墙方案，本轮不自行扩大监听边界。
