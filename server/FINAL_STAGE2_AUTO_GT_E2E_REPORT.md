# 决赛 Stage 2 自动标注 GT 全流程内部验收报告

> 批次：`FINAL-STAGE2-AUTO-GT-20261019`  
> 验收日期：2026-10-19（UTC/Asia-Shanghai 记录均保留时区）  
> 最终状态：`FINAL_STAGE2_INTERNAL_PREVIEW_E2E_COMPLETE`  
> 分类：`INTERNAL_PREVIEW`，不是正式成绩，不表示 GT 已人工批准。

## 1. 执行摘要

Stage 2 已在 51 的 Final Stage 1 实现上增量完成，未重构现有模块。本次实际完成：

- 冻结 7 份自动标注 GT 的 SHA-256、来源 Manifest、候选数、归并数和评分规则版本；原 GT 未修改、未删除、未批准。
- 在 `192.168.51.51:18080` 部署独立的 Stage 2 内部预览 API，使用独立数据库、凭据、提交额度和审计记录。
- 使用 50 的真实 Final Baseline Agent，以两个独立逻辑队身份完成 FT-01～FT-03 和 PK-R01～R04 的真实查询、绘图、提交和自动评分。
- PK-R05～R08 在 18080 停止期间只执行状态机模拟，未向测试身份发布重复 Case。
- 验证了上半场成绩持久化、PK 综合计分、胜者确认、擂主切换、八轮冠军记录和完整审计链。
- 在 50 新增独立的 Stage 2 大屏 `http://192.168.51.50:8089/`；原 8088 服务保持正常。

## 2. 独立环境与隔离

| 项目 | Stage 2 内部预览 | 正式/现有环境 |
|---|---|---|
| API | `192.168.51.51:18080` | `192.168.51.51:8080` |
| 数据库 | `/var/lib/aiops-final-stage2/stage2.sqlite3` | `/var/lib/aiops-grading/submissions.sqlite3` |
| 配置 | `/etc/aiops-final-stage2/` | `/etc/aiops-contest/` |
| 身份 | `STAGE2-TEAM-A/B` 及只读联调身份 | 现有队伍身份 |
| 评分标记 | `INTERNAL_PREVIEW` | 原有 OFFICIAL/PREVIEW |
| Web | `192.168.51.50:8089` | `192.168.51.50:8088` |

隔离验证结果：

- Stage 2 的 14 次真实队伍提交全部只位于独立数据库，且全部标记 `INTERNAL_PREVIEW` / `AUTO_GT_INTERNAL_PREVIEW_ONLY`。
- 生产数据库验收前后保持：`submissions=217`、`final_submissions=6`、`final_audit=13`。
- 正式比赛状态保持：Practice=`CLOSED`，Semifinal AM/PM=`NOT_OPEN`，正式 Final 未开放。
- `/etc/aiops-contest/competition_state.json`、`team_registry.json`、Stage 1 Catalog 的 SHA-256 与执行前一致。
- 51 上使用 50 Token 访问 18080 被来源 IP 绑定以 404 拒绝；仅 50 可使用该身份。
- 50 不存在 GT 路径、GT 数量或评分器私有字段泄漏；浏览器不接收 Token。

## 3. 临时 GT 冻结清单

冻结文件：`/var/lib/aiops-final-stage2/FINAL-STAGE2-AUTO-GT-20261019/gt-freeze-manifest.json`

| Case | GT SHA-256 | 原候选 | 归并事件 | 评分器实读 |
|---|---|---:|---:|---:|
| SF-004 | `7dfb57f97642cb9cbfd1decddb1f641b4224d7bacd34047d29b2fd83bf2f79c2` | 3 | 3 | 3 |
| SF-006 | `8f9476e7583284520a03c89489750caca905bca3d969d419291cd8747d5f4fc6` | 4 | 4 | 4 |
| SF-007 | `e9b52bcd4b594b5126b5a55e59d911487b0081c49babc26ed004829775a63fa7` | 23 | 4 | 4 |
| SF-008 | `4febc99c5a9e4efafb775a33cc837f2a6e64c3d05cbf5e2f320a5b82d7acedee` | 4 | 4 | 4 |
| SF-009 | `094b5aee5464259c98b70cc1d803639c5b49c6f853393602ca68311e17bb97f9` | 16 | 6 | 6 |
| SF-011 | `a41147d6e5acdd112c78ab8ecff88ca6e90de8b69204f604bf78392c28dadde0` | 2 | 2 | 2 |
| SF-012 | `0594a5a31fe56b9744d1c074d8687d6cc98556cc3cc907e68e64e0bcec78fad2` | 1 | 1 | 1 |

冻结规则：

- 评分规则版本：`final-stage2-auto-gt-1.0`。
- GT 归一化版本：`final-stage2-auto-gt-frozen-v1`。
- Stage 2 仅在 `INTERNAL_PREVIEW` 模式读取 `merged_events`；原 Stage 1 `PREVIEW` 行为保持不变。
- 归并规则由 GT 内既有声明冻结：同一 Public 实体，证据时间重叠或间隔不超过 30 秒；保留原候选，不做因果/根因推断。
- SF-007 明确按 23 个候选归并为 4 个评分事件；SF-009 按 16 个候选归并为 6 个评分事件，避免同一异常的多模态表现被重复计分。

## 4. 上半场 FT-01～FT-03

| 任务 | Case | A 异常/图表 | B 异常/图表 | A 分数 | B 分数 |
|---|---|---:|---:|---:|---:|
| FT-01 | SF-006 | 1 / 1 | 1 / 1 | 0.50 | 0.50 |
| FT-02 | SF-011 | 5 / 3 | 5 / 3 | 0.00 | 0.00 |
| FT-03 | SF-007 | 7 / 4 | 7 / 4 | 0.75 | 0.75 |
| **合计** | - | - | - | **1.25 / 30** | **1.25 / 30** |

每题都完成 `OPEN → SUBMISSION_LOCKED → SCORED → CLOSED`，且分数在后续 PK 综合计分中被正确引用。低分表明当前 Baseline 与自动 GT 的类型/实体/时间/证据匹配度较低，不是提交、持久化或评分流程失败。

## 5. 下半场 PK

### 5.1 四个真实 Case

| 轮次 | Case | A 自动分 | B 自动分 | 模拟评委 A/B | 综合分 A/B | 胜者 |
|---|---|---:|---:|---:|---:|---|
| PK-R01 | SF-004 | 2.00 | 2.00 | 70 / 80 | 30.25 / 33.25 | B |
| PK-R02 | SF-008 | 0.00 | 0.00 | 82 / 74 | 25.85 / 23.45 | A |
| PK-R03 | SF-009 | 2.00 | 2.00 | 78 / 73 | 32.65 / 31.15 | A |
| PK-R04 | SF-012 | 1.50 | 1.50 | 72 / 85 | 28.85 / 32.75 | B |

PK-R01～R04 两队均真实执行 Agent，使用 51 Public API 查询 Metrics/Logs/Traces/Topology，生成 `answer.json` 和 PNG，通过证据验证后进入内部评分。

### 5.2 PK-R05～R08 状态模拟

| 轮次 | simulation_only | 综合分 A/B | 胜者 |
|---|---|---:|---|
| PK-R05 | true | 25.25 / 23.75 | A |
| PK-R06 | true | 24.05 / 25.55 | B |
| PK-R07 | true | 24.65 / 24.95 | B |
| PK-R08 | true | 26.45 / 24.35 | A |

R05～R08 未执行 Agent 提交，只验证 `READY → RUNNING → SUBMISSION_LOCKED → JUDGING → RESULT_CONFIRMED → CLOSED` 状态链。模拟时 18080 已停止，重复 Case 没有对测试身份发布。八轮最终冠军记录为 `STAGE2-TEAM-A`。

## 6. 50 Agent 实际运行

真实运行根目录：`/opt/aiops-competition/participant-starter/runs/final-stage2/`

- 共 14 次真实 Baseline Agent 运行：3 道 FT × 2 队 + 4 道 PK × 2 队。
- 每次运行 29～30 条 API 请求，耗时 7.1～11.9 秒，提交状态均为 `PREVIEW_COMPLETED`。
- 每次均生成可校验 `answer.json`、`api_requests.jsonl`、`evidence_validation.json`、`summary.json`和提交记录。
- PK-R02 的 Baseline 产出 0 个异常和 0 张图，评分为 0；这是 Agent 实际结果，未为演示伪造输出。
- Token 使用：本批次使用 Baseline，未调用 LLM，Web 按要求显示“未提供”，未伪造 0 Token。

联调中发现并修复两个实际问题：

1. 50 `main.py` 原先把 PK 的合法 `RUNNING` 状态误当成未开放；已分离 FT=`OPEN` 与 PK=`RUNNING`，并新增回归测试。
2. 51 的 PK 权限检查使用轮次表，但公开任务响应曾返回任务表的 `NOT_OPEN`；已统一为 PK 从轮次表返回状态和时间窗。

## 7. 50 Web 联调

Stage 2 内部大屏：`http://192.168.51.50:8089/`

已验证：

- 上半场展示 FT-01～FT-03 状态、两队分项和总分。
- PK 展示 R01～R08 状态、每轮胜者、下一轮以及最终擂主。
- 回放本批次 PK-R03 真实 Agent 时，页面显示 29 个结构化事件、27 个主要 Public API 请求、真实耗时 7.5s、6 个异常、证据验证 PASS、服务拓扑和 3 张异常图。
- 明确区分“真实执行”与“延迟回放”，不展示模型隐藏推理或密钥。
- 服务依赖拓扑实际读取 26 个服务、25 条依赖边。

截图：

- `/opt/aiops-contest-server/reports/final-stage2-assets/FINAL_STAGE2_WEB_UPPER.png`
- `/opt/aiops-contest-server/reports/final-stage2-assets/FINAL_STAGE2_WEB_PK.png`

实际 Agent 图表：

- `/opt/aiops-competition/participant-starter/runs/final-stage2/team-a/PK-R03/final-chart-01.png`
- `/opt/aiops-competition/participant-starter/runs/final-stage2/team-a/PK-R03/final-chart-02.png`
- `/opt/aiops-competition/participant-starter/runs/final-stage2/team-a/PK-R03/final-chart-03.png`

## 8. 提交、持久化与恢复

- Stage 2 主数据库最终记录：11 个任务、8 个 PK 轮次、14 个真实队伍提交、16 个评委分数、118 条审计事件。
- 所有 14 个真实提交均完成自动评分并持久化，无 `PENDING` 遗留。
- 独立恢复数据库验证：中断前 `GRADING`，重启恢复为 `GRADE_PENDING`，`recovered_count=1`。
- 同队同任务第二次提交被拒绝为 `SUBMISSION_LIMIT_REACHED`。
- 评分持久化是幂等的：已存在 `grade_json` 的提交不会被重复覆盖。

## 9. 代码与配置改动

51：

- `app/final_api.py`
- `app/final_store.py`
- `grading/private_grader/service.py`
- `grading/private_grader/final_scoring.py`
- `scripts/setup_final_stage2.py`
- `scripts/freeze_final_stage2_gt.py`
- `scripts/manage_final_stage2.py`
- `tests/test_final_stage2.py`
- `config/aiops-final-stage2.service`
- `/etc/systemd/system/aiops-final-stage2.service`

50：

- `main.py`
- `tests/test_final_mode.py`
- `webapp/backend.py`
- `webapp/static/app.js`
- `/etc/systemd/system/aiops-agent-web-stage2.service`

回归结果：51 完整测试 `18 passed, 3 skipped`；Stage 2 + 既有 Final 回归 `7 passed`；50 Final 模式 unittest `3 passed`。

## 10. 尚待完成

1. 七份自动 GT 仍为 `DRAFT_PENDING_HUMAN_REVIEW`，必须由组委会完成人工审核后才能进入正式 GT 目录。
2. 本轮两队是同一台 50 上的两个独立身份，已验证身份/额度/成绩隔离，但不等于两台合规选手 VM 的真实并发验收。
3. PK-R05～R08 本轮只验证状态机，没有发布重复 Case，也没有 Agent 提交。
4. Baseline 对自动 GT 得分偏低；需在 GT 人工审核后再判断是 GT 口径、Agent 异常分类、实体选择还是时间窗存在偏差。
5. 正式决赛仍保持关闭；本报告不批准 GT、不发布任务、不生成正式成绩。

## 11. 结论

自动标注 GT 的 Stage 2 内部预览全流程已验收通过：任务开放、Agent 查询、异常图表、提交、自动评分、上半场汇总、PK 评委分、胜者确认、擂主切换、八轮冠军、Web 展示、审计和失败恢复均已实际验证。

正式状态仍保持关闭，未批准 GT，未写入正式排行榜。
