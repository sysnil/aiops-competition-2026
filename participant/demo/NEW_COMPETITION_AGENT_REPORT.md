# 新版半决赛与决赛 Agent 改造报告（50 服务器）

验收日期：2026-10-17  
服务器：`192.168.51.50`  
项目：`/opt/aiops-competition/participant-starter/`  
结论：`NEW_COMPETITION_AGENT_DEMO_READY`

> 普通选手已可用默认确定性程序完成已发布的半决赛查询题：自动读题、查询 Public API、聚合、绘图、回答三问、Schema 校验并提交。原 Baseline/LLM/Skill/MCP 异常检测 Agent 完整保留。正式 Q01–Q10 和决赛轮次尚未开放，本报告不声称它们已通过真实比赛验收。

## 1. 备份与增量改造

改造前备份：

`/root/aiops-backups/new-competition-20261017T1400CST/`

包含 `participant-starter.tgz` 和 `etc-aiops-competition/`。原有 `runs/`、LLM 配置、队伍凭据、Skill、MCP、Web 历史与报告未清空。

实际修改：

- `main.py`：新增 `--task anomaly|qa`、`--question Q01|all`、`--credential-file`；保留原异常检测路径。
- `qa_solver.py`：读题、分页查数、确定性聚合、Matplotlib PNG、三问计算与产物落盘。
- `submit.py`：自动识别 QA v1，使用专用 QA 路由，查询单题分数与十题汇总。
- `schemas/qa-answer-v1.schema.json`：本地提交前校验。
- `requirements.txt`：固化 Matplotlib 依赖。
- `tests/test_qa_solver.py`：双曲线、窗口、样本数和连续区间算法回归。
- `webapp/backend.py`：Web 直接启动真实 QA/Agent，发布脱敏 SSE、产物和决赛轮次状态。
- `webapp/static/app.js`：新增“查询·图表·三问”模式、真实 PNG、三问答案、耗时与回放。
- `README.md`、`PARTICIPANT_HANDBOOK.md`、`webapp/README.md`：新赛制命令、产物和安全边界。

## 2. 选手命令与凭据

队伍 Token 由组委会预置于：

`/etc/aiops-competition/team.env`

`main.py` 和 `submit.py` 默认读取该文件，无需把 Token 写入命令行。手工 `curl` 前可用 `set -a; source ...; set +a` 加载当前终端，但不应输出 Token。

单题：

```bash
cd /opt/aiops-competition/participant-starter
source /opt/aiops-competition/.venv/bin/activate

python main.py \
  --task qa \
  --agent baseline \
  --mode practice \
  --case SF-001 \
  --question P-Q01 \
  --base-url http://192.168.51.51:8080 \
  --run-dir runs/P-Q01
```

当前场次全部已发布题：

```bash
python main.py \
  --task qa \
  --mode semifinal \
  --question all \
  --base-url http://192.168.51.51:8080 \
  --run-dir runs/semifinal-all
```

提交：

```bash
python submit.py \
  --mode practice \
  --case SF-001 \
  --answer runs/P-Q01/answer.json \
  --server http://192.168.51.51:8080
```

`main.py --help` 已实际显示 `--task {anomaly,qa}` 和 `--question`；`submit.py --help` 已说明 QA v1/异常 v3/历史 v2 的自动 Schema 选择。

## 3. 统计与绘图能力

Starter 不把大批原始数据交给 LLM 自由计算。关键数值由 Python 确定性产生：

| 题型 | 实现 |
|---|---|
| Pod 内存 | Gauge 按 UTC 分钟取 mean，Bytes 转指定单位 |
| Pod CPU | Counter 速率，分组、非负 delta、Reset 后从新值起算 |
| Service/Trace/日志计数 | 每分钟计数，依题面过滤 |
| p95 延迟 | 每分钟 linear percentile |
| 错误率 | 每分钟 `ERROR/total × 100%` |
| 调用边 | `service + parent_service + status/operation` 过滤后聚合 |
| 双 Service 对比 | 分别分页查询，生成两条序列与图例 |

三问通用计算原语包括：最大/最小值与时间、最大相邻变化、窗口均值/均值差、较高窗口、总量、原始记录数、样本数、多序列峰值/均值差、连续正值区间。结果可复算，文字 `explanation` 不会代替数值。

## 4. P-Q01 真实运行

最终部署版运行目录：

`/opt/aiops-competition/participant-starter/runs/new-competition-P-Q01/`

运行结果：

- Public API 请求：2（Question + Metrics）。
- 真实原始记录：120。
- 聚合图表点：31。
- 本地计算耗时：0.853 s。
- Schema：PASS。
- PNG SHA-256：`a0ac2669a5d7d6657790bcc6aae6cd935a455953d5b5193efb75a1bcd0f1843a`。

三问答案：

1. 每分钟平均工作集内存峰值 `13.341797 MiB`，时间 `2026-10-14T09:15:00Z`。
2. 最大相邻变化为 `+0.330079 MiB`，从 `09:23:00Z` 到 `09:24:00Z`。
3. 观察窗口对基线窗口的平均变化为 `-0.001709 MiB`。

同一答案的练习提交 `SUB-67766E360F7043B3B261` 得分 10.0/10.0：图表 4.0，三问各 2.0，状态 `PREVIEW_COMPLETED`，不进入正式排行榜。

每题目录包含：

- `answer.json`
- `chart.png`
- `chart_data.json`
- `query_records.json`
- `summary.json`

## 5. Web 大屏与回放

访问地址：`http://192.168.51.50:8088/`

服务命令：

```bash
sudo systemctl restart aiops-agent-web
sudo systemctl status aiops-agent-web --no-pager
sudo journalctl -u aiops-agent-web -f
```

新版 Web 保留原 FastAPI + React + ECharts 架构，没有重建第二套。半决赛可选“查询·图表·三问”或“异常检测 Agent”。后端以无 Shell 子进程调用同一 `main.py`，通过 SSE 发布真实 API/工具/产物事件。

Web 实际运行：`web-20261017T062720Z-d5950e`

- 状态：COMPLETE。
- 程序计算耗时：0.857 s；包含进程和事件落盘的 Web 墙钟时间约 2.44 s。
- API 请求：2，页面展示路径、HTTP 状态和耗时。
- 图表：直接展示运行生成的 PNG，不是预设动画。
- 回放：5 个结构化事件，标识为 `replay`，不冒充实时。
- Token：该确定性 QA 运行不调用 LLM，页面显示“未提供”而不伪造 0。

Web 已在 CSS 视口 `1920×1080` 下验收；应用内没有水平溢出。嵌入式浏览器导出图像为 `1684×1080`（宿主侧边栏占用宽度），截图保存于：

`/opt/aiops-competition/participant-starter/NEW_COMPETITION_QA_WEB_FINAL_1080P.png`

## 6. 原有异常检测 Agent 和决赛基础

`--task anomaly` 完整保留 Baseline、LLM、Counter Skill 和 Trace Diagnostics MCP。既有真实 SF-001 运行可在 Web 回放：

- `web-20261016T123618Z-852898`：Baseline，55 次 Public API，13 个异常，Schema/证据校验通过，18.819 s。
- `demo-real-sf001-llm-skill-mcp`：LLM 真实参与规划与工具选择，14 次 LLM 调用，1 次 Skill，3 条 MCP 生命周期记录，页面可显示真实 usage。

决赛基础任务是自主异常发现、实体、时间、证据和调查过程；根因实体/故障机制不再是强制输出。Web 已请求真实 `/v1/final/rounds/current`；当前得到 404，因此阻止启动并明示权限边界，不用模拟动画假装已有决赛。

## 7. 测试和回归

| 项目 | 结果 |
|---|---|
| Starter 单元测试 | 18/18 PASS |
| `main.py --help` / `submit.py --help` | PASS |
| P-Q01 真实 CLI | PASS |
| QA v1 JSON Schema | PASS |
| Public 记录/分页/图表点 | 120 / 正常 / 31 |
| 提交与预览评分 | 10.0/10.0 PASS |
| Web health / Question proxy / artifact | PASS |
| Web 1920×1080 DOM 与视觉验收 | PASS |
| practice cases/metadata/metrics | HTTP 200 |
| 无 Token | HTTP 401 |
| semifinal 闭场 | HTTP 403 |
| final 无轮次 | HTTP 404 |
| 旧 Baseline/LLM/Skill/MCP | 单元回归通过，真实历史运行可回放 |

## 8. 安全与可信计量

- Team Token 和 LLM Key 只由后端受保护文件读取，不发给浏览器。
- SSE 过滤 authorization/token/api_key/prompt/messages/content，不显示模型隐藏推理。
- Web 仅能执行参数经 Pydantic 约束的 `main.py`，不拼接 Shell。
- 本地 Token usage 只在服务商返回 `usage` 时累计 input/output/total；无 usage 显示“未提供”。
- 本地数据不是官方 Token 凭据；正式计量需 51 统一网关或经批准的服务商 usage 日志。

## 9. 复制到 17 台队伍服务器

1. 在发行包中同步 `main.py`、`qa_solver.py`、`submit.py`、`schemas/`、`webapp/`、文档和锁定依赖。
2. 在每台机器预置该队专属 `/etc/aiops-competition/team.env`，严禁复制 50 的组委会测试 Token。
3. 保留选手自己的 `/etc/aiops-competition/agent-llm.env`，不复制 Judge 凭据。
4. 用专用 `aiops-web` 用户部署 systemd，每台机器只绑定已批准的内网 IP。
5. 先运行 18 项单元测试，再验证本队 practice Question、一次 QA 运行、Web SSE 与无 Token=401。
6. 只由组委会在 51 修改场次/轮次状态；队伍机不持有开场权限。

## 10. 待验收项

1. 正式 Q01–Q10 尚无已批准的 Case/实体/窗口/三问计算定义，因此无法进行真实 10 题连续执行。`--question all` 功能已实现，但不会发现未发布模板。
2. 决赛尚无真实 OPEN 轮次、第二队 Token/来源 IP 和未公开 Case，未完成真实双机对战。
3. 正式决赛 Agent 尚需增加 `--mode final --round-id` 的专用客户端适配；在轮次未开放前本次没有用练习 Case 冒充。
4. 官方后台耗时/Token 计量、评委记录和双画面对比需在真实队伍环境再验收。

正式题和决赛未经人工审批、真实双队和可信计量验收前，应保持当前 DEMO READY 而不升级为正式 READY。
