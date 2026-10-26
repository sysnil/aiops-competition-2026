# 半决赛最高分与速度排名

已部署到51。规则版本：`SEMIFINAL-BEST-SCORE-SPEED-2.0`。
这是2026年10月22日用户批准的排名规则修订，替代此前“最后一次完成评分、同分并列”。

## 计分和速度的精确定义

1. 只纳入TEAM-01–17、SEMIFINAL QA、OFFICIAL、正确题号和场次的有效提交。
2. AM为北京时间10月22日13:30–15:30，TEAM-01–09；PM为16:00–18:00，TEAM-10–17。
   时间以51保存的`received_at`为准，采用`start <= received_at < end`。
   计分截止后才完成评分的按时提交仍有效；历史、PREVIEW和超时提交不进入排名。
3. 每题取窗口内最高分。同一最高分重复出现，取最早收到的提交。
   后续低分或相同高分不会降低成绩或延后取得时间。
4. 十题分数相加，满分100分。总分高者优先。
5. 总分相同，比较从本队场次开场到首次凑齐该总分的用时；用时短者靠前。
   计算为各题正分最高分最早取得时间的最大值，减去该队场次开始时间。
   AM/PM用各自场次相对耗时，不用绝对UTC时钟；不使用评分完成时间。
6. 完全相同的总分与微秒级用时才并列。队伍ID仅稳定显示顺序，不决定胜负。
   全0分队伍没有取得正分时间，彼此并列；较晚提交0分也不延迟已有正分总分。
7. 按时提交仍待评分或分数/状态异常时，结果标PROVISIONAL；不把未出分当成已评分0分。
   缺答计0，同时保留缺答标记和已评分题数。

示例：A队Q01在开场10分钟取得5分、Q02在30分钟取得5分，总分10分，取得用时30分钟。
B队Q01在25分钟取得10分，总分同为10分，用时25分钟，B排在A前。
若B在50分钟再次提交10分，仍按最早的25分钟计算。

## 赛后最快操作

在51以root执行：

```bash
/opt/aiops-contest-server/semifinal-operations/export_semifinal_results.sh
```

默认要求两场结束且适用提交处理完成，否则提示`RANKING_NOT_FINAL`，不会导出冒充最终结果。
赛中查看或排查待评分可执行：

```bash
/opt/aiops-contest-server/semifinal-operations/export_semifinal_results.sh --provisional
```

每次创建独立私有目录，终端返回实际位置：
`/var/lib/aiops-contest/semifinal-results/20261022/results-<UTC时间>-<进程号>/`。

| 文件 | 用途 |
| --- | --- |
| leaderboard.csv | 全17队总排名、场内排名、总分、取得总分用时、逐题分数与提交ID |
| leaderboard-AM.csv / leaderboard-PM.csv | 分场队伍明细，包含全场rank和本场session_rank |
| best-submissions.csv | 17队×10题的选中提交、最高分、UTC取得时间和相对微秒用时 |
| results.json | 状态、逐条排除原因、每场完成状态及输入/规则SHA-256 |
| ranking-plan.json / input-rows.json | 本次只读汇总使用的规则和记录快照，便于独立复算 |

字段`total_score_elapsed_display`为`时:分:秒.微秒`；`total_score_achieved_at_utc`供回查。
不要把整份私有归档直接发给选手；它保留内部提交与评分审计字段。

只读复算（同版本代码）：

```bash
PYTHONPATH=/opt/aiops-competition-submissions python3 \
  /opt/aiops-contest-server/semifinal-operations/verify_semifinal_export.py \
  /var/lib/aiops-contest/semifinal-results/20261022/results-具体目录
```

## 后台与选手接口

组委会后台沿用本机SSH隧道18505 → 51的127.0.0.1:8504。
展开“半决赛正式计分”，比赛开始后可看到总排名、场内排名和取得总分用时，并导出CSV/审计JSON。
快照每5秒更新；点“刷新记录”清缓存。快照超过30秒未更新时停止汇总，避免旧数据被当作最终榜。

`/v1/semifinal/scores/semifinal`也使用相同核心规则，仅返回该队自己的最高分汇总，
不泄露其他队伍成绩。Practice和组委会非正式预览沿用原有独立汇总，均不进入正式榜。
单次提交评分与历史记录未重评、未覆盖，提交次数仍不限。

## 部署和验证

- 独立服务器代码副本回归129 passed、3 skipped；含12组排名场景及3组Store/API集成测试。
  覆盖重复低分、重复高分、输入乱序、同分时效、跨场公平、微秒并列、0分、截止边界、
  评分完成时间隔离、待评分与历史/预览排除。
- 命令行临时导出成功；赛前默认最终导出被拒绝；私有归档可按输入SHA复算。
- 页面实际systemd隔离环境下验证新版规则与“比赛尚未开始”，未登录不显示记录；
  独立合成窗口验证了“取得总分用时”列及下载入口。
- 真实API healthz为200，后台health为ok，API进程隔离环境可读取新版计划和核心模块。

新版开场清单SHA-256：
`0d7cb4147012ad94658b83dc3af5951d554bd59f71f2d8228e2c67a52946436a`。
旧清单SHA：`932c028c2d56755a1066556429b46546911eb2bf9ac227f5807dbc8b17b7364d`。
新清单保留GT、原始Manifest和开场代码哈希，仅更新计分计划、API汇总与排名核心版本；
开场service的清单绑定同步更新。13:30/16:00定时器均active，场次状态和队伍Registry哈希未改变。

51变更备份及部署记录：`/root/semifinal-ranking-best-score-20261022/`。
规则/核心在CLI、后台及选手正式总分接口之间一致。此目录不包含实际最终比赛排名。
