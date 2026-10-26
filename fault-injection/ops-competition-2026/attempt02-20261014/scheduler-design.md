# ATTEMPT-02 调度修复

Master UTC 为统一时间，控制器在 Master systemd service 执行。Mac 仅部署和查看。
Worker3 / Worker2 分别使用绝对 UTC 的 systemd calendar timer，AccuracySec=10ms，RandomizedDelaySec=0，Persistent=false。
计划、目标 UID、容器、PID start_ticks、原适配器及 holder 哈希绑定。原 competition_adapter.py、恢复 timer 和 cleanup 代码不修改；新 wrapper 只登记两个授权新 attempt ID，并在原 adapter 保存 ACTIVE 的同步调用处立即检查 actual_start。

Master 向 Worker 每次发出最多 4 秒授权租约；计划哈希、实时安全心跳、近期 Master/Worker RTT 时钟界、Worker NTP 均须有效。时钟测量总界必须不超过 0.5 秒；原始及含不确定度的启动误差均须不超过 2 秒。超差或撤销/租约过期立即走原 adapter 的 finally cleanup，并取消后续定时任务。原独立 duration+5 秒回滚 timer 不依赖 Master、Mac 或 wrapper 存活。

安全检查结束后才能发租约；第二个故障须通过新的 baseline、Global Safety 和 authorization。提前部署 timer 不等于得到执行许可。服务结束后先检查公开恢复，再允许第二阶段。

测试包含两 Worker 各 3 次无害 marker；额外每 Worker 一次故意 2.3 秒延迟 marker，验证超差终止并取消后续 marker。所有 marker 均不修改业务、Kubernetes 或资源限制。

原失败实验不修改、不重写时间戳。ATTEMPT-02 是唯一新正式尝试，任何失败不自动第三次注入。

导出在每个 capture 的 [start,end) 分区中选择 Metric 样本，明确排除 query_range 返回的右端点，避免交界处重新查询得到不同值而混用。全部原始文件保留。Trace/Log 原 ID、UTC 精度保持，未知值不造零。此规则在新正式尝试注册之前确定。
