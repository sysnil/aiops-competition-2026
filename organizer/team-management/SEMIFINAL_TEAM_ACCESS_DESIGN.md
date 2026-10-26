# 51 半决赛按队伍与题号授权设计

状态：`SUPERSEDED_DRAFT`。用户已确认半决赛只需隔离 AM/PM，不需要同场次内逐队题号授权。当前方案见 `SEMIFINAL_AM_PM_ACCESS_DESIGN.md`；本文及旧 `semifinal-access-plan-draft.csv` 仅保留历史，不得用作上线配置。

## 当前实现与缺口

- 51 已有 17 个独立且绑定来源 IP 的队伍 Token，TEAM-01–TEAM-17 对应 `.52`–`.68`。Registry 中 17 队的 `session` 目前均为 `UNASSIGNED`；AM、PM 都是 `NOT_OPEN`；正式半决赛 `published_cases` 为空。
- 现有管理命令支持单队 `assign-session`、场次 `set-state`、全局 Case `set-publication`。现有 `AccessController` 在场次 OPEN 后向该场次的所有队伍返回同一个全局 Case 集合，不能给不同队伍分配不同题号。
- 当前 Q01–Q10 均标为 `SEMIFINAL_PREVIEW`，关联 SF-003、SF-005、SF-010；该状态不会作为正式题面显示。多个题号共用一个 Case，例如 Q01–Q03 均使用 SF-003，因此仅按 Case 授权不足以隔离题号。
- 半决赛配置中的 `opens_at`、`closes_at` 目前没有在 `AccessController` 中检查；仅设置时间字段不能自动限制访问，必须先补充服务端 UTC 时间判定。
- 现有 QA 提交路径先按 Case 鉴权，再用 `QuestionCatalog.get_visible()` 检查题号的公开状态与 Case；尚无队伍级题号授权检查。直接猜测同 Case 下其他题号也必须被拒绝。

## 拟实施的唯一授权模型

将组织方审核的计划保存为 51 上 root 可写、`aiops-api` 可读的版本化文件，不放入 Public 数据目录。每队恰有一条记录：`team_id`、已登记 `assigned_ip`、`session`（AM/PM）、明确的 `question_ids`、计划版本及生效窗口。服务器从 Question Catalog 将授权题号推导为 Case ID，不能由浏览器提交 Case 清单。正式题需完成题面发布、正式 GT 审核及 Public Manifest 核验；`SEMIFINAL_PREVIEW` 永不进入正式队伍的题号集合。

对队伍 T 和请求题号 Q，服务端授权取以下条件的交集：有效 Token 与登记 IP 一致；T 允许 `SEMIFINAL`；T 的 AM/PM 场次已登记且状态为 OPEN；服务器 UTC 时间满足 `opens_at <= now < closes_at`；Q 在 T 的明确题号清单且已正式发布。可查询的 Case 只由这些题号反推。Case 列表、Metrics/Logs/Traces/Topology、题目列表/详情及提交接口必须使用同一次请求中的授权快照。未获题号的详情和提交统一返回 404；未开场次返回 403；不得在错误反馈中泄漏题目内容或 GT。

计划示例（示意字段，不代表实际分配）：

```json
{
  "version": "semifinal-team-access-v1",
  "status": "DRAFT_NOT_ENFORCED",
  "teams": [
    {"team_id": "TEAM-01", "assigned_ip": "192.168.51.52", "session": "UNASSIGNED", "question_ids": []}
  ]
}
```

若组委会决定所有同场次队伍做同一组题，可让它们具有相同 `question_ids`；仍保留逐队记录以便审计。若 AM/PM 题目不同，两个场次分别配置并验证，不能仅改全局 `published_cases`。同一 Case 下某队只获一个题号时，它可以读取该 Case 的 Public 数据，但不能看到或提交该 Case 下未授权的其他题号。

## 上线流程与测试

1. 组委会填写 17 行规划表并确认 AM/PM、题号、UTC 开闭时间和正式 GT 状态。校验 17 队不重不漏、IP 与 Registry 一致、题号存在且正式发布、各场次题量符合赛制；拒绝空授权及意外跨场次重复。
2. 实现并测试版本化计划的原子安装和回滚；不能依赖逐条 `assign-session` 的中途状态作为正式配置。记录计划、Registry、题面 Catalog、Public Manifest、GT 版本的哈希及操作审计。
3. 在 `AccessController` 增加服务端时间与队伍题号过滤；在 Question 列表/详情和 QA 提交入口检查同一题号清单。不要通过改 Token 或把题目复制到各队机器来做授权。
4. 使用隔离测试配置验证：正确 IP/Token 仅见本队题号；同 Case 其他题号详情与提交为 404；错误 IP、错误 Token、未开场次、时间窗外均拒绝；AM/PM 互不串题；已开练习仍只见 SF-001/002；正式 GT 不进入 Public 响应。
5. 开赛前先冻结并复核计划与代码版本，再按场次切换 OPEN；窗口结束自动阻断并由管理命令置 CLOSED。可用 `EMERGENCY_LOCKED` 紧急停场，保留所有提交和审计，不重写历史分数。

在上述代码与验证完成前，51 现有权限系统只能按 AM/PM 控制统一的正式 Case 列表，**不能安全地按队伍分配不同半决赛题号**。因此本设计文件及规划表均不应被当作已生效授权。
