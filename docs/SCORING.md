
# 当前归档的评测逻辑

## 半决赛

正式查询绘图题为Q01–Q10。题面及三问定义见`questions/semifinal/catalog-deployed.json`，正式答案见`private/ground-truth/semifinal/`；原始生成、参考图和独立求解器在`questions/semifinal/generation/`。

每题10分，图表4分、三问合计6分。具体数值容差、时间桶、Counter重置、p95和缺失值规则由题目及当前QA评分器决定。现行代码含赛中修复，不应使用旧报告中的旧反馈替代复算。

半决赛排名按各队比赛时间内每题最高分汇总，同分按首次取得相应分数的时间规则排序。时间计划保存在`organizer/scoring-plans/semifinal.json`，统计程序在`organizer/submissions/semifinal_results.py`及server相关模块。

## 决赛异常检测

现行规则：类型40、实体20、时间20、证据20；对匹配事件得分按GT事件数归一，误报和重复事件分别扣分，最终裁剪为0–100展示分。具体边界、兼容类型和证据规则以`server/grading/private_grader/final_scoring.py`及`private/scoring/formal-final-rubric.json`为准。

综合分不等于Exact准确率。当前宽松口径可能在类型/实体不正确时仍给予时间或证据部分分，`matched_events`不能直接解释为完全正确事件数。早期50独立参考评分报告曾漏计证据分，最终以51当前统一评测为准。

决赛上半场三题各按100分展示，总分为三题均值。计分窗口及9队名单由`organizer/scoring-plans/final-qualifier.json`保留，不由网页当前任务是否开放推断。

## PK

得分优先，同分按51收到有效提交的精确时间比较；不使用评委分。主实现为`server/app/accuracy_speed.py`和`server/scripts/final_event_control.py`。

归档时已支持同题在终态后重新open：新开题时间窗隔离新旧提交额度、幂等键和胜负记录，旧轮次保留审计。close会锁定收件并尝试自动结算；未提交为0，双方无有效提交时0:0、无胜者结束；待评分不是0分。需要处理的真实并列或配置不一致仍保留明确状态。

内部原始分与网页展示分存在10倍换算，不能把数据库0–10原始值误当成0–100展示值。赛程、原始提交和已确认结果未作为数据库整体复制到Git。
