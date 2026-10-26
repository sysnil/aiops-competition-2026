
# 故障脚本与采集代码索引

原目录结构在`fault-injection/ops-competition-2026/`保留，避免把不同冻结版本的adapter拼装为一个版本。

- `scripts/competition_adapter.py`：有界Service/Pod机制、目标PID/UID/cgroup绑定与cleanup。
- `scripts/run_competition_case.py`及同目录preflight、observation、archive模块：早期通用生产流程。
- `sf*-real-adapter-*/scripts/`：各题专属的注册、握手、worker故障执行、观测、导出和归档版本。
- `final-case-*-v*/scripts/`：后期统一入口、授权、租约、时钟、恢复和fail-closed安全门。
- `plans/`：场景、准入、数据导出和生产总账定义；不得因模型得分修改选择规则。
- `reports/`：历史产出与失败记录，不等于当前可重新执行的授权。
- `support/`：原执行链引用的负载、采集、路径验证及安全检查项目源码，只归档代码/合同/测试，不归档其DEV/TEST数据。

**不能按目录名里的版本号大小自动认定唯一生产入口。** 每题实际选中Attempt、Public Manifest和生产报告共同确定其使用版本；后续新环境必须重新预检和授权。

Service进程停顿、单Pod内存压力、HTTP入口延迟及两阶段复合注入等能力已在赛事资料中记录。Node/NetworkPath支持代码含更早的跨层研究/Rehearsal依赖；它们的存在不代表每道决赛都包含这些故障，也不代表允许在比赛服务器上执行。

归档保留原始绝对路径和源码锁。凭据脱敏后重新执行旧hash锁定流程可能拒绝运行，这是预期的fail-closed行为，应重新登记，而不是移除安全校验。
