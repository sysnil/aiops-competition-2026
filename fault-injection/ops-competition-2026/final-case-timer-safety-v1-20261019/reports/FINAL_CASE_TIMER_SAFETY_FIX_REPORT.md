# 决赛 Case Timer 生命周期安全修复验收报告

**结论：代码修复与无害 Marker 联合验证 PASS；正式生产仍未获准。** 本阶段真实业务故障 0 次、SF-013～SF-017 新正式 Attempt 0 个、Public/GT 0 份。SF-013 ATTEMPT-01/02 和既有十题保持原样。新版本位于 `/root/ops-competition-2026/sf013-adapter-v3-20261019/` 与 `/root/ops-competition-2026/final-case-timer-safety-v1-20261019/`，仅作为前瞻性修复与测试版本；没有对 ATTEMPT-01/02 的冻结代码或失败结论做追认。

## 根因与职责

ATTEMPT-02 在外部已注册 Worker2 Timer 后，控制器 `sf003_controller.py:186` 再次执行 `backend('arm')`。systemd 拒绝重复单元，`armed = True` 未执行；原 `finally` 在 230 行只按内存中的 `armed` 决定是否撤销，因此留下待触发 Timer。该 Timer 已在故障前按原 Manifest 显式撤销，历史故障次数为 0。ATTEMPT-01 则是目标 Pod/PID 快照先于多项准备工作生成，到启动时年龄约 180.2 秒，超过现有 180 秒门禁。

新版本由 **Master 控制器唯一注册** Worker Timer。启动器只验证环境、启动 capture/guard/controller，不 arm；控制器复制经哈希绑定的计划后先检查同 Attempt 是否已有 Timer，有则撤销并拒绝启动。控制器在 READY 后写入短期 `arm-owner.json`（run ID、action ID、Manifest SHA、unit、PID、Worker、计划时间），Worker `arm` 必须核对该记录。外部手工 `arm` 无 owner 时不可注册。

Worker `timer_lifecycle.py` 在创建 systemd Timer 前写入不可变 `arm-intent.json`。重复 arm、owner/状态校验失败、systemd-run 半途异常都会对**该 Attempt 的精确 unit**先写 `REVOKED.json`，再停止并确认 Timer/Service 均 inactive；撤销无法确认时返回 `ARM_CLEANUP_UNVERIFIED`，不得记录安全完成。重复 cancel 验证同一身份并保持原撤销记录不变，不扫描或停止其他 Attempt。正式 `fault_worker.py` 的所有已完成精确身份绑定后的 arm 异常也统一走此撤销路径。

Master 控制器的 `finally` 不再依赖 `armed` 布尔值，始终请求 Worker cancel、核对回执；失败终态为 `SF013_CLEANUP_UNVERIFIED`。启动器将 capture systemd-run、READY 握手、guard 与 controller 启动放入同一异常清理块。独立 `timer_guard.py` 监测控制器单元，控制器进程消失时按 Manifest 的 exact action 请求撤销，并停止未完成 capture；guard 自身配置为 `Restart=on-failure`。Worker 定时动作仍需既有短期安全租约及授权，真实故障适配器仍有独立精确 PID 恢复 Watchdog。

## 身份时效

新 DRAFT 在注册时要求身份快照年龄 ≤30 秒，正式入口要求 ≤150 秒，并继续保留原 180 秒硬门禁与 T0 前 90～240 秒启动窗。这样将身份刷新置于注册前最后一步，为采集器握手保留至少 30 秒余量。边界单元测试覆盖 ATTEMPT-01 的 180.2 秒拒绝，以及年龄 120 秒启动仍有 60 秒余量。此修改只属于未来新 Attempt 的代码，不回写旧计划。

## 实测验证

Master 新版本单元测试 **64/64 + 12/12 PASS**，相关 Python 源码编译 PASS。真实 Master 控制器/guard 与 Worker2 systemd Timer 的无害 Marker 最终批次 `20261019c` **10/10 PASS**：

| 场景 | 实际结果 |
|---|---|
| 正常 arm→触发→cleanup | 仅写入预注册 Marker 时间戳，Timer/Service 最终 inactive |
| 重复 arm、arm 安装后抛错 | 自动 revoke，计划时间后未触发 Marker |
| 身份、无害 capture、容量门禁失败 | 控制器 ABORT，计划时间后未触发 Marker |
| SIGTERM、SIGKILL | Worker 撤销 PASS，计划时间后未触发 Marker |
| 控制器重启、重复清理、T0 前撤销后等待 | 不允许再次 arm，原计划时间后未触发 Marker |

SIGKILL 的 journald 记录显示 `03:35:20Z` 控制器收到 signal 9，独立 guard 于 `03:35:21Z` 返回 `GUARD_ABORTED_ORPHANED_CONTROLLER` 和 Worker `ATTEMPT_TIMER_REVOKED_AND_INACTIVE`；本批无控制器自动重启，重启另有独立场景。Worker2 的独立 Watchdog 用牺牲 `sleep` 进程执行了真实 systemd 定时恢复，自测为 `COMPETITION_WATCHDOG_SELFTEST_PASS`、`COMPETITION_CLEANUP_ACTIONS_VERIFIED`，业务故障 0。所有 Marker 仅有写时间戳能力，不调用真实故障适配器。第一次 Marker 批次在测试控制器自动重启属性上与正式流程不一致，已保留证据；第二、三批次修正，最终以第三批次及其源码 SHA 为准。

另有独立的双 Attempt systemd 测试：A/B 同时 pending，撤销 A 后 B 仍 active；超过两者计划时间后 A 未触发、B 正常写入无害 Marker，最后两者均 inactive。合同 SHA `476cc245b394830367b315c5a29c62c9494b3c8e07db44d7e27adea29ffb91be`，结果 SHA `1f38b775d24e98e6339d23abb6e6e487108be08b6c880112ee4d63a3c4f8c725`。这直接验证撤销范围不会跨 Attempt。

最终 Marker 测试合同 SHA-256 `b0a119a5e343509d967b288f99887c74fcc51375e3d8f0ee1b9491c98da1d7e5`，最终结果 SHA-256 `47bdd75070fb0e579e24dffdaee6b45a109d089b5d2b5379d86cb15fc652b28b`。Worker2 与 Master 的共享 `timer_lifecycle.py` SHA 均为 `0c4b76a8c7ec1c361d7943d75a886c2721c296dcdb29a9bc4e5d82618c7e44bc`。完整 66 个源码/合同文件与 10 份证据哈希见 `results/code-and-evidence-sha-manifest-v3.json`，Manifest SHA-256 **`b19a7d27918d096602895db082a19a70d4ea43d2e44943cce8ab7b6b33b69b23`**，状态明确为 `NOT_PRODUCTION_PERMIT`。较早两版 SHA 清单保留为历史，不作为最终版本。

主要修改：新版本 `scripts/timer_lifecycle.py`、`fault_worker.py`、`sf003_controller.py`、`start_formal.py`、`timer_guard.py`、`startup_timing.py`、`run_registry.py`，以及独立 Marker 脚本和回归测试。核心代码 SHA 分别为 `0c4b76a8…c7e44bc`、`fc76dcfe…ac46b0`、`27159e9d…5fafe2`、`d1af9343…24d573`、`5ffdf14c…0f778d`、`1b922428…3df971`、`35659ff8…de042`；完整值以 Manifest 为准。

## 五主机与历史资产

最后快照：Master 根盘/数据盘 **45.250/60.660 GB**；Worker1 **36.712/19.073 GB**；Worker2 **53.771/17.988 GB**；Worker3 **35.297/11.306 GB**；VM1 根盘 **87.401 GB**。四个 Kubernetes Node Ready、四个 Calico Node Pod Ready；Prometheus healthy、OpenSearch green；Collector grpc/http refused 均 0，工作集约 301 MB，Logs/Traces 最终存储新鲜。`HOST_RESIDUE_PASS` 与 `COMPETITION_UNIT_RESIDUE_PASS`，五主机无活动竞赛 Service/Timer。

原 SF-003～SF-012 总账与交接索引 SHA 仍分别为 `813035c9…17e69f8` 和 `8b8d9ed8…64d401`。只读复核原十题 **10/10 Public Manifest、60/60 Public 文件 SHA、窗口与行数 PASS**，未读取或修改原始 capture/Private GT。SF-013 ATTEMPT-01/02 失败记录 SHA 仍为 `0e7958de…076da3a4b471`、`cdcaf590…59df6bbe17b`，未覆盖。

## 五题存储预算

采用历史单题峰值预留 **7,350,324,113 B**，十进制底线 **5,000,000,000 B** 不变。Worker3 数据盘约 11.39 GB，不可承担该预留再加 2 GB 后台写入。DRAFT 建议新 Case 的独立 Worker 副本改为受保护的系统盘目录：SF-013/016→Worker1、SF-014/017→Worker2、SF-015→Worker3；Master 仍保留原始数据与 Public/Private。每题源 Manifest、逐文件 SHA、异机 SHA 与恢复读回后才更新交接索引；归档全程监测每台机器根盘与数据盘，不删除历史数据。

按五题顺序、上述每题 7.35 GB 预留及每主机一次 2 GB 后台余量，Master 数据盘最终预测约 **23.91 GB**，各关键文件系统高于 5 GB。然而更保守的“每题在 Worker 数据盘额外消耗 2 GB”敏感性计算中，Worker3 数据盘在 SF-016、SF-017 后预测仅 **3.31/1.31 GB**，因此**不能无条件批准五题连续生产**；必须逐题刷新实际盘位和剩余写入预测，不足即停。`results/sequential-storage-projection-v3.json` 保存每题每盘数值。

Worker1/2/3 系统盘独立目录已用同一份 2,836 B 无害审计文件完成 Master→Worker 传输及 Worker→本机恢复读回，三台 SHA 均为 `f90ea3daaa4e2c7814262b5b0c01bbedd2bc98e2f891f91cc1ec90dd59911cf1`，JSON 可解析，目录 0700、文件 0600。它只证明路径和小文件校验流程；**7.35 GB 满载归档及 Parquet 恢复读回尚未实测**。

## 尚未通过的安全边界与下一步

1. 本轮没有重启生产 Master/Worker：真实主机异常重启的 Timer/guard 生命周期**未验证**。`Persistent=false` 和短租约是代码约束，不等于实际重启结果。Master 在短租约有效的瞬间失联仍有理论窗口；现有独立故障 Watchdog 可限制已启动故障的持续时间，但不能证明该窗口内绝不会启动。需要在隔离 systemd 环境或组委会批准的维护窗口进行真实生命周期验证，并审查 Worker 对 Master 实时存活的 fail-closed 方案。
2. 新 v3 代码尚未通过完整 1,800 秒无故障的 capture/observer/controller/guard 联合演练；Marker 验证的是共享 Timer/guard 核心与真实 systemd，并非正式数据质量验收。
3. 新归档路径需先做足额无故障归档与 Parquet/Manifest 恢复读回，并建立逐题动态预算。尤其 Worker3 数据盘的累计后台写入必须实时复核。
4. 未来新 Attempt 的目标身份、计划、代码、Admission、容量合同必须另行预注册并 SHA 冻结；当前 v3 `case-design.json` 明确为 DRAFT，准备器要求未来人工批准的正式 freeze 状态，故未创建或冻结 SF-013 ATTEMPT-03，也未恢复 SF-014～SF-017。组委会确认安全修复前不得启动真实故障或生产。

**本阶段最终状态：`TIMER_SAFETY_CODE_AND_MARKER_PASS__PRODUCTION_BLOCKED_PENDING_LIFECYCLE_REVIEW`。**
