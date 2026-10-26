# 半决赛 AM 队伍归属记录

状态：`AM_TEAM01_10_ASSIGNED / SEMIFINAL_NOT_OPEN`。2026-10-20，51 已将 TEAM-01～TEAM-10（192.168.51.52～61）设置为 AM。TEAM-11～TEAM-17（192.168.51.62～68）仍为 `UNASSIGNED`，并未默认分入 PM。变更前 Registry 备份位于 51 `/etc/aiops-contest/team_registry.before-am-team01-10-20261020.json`。

Registry 变更前 SHA-256：`699067188a30eaf8c09ed92a677565872d9b3415794b21f53d8c134292bafa62`；变更后 SHA-256：`fce19e97db840f54d302a5e53d6439201b414aeec3270b449f5eaaf7d73f1ccd`。17/17 队伍 ID、IP 与 Token ACTIVE 状态核对通过；只修改前十队的 `session` 字段。

从 17 台绑定 IP 实测：前十队的 `/v1/semifinal/cases` 返回 403 `SESSION_NOT_OPEN`；其余七队返回 403 `SESSION_UNASSIGNED`。17/17 的练习 Case 与 Question 接口仍返回 200，仅能看到 SF-001、SF-002 和已发布练习题。51 Practice 保持 `OPEN`，半决赛 AM/PM 保持 `NOT_OPEN`，正式 `semifinal.published_cases=[]`。

正式半决赛题仍需发布前提：51 的 Q01～Q10 题面当前均为 `SEMIFINAL_PREVIEW`，私有评分器的正式 `/etc/aiops-private-grader/qa-gt/` 中尚无这些题的已批准 GT。仅把 AM 状态切为 `OPEN` 也不会显示正式题；把预览题直接改为正式题则会造成提交无法得到已批准 GT 的正式评分。需要先明确 AM 正式题号/Case 清单，完成题面和 GT 审核、发布及版本校验，然后再按计划开场。当前未开放任何半决赛正式题。
