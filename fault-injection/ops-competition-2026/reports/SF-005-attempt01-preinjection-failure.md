# SF-005 ATTEMPT-01 注入前失败记录

状态：`FAILED_BEFORE_INJECTION`。未执行 ServicePause；没有正式 30 分钟采集、Public 包或 Private GT 草案，不计为已生产题目。

冻结登记 Manifest SHA-256：`1f9f984fc5be457eb12b25a7212dc4ff51980b4e2e4f46fc9c26e759e5815578`。计划窗口原定 2026-10-16 06:04:47.049964 UTC 开始，故障计划 T+600。控制器在 T0 前 ARMED 预检导入 `pilot_common.py` 时缺少 `SF005_RUN` 环境变量，失败终态 SHA-256：`427bc29208080cc11bcaf4eeb8ab11608fd2e32b1c27e9e5a3413bbba8d1cb32`。采集器第一次启动早于代码允许的 T0-240 秒，立即拒绝且未写采集数据；其 systemd 原始日志保留在 Master。

控制器撤销 Worker3 待执行 Timer；检查时 Controller/Observer/Timer/Service 均不活动，五主机残留审计 `HOST_RESIDUE_PASS`。原始失败目录 `/data/stage5-v3/ops-competition-2026/runs/SF-005-ATTEMPT-01/` 仅约 60 KB，未删除、未覆盖。ATTEMPT-02 使用独立目录和新的代码/计划哈希，不能替换此失败记录。
