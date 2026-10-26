# 院智能运维技能大赛：50 服务器决赛 Agent 与 Web 全链路验收报告

> 验收日期：2026-10-19（UTC+8）  
> 执行机：`192.168.51.50`  
> 项目：`/opt/aiops-competition/participant-starter/`  
> Web：`http://192.168.51.50:8088/`  
> 比赛 API：`http://192.168.51.51:8080`

## 1. 结论

本轮已完成决赛 Agent 模式、Final answer v1、真实图表、任务级 API 适配、Web 决赛上半场/PK 视图、实时事件和历史回放的实际开发与部署。FT-01～03 和 PK-R01～04 均已使用 51 的真实 Public API 运行 Baseline；FT-01～03 另完成了真实 LLM 对照运行。

当前状态为 **FINAL_STAGE1_AGENT_WEB_E2E_PARTIAL**，不应标记为正式赛前验收通过，原因是：

- FT-01、FT-02、FT-03 在组委会测试身份下的提交次数已用尽，无法在本轮重取三题分数。
- PK-R03 真实预览评分为 `0.75 / 100`，说明默认 Baseline 的实体、类型和时间对齐能力仍很弱；不得把 Schema/证据校验通过等同于检测正确。
- 51 当前无活动 Final/PK Round，`current round` 返回 `ROUND_NOT_FOUND`。
- 尚无第二台真实选手 VM，Web 只显示本机队位，对手队位明确标记“未连接第二台 VM”。

全程未修改 51 题目、GT、评分规则或开放状态，未向 17 台选手机部署。

## 2. 新增决赛 Agent 模式

CLI 新增：

```bash
python main.py --task final_qualifier --agent baseline --final-task FT-01 \
  --base-url http://192.168.51.51:8080 --mode final --run-dir runs/example

python main.py --task final_pk --agent llm --final-task PK-R01 \
  --base-url http://192.168.51.51:8080 --mode final --run-dir runs/example-pk
```

关键实现：

- 任务、Case、UTC 窗口、模态和开放状态由 `GET /v1/final/tasks` 及任务详情动态发现，不在 Agent 中硬编码正式 Case。
- Final Public 查询映射到 `/v1/final/tasks/{task_id}/case/*`，不读取 Parquet、staging 或 Private GT。
- 输出 `task_type=FINAL_ANOMALY_INVESTIGATION`、`schema_version=final-1.0`；支持最多 32 个异常和 8 张图。
- 图表使用真实证据记录生成，内含 PNG SHA-256 和 base64 artifact，字体使用 Noto CJK。
- `root_hypothesis` 默认为 `null`，不从普通异常观测中伪造 RCA。
- provider 未返回 usage 时显示“未提供”；Baseline 不伪造 0 token。
- LLM 连续忽略最终工具且无任何统计候选时，由程序控制器记录 `bounded_safe_abstention`，生成 0 事件，不超时、不造假。

## 3. 真实任务映射与状态

| 任务 | Case | 种类 | 51 状态 | 身份 |
|---|---|---|---|---|
| FT-01 | SF-006 | 上半场 | NOT_OPEN | ORGANIZER-TEST internal preview |
| FT-02 | SF-011 | 上半场 | NOT_OPEN | ORGANIZER-TEST internal preview |
| FT-03 | SF-007 | 上半场 | NOT_OPEN | ORGANIZER-TEST internal preview |
| PK-R01 | SF-004 | PK | NOT_OPEN | ORGANIZER-TEST internal preview |
| PK-R02 | SF-008 | PK | NOT_OPEN | ORGANIZER-TEST internal preview |
| PK-R03 | SF-009 | PK | NOT_OPEN | ORGANIZER-TEST internal preview |
| PK-R04 | SF-012 | PK | NOT_OPEN | ORGANIZER-TEST internal preview |

Web 只展示 51 对当前身份返回的任务，普通身份不能借助前端越过 NOT_OPEN。

## 4. Baseline 实际运行

| 任务 | Case | 耗时(s) | Public API 请求 | 异常 | 图表 | Schema | 证据 |
|---|---:|---:|---:|---:|---:|---|---|
| FT-01 | SF-006 | 7.799 | 27 | 1 | 1 | PASS | PASS |
| FT-02 | SF-011 | 7.457 | 27 | 5 | 3 | PASS | PASS |
| FT-03 | SF-007 | 7.678 | 27 | 7 | 4 | PASS | PASS |
| PK-R01 | SF-004 | 8.742 | 28 | 1 | 1 | PASS | PASS |
| PK-R02 | SF-008 | 6.978 | 27 | 0 | 0 | PASS | PASS |
| PK-R03 | SF-009 | 7.615 | 27 | 6 | 3 | PASS | PASS |
| PK-R04 | SF-012 | 6.686 | 27 | 6 | 3 | PASS | PASS |

PK-R04 第一次在 metadata 阶段命中 429 并正确失败，待限流窗口恢复后串行重试成功。没有发起多队并发压测。

示例（仅表示 Agent 观测，不表示与 GT 匹配）：FT-03 第一条事件是 Pod `grafana-7fd8f8759f-gfb8z` 的 `CPU_USAGE_INCREASE`，关联 2 条 Public 证据；对应图表为 1188×528 PNG，标注 UTC、指标和实体。

## 5. LLM / Skill / MCP 实际运行

| 任务 | 模型 | 耗时(s) | API | LLM 调用 | input | output | total | Skill | 结果 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| FT-01 | deepseek-v4-flash | 125.377 | 32 | 9 | 115,017 | 8,054 | 123,071 | 1 | 0 事件，安全弃权 |
| FT-02 | deepseek-v4-flash | 165.974 | 25 | 9 | 127,869 | 11,237 | 139,106 | 1 | 0 事件，安全弃权 |
| FT-03 | deepseek-v4-flash | 92.625 | 22 | 9 | 92,108 | 7,126 | 99,234 | 0 | 0 事件，安全弃权 |

- 三次完成运行均使用 provider 返回的真实 usage，未估算费用。
- FT-01 先前的诊断运行实际调用 18 次 LLM、44 次工具、2 次 Skill 和 1 次 MCP，总 usage 494,826 tokens；该运行因模型未发出 finalize 而失败，促成了有界收敛修复。
- Skill 仅对真实 Counter 样本运行 handler，无适用样本时返回 0 candidate。
- MCP 只返回真实 Server 结果；遇到 429/服务失败时记录失败，不生成伪造结果。本轮补充了 429/502/503/504 最多 3 次的有界退避，并修正 MCP `isError` 的成功/失败标记。

结论：LLM 确实参与规划和工具选择，但本轮未生成高置信候选，不能声称优于 Baseline。

## 6. Submission 与 Score Feedback

| 任务 | 提交结果 | 分数/说明 |
|---|---|---|
| FT-01 | `SUBMISSION_LIMIT_REACHED` | 本轮前测试身份的 1 次限额已用尽 |
| FT-02 | `SUBMISSION_LIMIT_REACHED` | 同上 |
| FT-03 | `SUBMISSION_LIMIT_REACHED` | 单次尝试后停止，未绕过限额 |
| PK-R03 | `PREVIEW_COMPLETED` | Submission `FSUB-94363F512897462B9D93`，`0.75 / 100` |

PK-R03 评分摘要：GT 16 事件，预测 6 事件进入事件级明细；部分证据和时间项得分，实体和类型对齐很差。原始返回保存于：

`runs/final-pk-r03-baseline-e2e/submission.json`

`submit.py` 已修复 Final 响应的嵌套 `grade.score` 展示；修复前 CLI 误显示 0，修复后正确显示 0.75，原始 JSON 从未被改写。

## 7. Web 验收

### 7.1 功能

- `aiops-agent-web.service` 已重启并处于 `active`。
- `/api/health` 返回 `status=ok`，Token 和 LLM 均仅在后端配置。
- `/api/final/tasks` 动态返回 3 道 FT 和 4 道 PK；前端为下拉框，不允许手工构造正式题号。
- `/api/final/topology?task_id=FT-01` 返回 26 节点、25 边；前端 ECharts canvas 实际渲染为 530×300 CSS px。
- 决赛上半场页显示三题状态、当前 Agent、异常数和完成状态。
- PK 页显示当前 Round 为 NOT OPEN、四道内部任务、本机队位和未连接对手队位。
- FT-03 回放显示 23+真实事件、27 次 API、7 个异常、4 张图、证据 PASS；回放有“延迟回放”标记，不伪装实时。
- 4 张 FT-03 图均完整加载，自然尺寸均为 1188×528。
- 历史运行与新运行按 run directory 隔离，未覆盖半决赛记录。

### 7.2 截图和布局

已用 1920×1080 视口实际验证页面 DOM、动态下拉框、回放、图表和拓扑渲染。本轮 Codex 内置浏览器的 screenshot adapter 连续返回 `Unable to capture screenshot`，因此没有用伪造或旧版图片充当新截图。页面已在应用内浏览器中保持可见，这是一项交付缺口，建议组委会在正式投屏机上手工截取两张图：

1. 决赛上半场三题概览。
2. PK 模式 FT/PK 回放、拓扑、异常图表和右侧结果卡。

旧版 Web 截图仍保留在 `reports/agent-visualization-web/`，本报告不将其当作新版决赛页面证据。

## 8. PK 双队展示

当前只有 50 一台测试机，故实现了可迁移的双队布局和权限接入点，但只显示：

- 本机测试队：真实运行/回放。
- 对手队位：“未连接第二台 VM”。

未用单机模拟数据冒充两台选手 VM 并发验收。R05～R08 未出现在当前授权任务列表，51 也没有活动 Round，故本轮不伪造其轮次流程。

## 9. 安全、资源与可迁移性

- Token/LLM Key 仅从 `/etc/aiops-competition/*.env` 后端读取，不返回浏览器。
- Web systemd 单元启用 `ProtectSystem=strict`、`NoNewPrivileges=true`，只写 `/var/lib/aiops-agent-web`。
- Public API 存在请求预算、单页上限、Trace 详情上限和总超时。
- MCP 新增的退避最多 3 次，单次等待上限 2s，不无限重试。
- 新模块位于 Starter 内，不导入 `organizer-reference`；未把 GT、参考补丁或组委会 Token 写入代码。
- 此次不制作镜像、不清理 50、不复制到选手机。

## 10. 回归测试

- Python 编译：PASS。
- JavaScript 语法检查：PASS。
- 远端 unittest：24/24 PASS。
- MCP 针对性测试：2/2 PASS。
- 半决赛 EX-P01 真实 API 查询、`chart.png`、三问 `answer.json`：PASS。
- Web import、systemd restart、health、Final tasks、Final topology：PASS。

## 11. 修改文件

- `main.py`
- `submit.py`
- `final_answer.py`（新）
- `client/public_api.py`
- `agent/core.py`
- `agent/llm_core.py`
- `agent/mcp_client.py`
- `mcp_servers/trace_diagnostics.py`
- `schemas/final-answer-v1.schema.json`（新）
- `tests/test_final_mode.py`（新）
- `webapp/backend.py`
- `webapp/static/app.js`
- `webapp/static/styles.css`

部署前备份：

`/opt/aiops-competition/organizer-reference/final-stage1-backup-20261019T0340Z/`

## 12. 尚需完成的正式验收

1. 由 51 管理员创建新的隔离测试身份/重置合法预览限额，重测 FT-01～03 三题分数。
2. 根据 PK-R03 的事件级反馈，改进通用指标选择、实体归并、taxonomy 对齐和时间窗估计；不可按 Case 硬编码。
3. 在 51 正式 Final/PK Round 控制平面中创建隔离轮次，验收进入、锁定、评委分、胜者确认和下一轮。
4. 准备两台独立选手 VM 和两个合法队伍身份，完成真实双队并发投屏。
5. 在正式投屏机上补录 1920×1080 决赛页面截图。
6. 由组委会人工审核决赛 GT 和打分口径；本轮不自动批准。

