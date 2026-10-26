# SF-003～SF-012 正式生产启动尝试（2026-10-15）

**终态：NO_FORMAL_CASE_ELIGIBLE；正式题目 0/10，正式故障注入 0，Public/Private 题目包 0。**用户已授权真实生产，本次按原十题顺序执行新鲜准入审查。没有合格题目，因此未签发真实故障租约、未注册正式 Worker Timer，也未把独立机制 Rehearsal 记成 SF-010。

## 新鲜门禁

- 五主机残留：`HOST_RESIDUE_PASS`；四个 Kubernetes Node Ready，`otel-demo` 39/39 Pod Ready。Collector UID `ac4b46f1-428e-4814-90ba-c52eaa43e9f3`，配置 SHA-256 `128ed286e8a959aacddc2ee6c00e2d6e8b0a3eb18ea43e5de730a20e3793af2d`，gRPC/HTTP refused 均为 0，Metrics/Logs/Traces 存储新鲜。
- 新版 5,000,000,000 bytes 十进制磁盘保护线：五主机实际系统/数据盘下一题预算为 `DYNAMIC_STORAGE_PASS`；这一项不授予故障权限。旧版 20 GB 门槛和 34/31/29 GiB 静态预留没有用于本次新版本。历史 SF-001/002 文件未修改。
- 51 的无故障 5.88 GB 归档传输已经逐文件验证，但正式 Private 数据加密封装和 30 分钟执行器归档接线仍待完成；当前新题若要启动，只能先使用仍满足门槛的 Worker2+Worker3 路径。
- `results/formal-start-final-20261015.json` 是本次逐题启动门禁记录（SHA-256 `5effa3fab8e378f74b1d70fb43a5c9eb2fcf5d4bc43c130038aec8d2c5cd90bb`），结果为 `NO_FORMAL_CASE_ELIGIBLE`。门禁新增快照 5 分钟新鲜度检查和部分题可执行状态的正确区分；相关测试全部通过。

| 题目 | 原组合保留 | 本次状态 | 直接阻断 |
|---|---|---|---|
| SF-003 | PodCPUStress、HTTP ReqDelay、PodKill | BLOCKED | PodCPU/PodKill 安全适配器；ReqDelay 正式适配器；时间合同与正式执行器 |
| SF-004 | NetworkDelay、HTTP RespDelay、PodMemoryStress、ServicePause | BLOCKED | NetworkDelay 路径/撤销证明；RespDelay 适配器；时间合同与正式执行器 |
| SF-005 | HTTP RespReplaceCode、ContainerKill、NetworkLoss | BLOCKED | HTTP 替码适配器；Kill/Network 安全资格；时间合同与正式执行器 |
| SF-006 | PodCPUStress、HTTP ReqAbort、NetworkBandwidth、HTTP RespAbort、ServicePause | BLOCKED | 四项新机制及现有非重叠时序均不合格 |
| SF-007 | NodeCPUStress、HTTP ReqDelay、PodKill、NetworkDelay | BLOCKED | Node/Pod/Network 安全资格；现有时序至少超 15 秒 |
| SF-008 | NetworkLoss、HTTP RespReplaceCode、PodCPUStress、HTTP RespAbort、NetworkBandwidth、PodMemoryStress | BLOCKED | 多项机制；六事件时序超过 1800 秒 |
| SF-009 | NodeCPUStress、ContainerKill、HTTP RespDelay、HTTP ReqAbort、ServicePause | BLOCKED | 多项机制；五事件时序超过 1800 秒 |
| SF-010 | ServicePause、HTTP ReqDelay | BLOCKED | ReqDelay 正式适配器和 30 分钟双事件控制器未验证；仅数学时序可放入 |
| SF-011 | PodMemoryStress、NetworkDelay | BLOCKED | 当前拓扑的 NetworkPath 路由/独立对照及正式执行器未合格 |
| SF-012 | NodeCPUStress、HTTP RespReplaceCode | BLOCKED | Node 安全停止合同及 HTTP 替码适配器未合格 |

上述各题的实际故障开始/结束/恢复、Metrics/Logs/Traces 正式数据量、Public/Private、双副本归档与题目 SHA-256：**均未产生，不标 READY**。既有方案 B 的机制种类、数量和次序没有修改。

## 真实业务机制 Rehearsal（不是正式题目）

独立原始目录：`/data/stage5-v3/ops-competition-2026/rehearsals/SF30-REQDELAY-MEDIUM-20261015/`。不可变登记 SHA-256 `bdc515956494c22d690782e1bc7fd1cc080e8982c4aad19caad0f80459190e52`。在原 MEDIUM 状态机 1.4 sessions/s 与 Image 1/s、Cart 2/s 固定负载下，只有 60 秒 During 的正常业务 `home` 请求携带 250 ms Envoy 延迟头；不改集群代理配置。systemd `RuntimeMaxSec=510` 与 `KillMode=control-group` 限制所有子进程，单请求退出后无持久故障。

| 实测 | Before 180 s | During 60 s | After 180 s |
|---|---:|---:|---:|
| 首页请求数 | 252 | 84 | 252 |
| 首页 p50 / p95 | 9.19 / 15.80 ms | 260.82 / 266.16 ms | 9.93 / 15.47 ms |
| Image 请求数、p95 | 180、2.96 ms | 60、2.94 ms | 180、3.10 ms |
| Cart 请求数、p95 | 360、11.88 ms | 120、9.03 ms | 360、10.73 ms |

固定随机种子 `20261015032`；588 会话、3,732 次状态机请求、420/420 Image 和 840/840 Cart 均完成，业务与对照错误 0，漏调度 0。84 条首页请求在故障期实际携带延迟头；其他阶段 0。运行期间 31 次主机/Collector 安全样本均合规，Collector 工作集峰值 321,572,864 bytes（原安全上限 380 MiB），receiver refused 始终 0；五主机动态磁盘监控及最终残留检查均 PASS。运行终态 SHA-256 `802c0b1df7e1ae8f11322a0b28875a1e5f86ce66821a17a1b00b2439d34545b6`，独立 systemd 单元成功退出、无遗留 Timer。

Trace 使用 `JAEGER_OPENSEARCH_STORAGE`。第一次 +60 秒审计原样保留为 `MEDIUM_TRACE_MATCH_INCOMPLETE`：它把 Image 的两个合法 Proxy ingress Span 误判为“不是恰好一个”；经原始 Span/Span ID 核对后修复审计条件并增加重复 ID 拒绝测试。+120 与 +240 秒新审计均为 **1,848/1,848 个选定请求匹配**，读取 14,868 个不重复的真实 Span；+240 审计 SHA-256 `9bf60feff0ea1e39e28a213e58787cd0bd3b0c9fff0fae0f1af01e23852c8c62`。Image 多入口 Trace 数量分别为 180/60/180，解释了全部首次审计差额。失败审计没有被覆盖。

此结果支持 HTTP ReqDelay 的**独立业务机制有效性**，不等于正式 SF-010 合格：尚缺把同一版本嵌入 30 分钟正式控制器、与 ServicePause30 的独立 Worker Timer/短租约、首段完成审计、安全撤销、完整三模态采集、Public/Private 导出和双归档联合验证。其余机制也不得借用这个结果获得资格。

## 停止条件与下一步

当前继续真实题目注入将违反原计划的机制安全和正式时序要求。先完成 SF-010 的双事件正式执行器和独立无故障联合演练，重证当前 Recommendation 目标身份，验证 HTTP ReqDelay 正式封装；再逐项完成 SF-003 等题所需的 PodCPU、PodKill、Network、Node 与其余 HTTP 机制资格。SF-006～SF-009 若仍无法容纳原事件数量，只能保持 BLOCKED 或提交计划修订，不能删减故障或拼接数据。本轮没有访问比赛样板机、TEST GT，也没有删除历史归档。
