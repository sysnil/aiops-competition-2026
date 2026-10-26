# 院智能运维技能大赛：50 统一客户端升级报告

执行日期：2026-10-15（Asia/Shanghai）  
服务器：`192.168.51.50`  
最终状态：`ACCESS_CONTROL_READY` / `PRACTICE_READY` / `SEMIFINAL_RELEASE_PENDING`

## 1. 升级结果

- `main.py` 新增 `--mode practice|semifinal` 和 `--team-token`，默认安全模式为 `practice`。
- Agent API Client 对所有查询发送 `Authorization: Bearer ...`，自动使用 `/v1/practice` 或 `/v1/semifinal`。
- Tool Registry 已全部适配 mode；Skill 仍通过主客户端数据执行。
- MCP Client 把 mode 和 Token 以子进程环境传递给 Trace Diagnostics Server，MCP 不再使用匿名共享客户端。
- `submit.py` 新增相同参数，提交/轮询分别进入对应命名空间，输出 mode、team_id、Submission ID、状态和分项得分。
- Token 优先级：显式 `--team-token` > `AIOPS_TEAM_TOKEN`；无 Token 显式返回 `AIOPS_AUTH_REQUIRED`，不降级为匿名。

## 2. 修改文件

- `main.py`
- `submit.py`
- `client/public_api.py`
- `agent/core.py`
- `agent/llm_core.py`
- `agent/tool_registry.py`
- `agent/mcp_client.py`
- `mcp_servers/trace_diagnostics.py`
- `README.md`
- `PARTICIPANT_HANDBOOK.md`

回滚备份：`/opt/aiops-competition/participant-starter/backups/access-control-20261015/`。

## 3. 凭证配置

50 仅安装组委会测试身份：

```text
/etc/aiops-competition/team.env
```

权限为 `0600 root:root`，与选手 LLM 配置和组委会 Judge 凭证完全分离。运行时：

```bash
set -a
source /etc/aiops-competition/team.env
set +a
```

验收运行目录扫描结果为 `token_log_hits=0`；API、Agent、MCP 和提交日志均未记录 Authorization Header 或完整 Token。

## 4. 实际 --help 摘要

`main.py --help`：

```text
--agent {baseline,llm}
--case CASE
--base-url BASE_URL
--mode {practice,semifinal}
--team-token TEAM_TOKEN
--skills-dir SKILLS_DIR
--mcp-config MCP_CONFIG
```

`submit.py --help`：

```text
--case CASE
--answer ANSWER
--server SERVER
--mode {practice,semifinal}
--team-token TEAM_TOKEN
--output OUTPUT
```

两个命令均已在 50 的 `/opt/aiops-competition/.venv/bin/python` 中实际执行。

## 5. 真实练习题端到端

运行目录：

```text
/opt/aiops-competition/participant-starter/runs/access-control-practice-e2e-v2/
```

实际结果：

- 使用 `--agent baseline --mode practice --case SF-001`真实查询 51。
- 55 个主 API 请求；最终 13 个异常候选；`evidence_valid=true`。
- `counter-rate-analysis` 已 discovered/selected/executed 各 1 次。
- MCP `analyze_edge_latency` 已通过认证路由真实执行 1 次，调用状态 `ok=true`。
- 结果文件：`answer.json`、`grading-result.json`、`api_requests.jsonl`、`skill_usage.json`、`mcp_calls.jsonl`。
- Submission ID：`SUB-C492D6A946A442B0A09A`。
- 评分状态：`PREVIEW_COMPLETED`；返回内容明确标识为草案 GT 预评测，不进入正式排行榜。

## 6. 权限和回归

- 50 客户端单元测试：16/16 通过。
- 无 Token 运行 `main.py`：退出码 1，显式 `AIOPS_AUTH_REQUIRED`。
- 练习开放时合法组委会身份返回 200；关闭后返回 403。
- 无 Token 和错误 Token 均为 401。
- 组委会身份访问未分配正式场次为 403。
- 旧 `/v1/cases` 匿名访问为 401。
- 50 直连 51 的 8000 内部端口失败。

## 7. 选手运行方式

练习：

```bash
python main.py --agent llm --mode practice --case SF-001 \
  --base-url http://192.168.51.51:8080 \
  --question-file examples/question.txt --skills-dir skills \
  --mcp-config configs/mcp_servers.json --run-dir runs/practice-sf001
```

提交：

```bash
python submit.py --mode practice --case SF-001 \
  --answer runs/practice-sf001/answer.json \
  --server http://192.168.51.51:8080 \
  --output runs/practice-sf001/grading-result.json
```

正式比赛只需改为 `--mode semifinal`和已发布的 SF-003～SF-012，但当前不具备真实运行条件。

## 8. 遗留项与开放前检查

1. 组委会向每队安全交付独立 Token，并指导存入队伍服务器的受保护环境文件。
2. SF-002 发布后重做真实查询、Agent、v3 提交和 PREVIEW 回归。
3. 官方确认 10 月 21 日具体开放时刻前，不保持练习接口开放。
4. 正式赛前配置 AM/PM 队伍、发布真实 Case、批准 GT，然后用真实场次身份完成生产验收。
5. 当前状态为练习已关闭、正式场次未开放，因此保持 `SEMIFINAL_RELEASE_PENDING`。

