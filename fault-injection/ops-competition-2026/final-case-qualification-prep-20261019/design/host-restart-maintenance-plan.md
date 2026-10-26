# Master / Worker 重启生命周期验证审批草案

**状态：未执行，需可信隔离 VM 或组委会批准维护窗口。** 当前 Master 和本机未发现可复现生产 PID 1、systemd Timer、SSH、boot ID、cgroup 与 Watchdog 的现成隔离 VM/rootfs。容器进程重启、Guard 重启和模拟 boot ID 不等价于主机重启。本轮禁止直接重启生产 Master/Worker。

首选由组委会提供隔离 VM：复制当前版本代码/合同及受限 SSH 测试身份，使用无害 Marker 与牺牲进程，不复制生产私钥或 GT。独立验证 Master 断电/重启、Worker 重启、待触发 Timer、Guard 重启、撤销状态持久化、Master 不可达拒绝新授权、已执行牺牲进程的独立恢复；记录预定/实际时间、boot ID、单位状态、授权/撤销记录和终态 SHA。隔离环境必须覆盖 systemd PID 1 与真实 SSH 通道；否则不宣告 PASS。

若隔离环境不可得，申请非赛时维护窗口，明确 Master 控制面、Worker3 Collector 本地 PV、业务流量与监控可能中断。批准前准备：五机备份与恢复通道、云平台控制台/带外访问、历史数据哈希、当前 Pod UID、4 Nodes Ready、Calico 3/3、Collector/Prometheus/OpenSearch/Jaeger 和十进制 5 GB 余量；确认无正式实验或活动故障。仅注册未来触发的**无害 Marker**、独立 Guard 和牺牲进程 Watchdog；每次只测一台主机，前一台恢复并通过完整健康门禁后才进入下一台。

验收：重启前已撤销 Action 在原定时刻之后仍无 Marker；未取得实时 Master 授权的待触发 Timer 必须 fail closed；Guard 和受限 SSH 恢复后的状态可审计；牺牲进程无暂停/holder 残留；最终 4 Nodes Ready、Calico BGP 3/3、Collector 零新增拒收、数据库健康、业务恢复、五机磁盘余量达标。若任何恢复通道、Watchdog 或空间门禁失效，停止后续测试，保留原始日志并依批准的回滚/带外方案恢复。**该维护计划本身不授权重启，也不授权真实故障注入。**
