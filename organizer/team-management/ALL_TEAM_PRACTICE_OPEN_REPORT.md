# 全队练习权限开放记录

状态：`ALL_TEAM_PRACTICE_OPEN_VERIFIED`，2026-10-20。

51 的 `/etc/aiops-contest/competition_state.json` 已通过既有 `manage_competition.py set-state practice OPEN` 将 Practice 从 `CLOSED` 切换为 `OPEN`。变更前快照保存在 `/etc/aiops-contest/competition_state.before-all-team-practice-20261020.json`。练习发布列表保持 `SF-001`、`SF-002`；两题真实 Public Metrics/Logs/Traces 文件均已在 51 存在。未修改 GT、评分规则、历史提交或队伍 Token。

变更前状态文件 SHA-256：`bb5590d837a04e9e26a9f76535fc12ae8de2f1ac2806314325be68fd627e73a9`；变更后 SHA-256：`ff630df33a25c1fa85a99b3bbafcffead61e3a2f903556df08e2194adba8b84f`。两份文件均为 `0640 root:aiops-api`。原有 `planned_date=2026-10-21` 是先前计划日期，不参与鉴权；当前 `state=OPEN` 才是实际开放状态。

从 TEAM-01–TEAM-17 各自服务器及绑定 IP 使用本队 Token 实测：`GET /v1/practice/cases` 全部返回 200，且仅列出 `SF-001`、`SF-002`；`GET /v1/practice/questions` 全部返回 200，题号为 `EX-P01`、`EX-P02`、`P-Q01`。17 台对 `GET /v1/semifinal/cases` 均得到 403 `SESSION_UNASSIGNED`。AM/PM 场次仍为 `NOT_OPEN`，半决赛 `published_cases` 仍为空。

匿名练习访问返回 401；从 51 使用 TEAM-01 Token 返回 403 `SOURCE_IP_MISMATCH`。比赛 API 服务保持 `active`。

后续撤销全局练习访问可在 51 使用既有命令 `python3 /opt/aiops-contest-server/scripts/manage_competition.py set-state practice CLOSED`；该操作不删除历史提交。
