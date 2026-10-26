# SF-PILOT-01 首道半决赛题目试生产报告

**SF_PILOT_01_BLOCKED。仅执行了一次试生产；未形成完整 60 分钟数据包，不具备导入条件。**

## 实际执行与中止原因

计划窗口（Master UTC）：2026-10-14T03:33:21.800133+00:00 至 2026-10-14T04:33:21.800133+00:00。Master 观测进程在 2026-10-14T03:50:26.457211+00:00 停止，实际经过 1024.657078 秒，约 17 分钟；不能称为完整一小时。

| 项目 | 实际结果 |
|---|---|
| Service 计划开始 | 2026-10-14T03:45:21.800133+00:00 |
| Service 实际开始（Worker 原始记录） | 2026-10-14T03:45:24.365802+00:00 |
| 启动偏差 | +2.565669 秒 |
| 预登记启动容差 | 2 秒 |
| Service 实际暂停持续时间 | 300.000142 秒 |
| Service 撤销 | COMPETITION_CLEANUP_ACTIONS_VERIFIED，2026-10-14T03:50:24.369457+00:00 |
| Pod 32 MiB 故障 | 未执行；中止后未继续下一个故障 |
| 第二次正式尝试 | 未执行 |

触发条件是本轮执行器预登记的启动时序约束。**2 秒这个数值由本轮执行器在试生产前登记，并非用户原文指定的数字。** 实际偏差超过该约束，因此控制器标记 INVALID 并停止观测。没有在看到结果后将容差改为 3 秒，也没有补跑或重用同一目录覆盖失败记录。

准确规则位置：`/root/ops-competition-2026/plans/SF-PILOT-01.json` 的 `fault_onset_tolerance_seconds`；`/root/ops-competition-2026/production-20261014/scripts/pilot_controller.py:46` 的启动偏差检查。计划 SHA-256：`f6d719359f094d910ef963d386e823315d1920a6a5873e935c221a8c544e15e0`。登记的源文件复核未变：`True`。

## 调度缺口与时钟证据

计划由 Master 生成，但 `pilot_controller.py:49` 使用 Mac 的 `time.time()` 等待计划时刻，再于第 88 行通过 SSH 启动 Worker 的 systemd 服务。最初的五主机 NTP 检查覆盖 Master、三个 Worker 和 VM1，**没有覆盖实际承担定时派发的 Mac**。

事后 3 次往返测量：Master 相对 Mac 中点估算平均快 2.440 秒，Worker3 平均快 2.404 秒，单次半往返不确定度约 0.03–0.06 秒。这与约 2.57 秒的启动偏差一致，支持控制端时钟偏差是主要调度问题；该测量发生在事后，不能当作注入时刻的精确时钟校正。

Worker journal 的 systemd 启动记录为 03:45:24.258424 UTC，适配器 actual_start 为 03:45:24.365802 UTC。原始日志均保存。Mac terminal 的时间戳也保持原样；本文窗口和停采时间采用 Master 记录，故障实际时间采用 Worker 记录，没有倒改数据。

当前控制器在读取注入终态结果时才检查 actual_start，因此本次完成了 300 秒暂停后才识别时序不合规。这是执行器需要修正的另一处检查时机问题。没有将这段成功撤销的注入当作合格的一小时题目。

## 故障期间采集与恢复

Collector 仍为已验证配置，配置 SHA-256：`128ed286e8a959aacddc2ee6c00e2d6e8b0a3eb18ea43e5de730a20e3793af2d`；未重新调参。运行中记录 69 个安全/Collector 采样点，gRPC/HTTP refused 从 {'grpc': 0.0, 'http': 0.0} 到 {'grpc': 0.0, 'http': 0.0}，没有增量；working set 观测峰值 314.02 MiB，低于 380 MiB 停止线。

Service 的两个原进程均被恢复，Pod UID、container ID、PID/start ticks 复核保持一致。独立恢复 timer 已取消/不存在，未发现暂停进程或残留故障。中止后的 4 次真实推荐 API 请求均返回 200，记录为单独恢复诊断，**不替代正式采集，也不用于填满 60 分钟**。

最终五主机安全复查：`COMPETITION_ENVIRONMENT_PASS_WATCHDOG_PENDING`，实际环境 blockers：`[]`。Observer 的 failed systemd 状态和退出记录保留作为失败证据，不代表仍有注入进程运行。

## 数据质量与缺项

只完成前两个 5 分钟数据分段及其不可变补齐审计。包含 Service 故障的 10–20 分钟分段因整体中止而未完成，后续分段也未执行。不能把前缀审计通过外推成故障期完整性通过。

| 已成熟前缀数据 | 数量 |
|---|---:|
| 原始 Metric 查询样本（含边界点） | 12,810 |
| Logs | 98,125 |
| 最终已审计 Spans | 144,438 |
| 已成熟分段 | 2 |

每分钟前缀计数、请求记录、Collector 自遥测和匹配详情见 `production-failure-summary.json`。没有完整 60 分钟的 Metric/Log/Trace 数量、业务服务/调用边覆盖或完整性结论，这些全窗口项目为 N/A，不填为零。

中止时的逐请求 live 日志保留已完成请求；可能仍在进行的请求未能完整收尾，不能将这些日志称作全部已发请求的最终分母。软件校验中的 Parquet 文件仅是真实样本的表示测试，不是正式题目导出。

Public 三模态包、trace_index、Public Manifest、可导入归档：**未生成**。正式异常候选和 GT 草案：**未生成**。没有把注入记录直接转成异常答案，没有拼接资格数据，也没有导出部分数据冒充一小时题目。正式导出耗时、压缩包大小和导入兼容性：N/A。

## 证据与交付路径

- 原始失败尝试：`/data/stage5-v3/ops-competition-2026/runs/SF-PILOT-01/`。
- 注入与恢复私有证据：`/data/stage5-v3/ops-competition-2026/runs/SF-PILOT-01/private-injections/SF-PILOT-01-SERVICE/`。
- 当前计划及源哈希：`/root/ops-competition-2026/production-20261014/plans/manifest.json`，主计划在 `/root/ops-competition-2026/plans/SF-PILOT-01.json`。
- 控制端时钟诊断、Worker/Master journals：`/root/ops-competition-2026/production-20261014/results/`。
- 机器汇总：`/root/ops-competition-2026/production-20261014/results/production-failure-summary.json`。
- Public/Private 正式题目树：未生成。已有内容是受保护的私有尝试证据，不是发布包。

控制器 terminal SHA-256：`63c41fdc5577b69a290dc0503bbf943a0ec9affc17a9ee97d923876cf9e36b12`。各原始文件及双 Worker 归档 SHA-256 将在独立交付清单中列出。Stage5-v1/v2 冻结数据、研究准入规则及历史 Collector BLOCKED 记录保持不变；没有访问或操作 192.168.51.50、192.168.51.51。

## 下一步建议（本轮未执行）

优先把绝对时刻的等待与执行放到目标 Worker 本地，在提前分发、校验计划和授权后按本地时间启动；将控制端与执行端时钟偏差、派发延迟纳入无故障调度验证，并在 actual_start 写出后立即检查偏差。保留独立 watchdog 与实际恢复检查。

不建议仅调宽 2 秒阈值来挽救本次失败记录。本轮不修改已登记规则、不进行第二次真实注入，也不对 Mac 或节点时钟做事后调整。下一次如何登记后继尝试，应先由人工审核；本次记录永久保留。

**SF_PILOT_01_BLOCKED。已停止，等待人工审核。**
