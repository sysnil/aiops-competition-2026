# 51 半决赛 AM/PM 场次隔离方案

状态：`DRAFT_NOT_OPEN`。按用户确认的赛制，同一场次内各队可见相同题目，只需将队伍分配到 AM 或 PM。17 队的规划表为 `semifinal-session-plan-draft.csv`，目前仍未分场。

## 现有能力

51 的 `team_registry.json` 为每队保存唯一 Token、绑定 IP、`allowed_modes` 和 `session`。`AccessController` 先校验 Token/IP，再检查 `SEMIFINAL` 模式与队伍所属场次；只有该场次状态为 `OPEN`，队伍才能读全局 `semifinal.published_cases`。题面还需在 Question Catalog 中标为正式发布。Case 数据查询与 QA 提交使用同一授权流程。

目前 TEAM-01–TEAM-17 的 `session` 全为 `UNASSIGNED`，AM/PM 均 `NOT_OPEN`，正式 `published_cases=[]`。Q01–Q10 仍为 `SEMIFINAL_PREVIEW`，关联 SF-003、SF-005、SF-010；这些题面不会向普通参赛队显示。Practice 已开放 SF-001/002，与半决赛授权独立。

## 场次执行顺序

1. 组委会确定 AM/PM 队伍名单及各场题目，填写规划表并复核 17 队不重不漏、IP 与 Registry 一致。检查题目 Public Manifest、正式题面发布状态和评分 GT 审核状态。不得仅因 Public Case 文件存在就视为题目已发布。
2. 在 51 备份 Registry/状态并记录 SHA-256。可用现有 `manage_competition.py assign-session TEAM-xx AM|PM` 分队；逐条设置期间保持两场 `NOT_OPEN`。全部 17 队设置完成后，从各队 IP 验证未开场次仍被拒绝。
3. 只发布 AM 使用的 Case/正式题面，确认 PM 仍 `NOT_OPEN` 后切 AM 为 `OPEN`。AM 队伍读到同一组题，PM 队伍不得访问。结束时先切 AM 为 `CLOSED`，再处理提交与评分；任何异常可切 `EMERGENCY_LOCKED`。
4. 在 AM 已关闭的条件下，将全局发布 Case 列表切换为 PM 题目，完成 Public/GT/题面核对，然后切 PM 为 `OPEN`。PM 结束后切 `CLOSED`。**AM 和 PM 不得同时 OPEN**，否则现有全局 `published_cases` 无法承载两组不同题目。

当前半决赛 `opens_at`、`closes_at` 只是状态字段，`AccessController` 尚未按 UTC 时间自动拒绝；正式执行必须以服务端 `OPEN`/`CLOSED` 转换为准，并由独立计时提醒或经测试的自动关场任务保障准时关闭。不能以只填写时间字段替代关场操作。

## 上线前验收

- AM 开放时：AM Token 从绑定 IP 可见 AM 发布题，PM Token 与错误 IP 被拒绝；反向场次同理。
- AM 关闭后即使知道题号或 Case ID，AM 队伍不能读 PM 数据、题面或提交；PM 开放前后重复验证。
- 匿名和错误 Token 为 401/403；未发布题的详情、数据与提交不能通过猜测 ID 绕过；练习 SF-001/002 继续正常。
- 正式题必须从 `SEMIFINAL_PREVIEW` 受控转为正式发布，并且正式评分 GT 就绪。记录每次状态转换、题面/Case/GT 版本和提交审计。

现有代码已具备 AM/PM 状态隔离；本轮不设置具体队伍场次、不发布半决赛题、不开放 AM/PM。
