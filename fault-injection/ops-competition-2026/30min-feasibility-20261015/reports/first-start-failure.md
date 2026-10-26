# 第一次无故障演练启动失败

2026-10-15 09:03:43 UTC，`ops-sf30-nofault-20261015.service` 在预检阶段退出。预检读取正在运行的 systemd 服务时，把当前演练自己的服务匹配为另一实验，报错 `Another competition run is active`。故障发生在创建 rehearsal 数据目录之前，未启动业务负载、采集或故障注入。

修订仅允许明确登记的 `ops-sf30-nofault-r2-20261015.service` 在自身预检中出现；任何其他 `ops-sf002-`、`ops-sf30-`、`ops-b8-` 运行服务仍触发拒绝。第一次 journal 原始记录留在 Master 的 systemd 日志中，不覆盖。
