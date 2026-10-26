# 半决赛 AM 内测开放记录

状态：`AM_PREVIEW_10_TEAMS_VERIFIED`。2026-10-20 在 51 开放限时半决赛内测，范围为 TEAM-01～TEAM-10（52～61）及现有 Q01～Q10 预览题。关联 Public Case：SF-003、SF-005、SF-010。授权到期：**2026-10-22 08:33:28 UTC（北京时间 16:33:28）**。到期后预览 Case 授权自动失效；提前停止可禁用 `semifinal.am_preview_test.enabled` 或将 AM 状态设为 `CLOSED`。

本次使用独立的 `am_preview_test` 授权，要求队伍 Token、来源 IP、AM 归属、AM `OPEN` 状态、明确的 10 队名单、3 个 Case 清单及有效期全部通过。正式 `semifinal.published_cases` 仍为空，Q01～Q10 仍标记 `SEMIFINAL_PREVIEW`；其 GT 均为 `DRAFT_PREVIEW_ONLY`、`DRAFT_PENDING_HUMAN_REVIEW`。QA 提交沿用现有预览评分路径，结果标记 `PREVIEW`，不占 `OFFICIAL` 提交额度，不能当正式成绩。AM 内测拒绝非 QA 的异常题提交。

变更前状态文件备份：51 `/etc/aiops-contest/competition_state.before-am-preview-20261020.json`，SHA-256 `ff630df33a25c1fa85a99b3bbafcffead61e3a2f903556df08e2194adba8b84f`。新状态 SHA-256 `6e84aa5a5be6580f5154970e8bea2f7c1f736de1e360cb9`。API 代码备份位于 51 `/opt/aiops-contest-server/backups/am-preview-20261020/`。变更后的 `access_control.py` SHA-256 `04f20b10a5baec298072ecaba7b982936fb95b942427a355774207cbdaf99bc4`；提交路由 SHA-256 `dcc99554440f6a8fcb2d12deee2e18b1361a4417875f45119926b022d5ba2eb3`。

验证：隔离代码副本全量测试 37 passed、3 skipped；51 API 和私有评分器均为 `active`。从 17 台选手机器读取：前 10 队均获得 3 个内测 Case、Q01～Q10、Q01 详情及 SF-003 Metadata（HTTP 200）；后 7 队的半决赛列表/详情/Metadata 均为 403 `SESSION_UNASSIGNED`；17 队练习 SF-001/002 仍可用。现存上述 3 个 Case 的提交记录仅有历史 `PREVIEW`，没有 `OFFICIAL`；本次验证只读，没有创建新提交。PM 保持 `NOT_OPEN`。

队伍可在各自机器使用本队 Token 请求 `GET /v1/semifinal/questions` 和 `GET /v1/semifinal/cases`。正式半决赛仍需单独审核并发布题面与 GT，不能沿用本次预览分数作为正式成绩。
