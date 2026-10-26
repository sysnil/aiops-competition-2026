# SF-003～SF-012 正式题目生产执行状态

**截至 2026-10-15 17:30 UTC：10 题中 READY 1、BLOCKED 9。** READY 仅表示已完成真实采集、双故障、Public/Private 草案和归档，可进入导入测试；不表示 Private GT 已完成人工审核。没有把无故障演练或失败尝试记作正式题目。

| 题目 | 原计划机制 | 真实状态 | 依据或阻断 |
|---|---|---|---|
| SF-003 | PodCPUStress、HTTP ReqDelay、PodKill | BLOCKED | PodCPU/PodKill 尚无本组合的安全适配、Watchdog 与恢复资格；ReqDelay 已在 SF-010 单独验证，不能自动授予三事件时间合同 |
| SF-004 | NetworkDelay、HTTP RespDelay、PodMemoryStress、ServicePause | BLOCKED | 当前拓扑的 NetworkPath 证明和 NetworkDelay 撤销资格、RespDelay 正式适配及四事件合同未通过 |
| SF-005 | HTTP RespReplaceCode、ContainerKill、NetworkLoss | BLOCKED | HTTP 替码、容器终止与网络丢包的组合安全和恢复资格未通过 |
| SF-006 | PodCPUStress、HTTP ReqAbort、NetworkBandwidth、HTTP RespAbort、ServicePause | BLOCKED | 四项新机制未合格；原五事件非重叠时间表仍超出 1800 秒 |
| SF-007 | NodeCPUStress、HTTP ReqDelay、PodKill、NetworkDelay | BLOCKED | Node/Pod/Network 安全资格未通过；原四事件时序超出窗口 |
| SF-008 | NetworkLoss、HTTP RespReplaceCode、PodCPUStress、HTTP RespAbort、NetworkBandwidth、PodMemoryStress | BLOCKED | 多项机制未合格；原六事件时序超出窗口 |
| SF-009 | NodeCPUStress、ContainerKill、HTTP RespDelay、HTTP ReqAbort、ServicePause | BLOCKED | 多项机制未合格；原五事件时序超出窗口 |
| SF-010 | ServicePause、HTTP ReqDelay | **READY** | ATTEMPT-07 完成连续 1800 秒、两次真实故障及撤销、六段三模态质量、Public/Private 草案、双副本归档和最终健康检查 |
| SF-011 | PodMemoryStress、NetworkDelay | BLOCKED | NetworkDelay 当前路径证明、独立正常对照及正式联合执行器未合格 |
| SF-012 | NodeCPUStress、HTTP RespReplaceCode | BLOCKED | Node 安全停止合同与 HTTP 替码适配器未合格 |

SF-010 的权威执行报告：`/root/ops-competition-2026/reports/SF-010-attempt07-report.md`。原始数据位于 `/data/stage5-v3/ops-competition-2026/runs/SF-010-ATTEMPT-07/`，Master 上可供后续导入测试的六文件 Public 包位于 `/data/stage5-v3/ops-competition-2026/exports/SF-010-ATTEMPT-07/public/`，私有异常 GT 草案位于同级 `private/`。未操作 192.168.51.50/51，也未发布 API。

执行期间保持新的十进制 5,000,000,000-byte 磁盘保护线、动态五主机预测与故障自动清理。Worker2 当前数据盘剩余 49,794,150,400 bytes，下一题容量检查仍须单独执行；按历史单题敏感性预算直接累计十题将低于保护线。此前授权的 51 归档迁移只能在完整 SHA 与恢复读回后逐批执行，不得直接删除历史数据。本轮没有做空间删除或迁移。

SF-010 的 ATTEMPT-01～06 均在首个真实故障前终止，原始证据永久保留；ATTEMPT-07 才完成正式双故障生产。继续下一题之前必须逐机制完成安全资格和新的 30 分钟时间合同，不能替换原故障组合、减少数量或把数学候选时序当作执行许可。SF-006～SF-009 的窗口冲突需要单独审查，不能自行更改。
