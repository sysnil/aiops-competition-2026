# SF-013～SF-017 生产进展与阻断（2026-10-19）

**状态：`PRODUCTION_BLOCKED_PENDING_REAL_MECHANISM_QUALIFICATION_AND_EXPORT`。正式新题 0/5；真实故障注入 0；新 Public 包 0；向 51 传输 0。** 10 小时是目标，不构成跳过资格、恢复、完整采集或 GT 审核的理由。本报告不改变 SF-003～SF-012 或任何失败 Attempt。

## 清晰异常候选设计

独立 DRAFT：`/root/ops-competition-2026/final-case-formal-entry-v5-20261019/design/five-case-clear-signal-plan-draft.json`（SHA-256 `f2517a2385bbb409e4e7ffaf835ba603f411c6628ba269fab7122d93486f1039`）。SF-013/016 为单 Pod 内存压力 300 秒；SF-014 为双副本 Recommendation 暂停 300 秒；SF-015 为首页 250 ms 请求延迟 300 秒；SF-017 为首页延迟 240 秒及服务暂停 240 秒，间隔 120 秒，末段恢复观察 600 秒。每题仍为完整 1800 秒窗口，故障强度不变，延长时长必须重新资格验证。SF-013 与 SF-016 的机制和目标可能相似，不能仅靠题号或种子称为不同异常类型。

计分 GT 目标为每题最多 5 个有 Public 证据的归并事件，优先持续至少 90 秒。原始候选和所有未入选事件保留；未入选项须有人工拒绝理由，超过 5 个有资格事件则阻断审核，绝不自动截断。只读门禁 `check_gt_event_budget.py` 已有 2 项单测；历史 SF-007 为 23 候选/4 归并事件、SF-009 为 16 候选/6 归并事件，按新清晰度门禁均需审核，不追改历史 GT、预测或成绩。

## 新候选执行器与无害验证

新建独立 v2 共享入口与 v4 正式入口，旧 v1/v3 文件不改。v4 修复了资格回执包含 Manifest SHA、而 Manifest 又包含回执 SHA 的哈希闭环：回执现在绑定 Action 范围、目标注册哈希与适配器源码 SHA，最终 Manifest 再引用回执 SHA。新增测试证明回执能先于 Manifest 生成，篡改范围会拒绝。SF-017 合同强制至少 120 秒 Action 间隔。核心 SHA：`control_contract.py` `d34d2d02dd101788f086f6243e28473ce45d40a4f40c79e4aa67e383d84a7e68`；`qualified_effect.py` `53ac27475c3d773d997aee90816d5bd0f8841d012a279545c2af4c6081d21bd0`；v2 `entry_policy.py` `225d1eeea19776424f77a7a790ae86842bedfd293f350b1723f5244d9cc64519`。编译 PASS；共享授权单测 13/13、正式入口及 GT 单测 15/15 PASS。

新版 Worker2/Worker3 无害 Marker 联合测试：三个正向动作均一次性授权并触发；SF-017 预撤销动作未触发；两台 Worker 最终待触发竞赛 Timer 均为 0。第一次联调因候选状态目录与现有受限 Master SSH 入口不一致而 fail-closed（0/3 正向 Marker），失败回执保留；修正为沿用现有受限授权状态目录后重新测试 PASS。没有开放新生产授权端口，也没有执行真实适配器。

v4 独立全长无故障 Run `SF-017-ATTEMPT-MARKERTEST-E0199BAE`：Manifest SHA `e1ddddcf5c4de5fd1bd5af1a0a37e0e6d0f1467241097e0ba8245d5bb4bcdfe9`，freeze SHA `3d803a2cf73f711f4362b3bf05e81da7570e336e81ac414e92ca1fe0800eca14`。六个 300 秒采集段共 52,360 Traces、343,734 最终 Spans、188,681 Logs；7,200/7,200 普通对照请求匹配 Proxy Span；Collector Metrics/Logs/Spans refused 增量均为 0，+120 到 +240 无新增 Span。质量报告 SHA `4a16ca9a3b6eb0b20e15812666a19b4705f37a7dc2c3f7538205f23def2a3292`。原始 3,588 文件、4,453,707,797 B 在 Master 和 Worker2 `/root/ops-competition-2026/rehearsal-archives/SF-017-ATTEMPT-MARKERTEST-E0199BAE-full1800/`，Worker2 独立 `sha256sum -c` 与读回 PASS；归档 SHA 清单 `ff091c9d4a9eae8e1e44a27c7b70da1151e1cffe1100d39e8d6b83ee8c357bf7`。末次重新核对冻结的 44 份源码/配置 SHA 无漂移。该演练 `faults_injected=0`、`formal_attempt_created=false`，不算任何题的真实机制资格。

更新后的 v5 最终候选又独立完成全长无故障 Run `SF-017-ATTEMPT-MARKERTEST-22991414`：Manifest SHA `461aeb07ff55e845b87ff7c732b4aef105fa7ddcd2e83cf607ae83fad4b22f9e`，freeze SHA `47742ec52e272c2e6f18b21abd4ebbef6e064ebce732cc79e5e5a758f6e3f1ff`，**45 份来源文件（含 v5 真实适配器）**末次复核无漂移。v5 `control_contract.py` SHA `745951076894854fafb4e2e8329600b64fa134629efeffeddfc3faba6e5482db`；`qualified_effect.py` SHA `23fb73980ea59536bd7a35b971535b4aeadc26f1db0204ee6bb7a41b0c31970e`。六段最终 53,746 Traces、352,749 Spans、195,010 Logs；7,200/7,200 对照请求匹配；Collector 三类 refused 增量 0；`faults_injected=0`、`formal_attempt_created=false`。质量报告 SHA `d78be23687d9922925d7e39a95b71c2b9169b2016c960069dace09614d24e475`。Master 原始 3,682 文件、4,576,389,844 B 与 Worker2 `/root/ops-competition-2026/rehearsal-archives/SF-017-ATTEMPT-MARKERTEST-22991414-full1800/` 双份保存；Worker2 独立 `sha256sum -c` 和读回 PASS，归档 SHA 清单 `8d98bbf9b82ec6d4503bbd37164977d8cf01d96f1a42cb6e4e6a9e7d9347643b`。**v5 无故障采集门禁 PASS，但不是任何真实机制的效果/恢复资格。**

## 隔离主机重启测试

Mac 上两台独立 Ubuntu 24.04 VZ VM 运行真实 systemd 和 SSH。生产授权核心源码原样复制；VM 包装器只替换测试路径、IP/端口和测试密钥，未复制生产私钥。实测正向 Timer 消费签名一次性 grant 并触发无害 Marker；Master VM 重启后旧 Timer 到点、SSH 仍可达，但 boot ID 改变且无 grant/Marker；Worker VM 重启后瞬态 Timer 消失，过原定时刻无 grant/Marker；预撤销、Guard 退出、授权通道断开均无 Marker；独立 systemd Watchdog 将精确牺牲 PID 从 `T` 恢复为 `S`。机器可读记录在本地 `final-case-restart-vm-20261019/vm-*-result.json` 与 `watchdog-result.json`。Lima 在 VM 重启时重生成了测试 SSH Host Key；已核对新公钥指纹后重新固定。此为**隔离环境核心生命周期验证**，不等于生产 Worker3/Collector PV 的实际重启验证，也未证明真实业务故障恢复。

## 存储与当前健康

Worker3 `/data` 先前已通过清理两份 Master 逐文件 SHA 匹配的旧无故障重复观测数据增加约 11.9 GB。随后将旧 v3 成功演练和 v2 失败演练完整迁至 Worker3 系统盘，分别 `rsync --checksum --dry-run` 零差异；v3 另有 Worker2 完整归档且 Worker3 `archive.sha256` 全量 PASS。只删除 Master 两份旧演练的 `capture/`，保留终态、控制日志和迁移索引；释放约 9.06 GB。v5 演练完成后，又将旧 v4 成功演练全量迁至 Worker3 系统盘，checksum 零差异、归档 SHA 与三项 JSON 读回 PASS；Worker2 仍保有 v4 全量归档，再清理 Master 的旧 v4 `capture/`（约 4.45 GB）。**最新 v5 原始 capture 仍留在 Master 和 Worker2。**此外，Worker1 的旧 SF-007 ATTEMPT-02/04 归档和 Worker2 的旧 SF30-NOFAULT 归档均先迁到各自系统盘、checksum 零差异并读回，再用符号链接保留原 `/data` 访问路径；分别释放约 1.99 GB 和 6.08 GB，迁移记录在 `reports/storage-cleanup-20261019/`。没有触碰 OpenSearch、Prometheus、Collector PV、正式 Case 或历史 GT。

末次五机只读 Safety 为 `DYNAMIC_STORAGE_AND_BASIC_HEALTH_PASS`、当前单题 `STORAGE_PASS`。可用字节：Master 数据盘 `46,693,163,008`，Worker1 数据盘 `17,706,233,856`，Worker2 数据盘 `22,600,167,424`，Worker3 数据盘 `22,540,705,792`；51 数据盘先前只读检查约 `191,481,778,176`。按每题 Master 峰值 7,350,324,113 B、各 Worker 数据盘每题背景增长 2 GB 的保守五题模型，Master 约余 9.94 GB、Worker1 数据盘约余 7.71 GB、Worker2 数据盘约余 12.60 GB、Worker3 数据盘约余 12.54 GB；这仍是预测，不是永久执行许可，每题须重新测量并动态停止。4/4 Nodes Ready；关键业务、Collector、Prometheus、OpenSearch、Jaeger Pod 均 Running；OpenSearch green、Prometheus healthy；四机/VM1 未见 `netem`/`tbf`，五机无待触发竞赛 Timer。

## 五题状态与下一门禁

| Case | 新时长控制合同 | 无害链路 | 当前真实机制资格 | 正式 Public/GT | 51 传输 |
| --- | --- | --- | --- | --- | --- |
| SF-013 | PodMemory 300 s | Marker PASS；v5 Watchdog PASS | 当前业务 Pod 未用 v5 真实验证 | 无 | 无 |
| SF-014 | ServicePause 300 s | Marker PASS；v5 Watchdog PASS | 新时长未真实验证 | 无 | 无 |
| SF-015 | HTTP ReqDelay 300 s | 单测及 v5 1800 s 无故障路径 PASS | 真实首页 300 s 未验证 | 无 | 无 |
| SF-016 | PodMemory 300 s | Marker PASS；v5 Watchdog PASS | v5 独立业务 Action 未真实验证 | 无 | 无 |
| SF-017 | HTTP 240 s + Service 240 s | Marker/v5 1800 s 无故障 PASS | 两个新时长及真实双 Action 均未验证 | 无 | 无 |

在启动正式 Attempt 前还须实现并验证**同版、可审计的独立真实机制资格执行**，产生与当前目标及源码绑定的回执；把新入口接入正式 Public/Private 导出、Admission、双副本归档和 51 受保护导入链，再对最终源码重新做全长无故障验证。现有新入口只完成控制与无故障采集，不能凭单测或历史 SF-007/SF-009 的真实故障记录伪造资格回执。

资格预检另发现 v4 适配器 Watchdog 自检的状态路径固定为 `qualification/private-runtime/sf01-watchdog-v3-<host>.json`，Worker2/Worker3 均已有 10 月 13 日的历史文件；历史证明的适配器 SHA `791481b3e12ce6f64bb77c75acaff7df8000d800081589194ccde5d5ad3f2a92` 与 v4 适配器 SHA `71cfe0f2e4d9a70beb8e4255df11a1bd0215f15ea85a49039c809ed1d87a133f` 不同，不能覆盖或复用。已创建独立 `sf013-adapter-v5-20261019`，自检状态名从证据 ID 派生且限定在资格目录；2/2 单测通过。当前 v5 适配器 SHA `472bbddbb7343c124c84730b0c8c2527fde4d158dc47335bdb40fc74b1389227`，Worker2/Worker3 各一次真实牺牲进程 Watchdog 自检 PASS（旧状态未修改），回执 SHA 分别为 `ab4755cef9c51f9156698d412bca7d6b074e64d55bfc619cbe2d29e11c9ec737` 和 `51144edba87d3faa6b4f209e9184ee1a994beef947c92aaa1c883deaf47a4ffb`。独立 `final-case-formal-entry-v5-20261019` 显式锁定该适配器来源，15/15 静态测试通过，Master/Worker2/Worker3 源码 SHA 一致；**v5 尚无 1800 秒全长无故障测试，也没有任何真实业务机制资格或正式 Case**。51 当前仅完成只读容量/导入路径检查，练习和正式场次状态未修改，未发布新题。**在这些门禁完成前，10 小时内交付 5 个真实可评分 Case 不可承诺。**
