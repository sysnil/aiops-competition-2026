> 2026-10-22 排名规则已更新：每题取本场窗口内最高分，同一最高分取最早提交；同总分按本场开场至首次取得该总分的用时排序。当前操作指南为 /opt/aiops-contest-server/SEMIFINAL_BEST_SCORE_RANKING_GUIDE.md。以下旧规则段落保留为历史记录。新版开场清单SHA为 0d7cb4147012ad94658b83dc3af5951d554bd59f71f2d8228e2c67a52946436a，定时任务绑定已同步。

# 10 月 22 日半决赛正式启用报告

状态：FORMAL_GT_APPROVED_AND_OPENING_SCHEDULED；当前两个场次仍 NOT_OPEN。
授权：用户于 2026-10-21 确认 Q01–Q10 已审核，转正式 GT，半决赛 QA 不限提交次数。

## 名单、题目和时间（北京时间）

| 场次 | 队伍 | 服务器 | 开放时间 | 正式计分截止 |
| --- | --- | --- | --- | --- |
| AM（上半场） | TEAM-01–09 | 192.168.51.52–60 | 2026-10-22 13:30 | 15:30 |
| PM（下半场） | TEAM-10–17 | 192.168.51.61–68 | 2026-10-22 16:00 | 18:00 |

两场均为 Q01–Q10，每题满分10分、总分100分。AM/PM沿用系统代号。
只设置开场任务，不设置赛后关闭读取。开场后可持续测试和提交；正式排名只
纳入本队计分窗口 `start <= received_at < end` 内51服务器收到的提交。
允许评分在截止后完成；仍待评分时只显示临时汇总。

## 正式 GT 和安全边界

- `/etc/aiops-private-grader/qa-gt/Q01.json`–`Q10.json` 已设为 APPROVED。
  版本为 `semifinal-qa-formal-20261022-v1/<question_id>`。
- 原 `/etc/aiops-private-grader/qa-gt-preview/` 完整保留。正式转换只改变审批、
  分类和版本元数据；10份GT的答案、图表、容差及其他评分内容与原稿逐字段一致。
- 各GT来源Manifest与51导入Manifest中的 `source_manifest_sha256` 一致。
  不重写不可变Public原始Manifest和采集数据。
- 公开题面改为 SEMIFINAL_PUBLISHED，授权数据为SF-003、SF-005、SF-010。
  17队均有SEMIFINAL模式授权，但访问仍受各自AM/PM状态控制。
- 逐字段核对Registry：Token、IP、Final与Practice权限未改变；历史提交不重评。
  练习和决赛规则未变。正式GT只在Private Grader区域，无新增下载接口。

## 不限次数

`grading/submission_api/router.py` 不再对 SEMIFINAL_QUERY_VISUALIZATION
执行累计次数上限。保留其他任务的既有规则，以及身份认证、来源IP绑定、
场次隔离、结构校验、文件大小和资源安全保护。幂等请求仍返回原提交；每次
独立提交继续完整保存。无需修改52–68的Agent代码。

## 定时开场与预检

51安装并启用：

- `aiops-semifinal-AM-20261022.timer` → `aiops-semifinal-open@AM.service`
- `aiops-semifinal-PM-20261022.timer` → `aiops-semifinal-open@PM.service`

systemd的UTC下次执行时间分别为10月22日05:30和08:00，即上述北京时间。
定时器使用Persistent；只开放对应场次，开场前核验GT、题面、公共Manifest、
计划及代码哈希和17队Token所属名单/IP。提前执行、文件变化、手动关闭或
EMERGENCY_LOCKED均拒绝。开场操作保存状态备份和审计，不自动关闭。

正式启用清单：`/etc/aiops-contest/semifinal-20261022-release.json`。
SHA-256：`932c028c2d56755a1066556429b46546911eb2bf9ac227f5807dbc8b17b7364d`。
计划：`/root/semifinal-20261022-preparation/semifinal-20261022-plan.json`。

## 赛后排名

组委会评分记录页（51的127.0.0.1:8504，工作站隧道18505）已增加：
**半决赛正式计分 · 总分与排名 / CSV 导出**。

比赛开始后可查看临时榜；两场结束且按时提交都处理完后显示完整汇总。
可导出总分排名CSV（含每题被选提交ID）及审计JSON。只读一致性快照，
每5秒刷新；点“刷新记录”清缓存。超过30秒未更新的快照不用于最终汇总。

沿用已存在的QA选择规则：每题取窗口内最后一次完成有效评分的提交，
不是取最高分。历史测试、PREVIEW、错误场次、窗口外提交均不计入；
缺答计0，待评分单列并使榜单保持PROVISIONAL。同分并列；未新增速度决胜规则。

也可在51执行：

```bash
python3 /opt/aiops-competition-submissions/tools/semifinal_results.py \
  --plan /root/semifinal-20261022-preparation/semifinal-20261022-plan.json \
  --output "/root/semifinal-20261022-preparation/results-$(date -u +%Y%m%dT%H%M%SZ)"
```

每次输出新目录，不覆盖历史汇总，不写评分数据库。

## 实际验证

- 隔离服务器代码副本：113 passed、3 skipped，包括真实路由下同Case12次、
  同题8次均202、完整答案归档及幂等复用；无真实比赛提交被伪造。
- 开场状态机合成测试：提前拒绝、独立开场、幂等、不自动关闭、文件哈希变化
  拒绝、紧急锁定不覆盖及错误IP拒绝。正式开场脚本在systemd隔离环境中
  check-only PASS，两场当前均NOT_OPEN。
- 从17台真实队伍绑定IP验证：Practice问题200；Semifinal列表和直接Q01
  访问均403 SESSION_NOT_OPEN。记录在根目录下 `before-open-verification.json`。
- Private Grader真实mount namespace：10份正式GT均可读且评分内容未改变。
- 只读数据测试7项通过；排名基础合成测试此前6项通过。真实页面
  AppTest确认半决赛计划可见且显示“比赛尚未开始”，未登录不暴露表格或统计。
  独立合成数据另验证了赛后排名表格、CSV和审计JSON导出入口，未伪造比赛成绩。
- 比赛API `/healthz` 为200，评分记录页health为ok；定时器active。

## 备份与操作

配置和原GT备份：
`/root/semifinal-formal-release-20261021/approved-release/`。
Router原文件：同根目录 `router.before-unlimited.py`。
页面原文件：同根目录 `viewer-before-semifinal/`。

赛前检查（51）：

```bash
systemctl list-timers --all 'aiops-semifinal-*'
journalctl -u aiops-semifinal-open@AM.service -u aiops-semifinal-open@PM.service
```

若紧急停止尚未发生的自动开场，禁用对应timer，并通过原管理工具设
EMERGENCY_LOCKED；不得手工编辑GT来影响开场。若任何被锁版本需要修改，
先停相应timer，重新审核并更新清单和unit中的SHA，不能绕过哈希预检。

本报告不包含真实比赛排名；比赛尚未开始。
