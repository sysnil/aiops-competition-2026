# SF-013～SF-017 真实机制资格试验草案

**状态：待组委会单独批准；本文不授权注入。** 任何试验先建立新资格 ID、冻结目标 UID/容器 ID/PID/start ticks/cgroup、控制源码 SHA、负载、Watchdog、时序和停止条件。使用当前统一 v3 入口；不得复用旧本地 4 秒租约脚本。每次只执行一个已批准的机制试验，完成五机恢复与残留检查后再考虑下一项。

| Case / 机制 | 拟验证的真实效果 | 最低准入与恢复证据 | 本轮已有证据 |
|---|---|---|---|
| SF-013、SF-016：Worker2 Frontend PodMemory 32 MiB/300 s | 仅一个当前 UID 的 Frontend Pod，正常副本在 Worker1 | 两副本 Ready 且正常业务有请求；目标 PID/start ticks/cgroup 与 UID/Container ID 一致；Worker2 MemAvailable 保持原 8 GiB 安全线、无 OOM/Pod 重建；独立 Watchdog 终止 holder；逐实例请求、内存与业务恢复 | Worker2 独立牺牲进程 32 MiB/Watchdog PASS；真实业务 Pod **未验证**。两题正式目标必须分别注册，不能自动共用一个资格回执。 |
| SF-014：Worker3 Recommendation 双副本 ServicePause 30 s | 同一服务的两个当前 UID 与精确进程身份 | 两副本均有真实请求；两进程被暂停并按时 SIGCONT，未波及 Collector/节点；独立 Watchdog 同时恢复两 PID，服务请求恢复且无残留 | Worker3 两个牺牲进程暂停/定时恢复 PASS；真实 Recommendation **未验证**。 |
| SF-015：Master HTTP ReqDelay 250 ms/60 s | 仅普通首页 GET `/`，不改变 Image/Cart 对照 | 请求级许可与撤销、真实请求头/响应延迟、对照不带头、窗口外恢复；Collector/请求 ID/Proxy Span 完整 | 回环 HTTP 的授权前后 Header 观察 PASS；业务 Frontend **未发送故障头**。 |
| SF-017：上述 HTTP + Worker3 ServicePause | 两 Action 明确非重叠、独立许可/终态 | 单机制资格均通过后，使用新独立资格 ID 联合验证；任一失败撤销兄弟 Action，完成 1800 秒业务/三模态和恢复 | 完整无故障双 Action 演练 PASS；联合真实效果 **未验证**。 |

每个真实试验还需在开始前通过 4 Nodes Ready、Calico 3/3、Collector/Prometheus/OpenSearch/Jaeger、五机磁盘动态预算、普通业务/对照采样、准确时钟和授权 Watchdog；任一失败不得启动。权限与强度仅限上表，不批准其他 Node/Network 故障，也不批准正式 Case 生产。输出只供资格复核，不能直接生成评分 GT 或把失败尝试替换为成功尝试。
