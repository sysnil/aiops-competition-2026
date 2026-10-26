# 院智能运维技能大赛：51 服务器统一访问控制升级报告

执行日期：2026-10-15（Asia/Shanghai）  
服务器：`192.168.51.51`  
最终状态：`ACCESS_CONTROL_READY` / `PRACTICE_READY` / `SEMIFINAL_RELEASE_PENDING`

## 1. 结论

- 17 支队伍 `TEAM-01`～`TEAM-17` 及 50 组委会测试身份已建立，每个身份使用独立高强度 Bearer Token。
- 服务端注册表只保存 scrypt salt/hash，不保存原始 Token；原始凭证束仅位于 root-only 路径。
- 数据和提交已分离为 `/v1/practice` 和 `/v1/semifinal`。历史 `/v1/cases` 仅是受相同鉴权和开放状态约束的练习别名，不能绕过正式题权限。
- 所有队伍场次仍为 `UNASSIGNED`；练习模式最终为 `CLOSED`；AM/PM 均为 `NOT_OPEN`；正式题发布列表为空。
- SF-001 已使用 50 的独立组委会身份完成真实查询、Agent、Skill、MCP、v3 提交和 PREVIEW 反馈闭环；验收后已重新关闭。
- SF-002 数据尚未发布；SF-003～SF-012 尚无真实发布数据和场次授权，不将合成测试冒充生产验收。

## 2. 修改文件

- `app/access_control.py`：Token、mode、session、开放状态和 Case 发布门禁。
- `app/main.py`：双命名空间数据 API、旧路由兼容门禁、身份级限流与审计。
- `app/security.py`、`app/settings.py`、`config/config.yaml`：队伍身份字段和安全配置路径。
- `grading/submission_api/router.py`、`store.py`：练习/正式提交路由、服务端绑定身份、数据库增量迁移。
- `config/nginx-aiops-contest.conf`：新提交路由的 POST/GET 白名单。
- `scripts/provision_team_registry.py`：首次生成哈希注册表和一次性凭证束。
- `scripts/manage_competition.py`：场次分配、开放/关闭、Case 发布、Token 撤销/激活/轮换。
- `scripts/test_competition_access.py`、`grading/tests/test_access_control.py`：17 队并发和合成场次权限回归。

## 3. API 路由对照

| 用途 | 旧路由 | 新路由 |
|---|---|---|
| Case 列表/数据 | `/v1/cases...` | `/v1/practice/cases...` 或 `/v1/semifinal/cases...` |
| 提交/轮询 | `/v1/submissions...` | `/v1/practice/submissions...` 或 `/v1/semifinal/submissions...` |
| 异常分类 | `/v1/anomaly-taxonomy` | 保持不变 |
| 健康检查 | `/healthz` | 保持不变 |

新旧数据路由均不信任 `X-Team-ID`。无 Token 和错误 Token 返回 401；已鉴权但模式/场次未开放返回 403；未发布或无权题目统一返回不泄露存在性的 404。

## 4. 队伍和 Token 管理

- 注册表：`/etc/aiops-contest/team_registry.json`，`0640 root:aiops-api`。
- 竞赛状态：`/etc/aiops-contest/competition_state.json`，`0640 root:aiops-api`。
- 原始凭证束：`/root/aiops-team-credentials-20261015.json`，`0600 root:root`。
- 注册表实际有 19 条：17 个正式队伍、1 个组委会测试身份、1 个历史凭证兼容身份。
- 原始 Token 不存在注册表、应用日志或报告中。凭证束需由组委会通过受控渠道分拆交付，交付后应转移到安全凭证系统并删除临时副本。

管理示例：

```bash
PYTHONPATH=/opt/aiops-contest-server python3 scripts/manage_competition.py assign-session TEAM-01 AM
PYTHONPATH=/opt/aiops-contest-server python3 scripts/manage_competition.py set-state semifinal OPEN --session AM
PYTHONPATH=/opt/aiops-contest-server python3 scripts/manage_competition.py token TEAM-01 revoke
```

场次开放前必须先完成队伍分组和正式 Case 发布审批；本轮未执行这些生产动作。

## 5. 数据库迁移和历史兼容

`submissions` 表以非破坏性 `ALTER TABLE` 新增：

- `competition_mode`
- `session`
- `token_id`

原有 16 条历史记录未改写，保留 `LEGACY`；其中 8 条 v2 仍存在。使用原历史凭证实测读取 SF-001 v2 提交 `SUB-C50C06D01A7E403A94B0` 返回 200，状态和内容未被迁移改写。新增 18 条 PRACTICE 验收记录不计入正式提交次数。迁移后的字段总数和分组已直接从 SQLite 复核。

回滚备份：`/opt/aiops-contest-server/backups/access-control-20261015/`，包含代码、配置、Nginx 配置和迁移前 SQLite。回滚需先停服务，由管理员显式执行，本轮未自动回滚。

## 6. 真实和合成验收

### 6.1 生产数据闭环

- 50 到 51 的 `/v1/practice/cases` 在手动开放后返回 200。
- Baseline + Skill + MCP 实际运行 SF-001：55 个主 API 请求，Skill 选择/执行各 1 次，MCP `analyze_edge_latency` 真实调用 1 次，最终 13 个异常候选，公开证据校验通过。
- 真实提交 `SUB-C492D6A946A442B0A09A`，返回 `PREVIEW_COMPLETED`，并明确标注不进入正式排行榜。
- 验收后练习状态已恢复 `CLOSED`。

### 6.2 17 队身份级并发

结果保存于 `reports/access-control-17-team-test.json`：

- 17/17 组合查询成功，覆盖 cases、metadata、topology、metrics、logs、traces 和 trace detail。
- 延迟：p50 1120.126 ms，p95/p99 1264.776 ms（单源 IP 同机并发测试）。
- 17/17 练习提交被接收且到达终态；队伍隔离读取返回 404。
- 伪造 `X-Team-ID` 无法更改服务端身份；错误 Token 返回 401。
- 首次把 17 个 POST 压在同一源 IP 的同一瞬间时，Nginx 正常 burst 限流生效；改为有界 2 并发后全部通过。这是测试方法修正，未放宽生产限流。

### 6.3 权限矩阵

- 50 实测：无 Token=401，错误 Token=401，有效 Token+练习已关闭=403，未分配正式场次=403，旧匿名路由=401。
- 50 直连 `192.168.51.51:8000` 失败，内部 FastAPI 仍只绑定 `127.0.0.1:8000`。
- 6 项 AccessController 合成回归覆盖 AM/PM 切换、未分配、未开放、未发布、撤销 Token 和伪造队伍头。
- 正式场次“可成功查询已发布真题”仅做了合成权限层验证，真实生产验收保持 PENDING。

## 7. 测试和服务状态

- 51 评分/鉴权回归：59/59 通过。
- Nginx 配置：`nginx -t` 通过。
- `aiops-contest-api`、`aiops-private-grader-demo`、`nginx`：均为 active。
- `/healthz`：200，API 版本 `2.0.0`。
- SF-001 GT 草案、Public 数据和历史评分结果未被改写。

## 8. 10 月 21 日前检查清单

1. 人工核对 17 支队伍名称、IP 和 Token 交付人。
2. 通过受控渠道逐队交付 Token，不在群聊或手册中发布。
3. 完成 SF-002 Public 数据、Manifest、v3 证据回归和 GT 状态检查后再发布 SF-002。
4. 组委会确认练习题具体开放时刻；使用 `set-state practice OPEN`手动开放。
5. 监控 Nginx 429、API 429/5xx、DuckDB 内存、评分队列和 Judge 失败类型。
6. 练习结束立即设为 `CLOSED`，并复核访问日志中没有凭证泄露。

## 9. 剩余阻断

- 上/下半场队伍名单与开放时间未确认。
- SF-002 未发布。
- SF-003～SF-012 真实 Public 数据未发布，正式 GT 批准状态未满足。
- 因此不能升级为正式发布就绪；状态保持 `SEMIFINAL_RELEASE_PENDING`。
