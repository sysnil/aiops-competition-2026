# SF-013～SF-017 最终生产资格门禁报告

**最终决策：`PRODUCTION_BLOCKED`；`READY_FOR_HUMAN_PRODUCTION_APPROVAL = false`。** 本轮真实业务故障注入 0 次、正式 Attempt 0 个、生产主机重启 0 次。五题冻结版本的无害入口验证完成，另有一次真正连续 1800 秒的 SF-017 代表性无故障采集及异机归档 PASS。真实适配器、主机重启和累计容量仍是强制门禁，不能从无故障 PASS 推导生产资格。

## 1. 候选版本与历史保护

最终演练身份：`SF-017-ATTEMPT-MARKERTEST-867F6CAE`；UTC 窗口 **2026-10-19 12:14:39.789104～12:44:39.789104**。冻结 Manifest SHA-256 **`f8722baeaee2fd9f416eee0403dc7db60c83583664dbaaa4a8326e9a99bb92d0`**；`freeze-manifest.json` SHA-256 **`2b745c3d71afc04d02861b68840b6bb4e2bb09c741d7f02e2da0cd27c5ebdf30`**，锁定 40 份 Controller、Guard、Timer、Adapter、HTTP Workload、合同、实时授权、撤销、采集器及工作负载源码/配置。全窗口结束时再次逐项核验冻结 SHA，未修改冻结版源码。原件：`/root/ops-competition-2026/final-case-formal-entry-v3-20261019/rehearsal/SF-017-ATTEMPT-MARKERTEST-867F6CAE/`。

源码代表 SHA：`control_contract.py` `29d41121dce32c983a18308a0eae04eeac49c96d1e163d39fe921583f47b7578`；`formal_controller.py` `216a605577f2c502302769efa3fc7f6539c6d36727bcf5322fd75779caae041b`；`formal_guard.py` `e6fb8e6d10b9e3469035d0cf8902e8832d70412bf61a4614ab4acc25a0ab2d89`；`worker_fault_entry.py` `f9307b8d775acdee191f931b2453c83c7335ecd9a3a74a9b0b84dc8657891cd8`；`http_workload.py` `53152704a18415a9406e5018c7b0df2dd8cfc15538116350ba1c1edbf08562b0`；共享 Ed25519 协议 `auth_protocol.py` `2d2ac188d0142791e386a687afc4b02e1f0362f65f1e6443377baf7f7d21bb07`。其余逐文件 SHA 见冻结 Manifest。Master、Worker2、Worker3 的 v3 执行源码经 rsync `--checksum --dry-run` 比对一致。

五题新 Manifest/Timer 命令只允许 v3 `worker_fault_entry.py` 或 Master `http_workload.py`；新 Controller 不引用 SF-007/SF-011/SF-012 历史本地租约入口。旧源码和历史 Attempt 未删改。root 管理员仍可读取历史脚本，不能把“新执行链拒绝旧入口”误写成“系统上不存在旧脚本”。

最早的 v1 无故障试验因 Worker Manifest 重新序列化导致 SHA 不一致，在 T0 前 fail closed；Worker3 确认无 Timer 并按精确 Action 身份写入撤销记录。v2 完成六段采集，但 HTTP systemd 单元从预部署开始累计 1900 秒而超时，缺少状态机结果 Manifest，因此保持 FAILED。两次失败的原始冻结文件、日志和采集均保留，未拼接进 v3。v3 将单元上限改为 2400 秒，且成功终态只撤销故障许可、不提前终止正常业务；独立回归与实际 1800 秒结果验证了该修复。

## 2. 五题逐项资格

最新候选拓扑：Frontend 两副本 Ready/零重启，Worker2 Pod UID `d4bb49b4-6fb8-4a07-aaf6-e2caff5df974`、Worker1 正常副本 UID `6305d17a-27f0-4dbf-8fc7-7238acb3be91`；Recommendation 两副本均在 Worker3，UID `8b7c229b-cea1-440f-bc88-e9c1f246ef9e` 与 `3886c989-d7a1-44e9-8b44-64c536db1897`，EndpointSlice 两端点 Ready。以上是只读**当前**身份；未来任何获批注入必须再次刷新 PID/start ticks、容器 ID 和 cgroup，不得只凭本报告 UID 执行。

| Case | 冻结 v3 无害链路 | 真实机制资格 | 1800 秒联合采集 | 重启安全 | 生产结论 |
|---|---|---|---|---|---|
| SF-013：Worker2 Frontend PodMemory 32 MiB/300 s | **PASS**，Run `SF-013-ATTEMPT-MARKERTEST-420CAB1E`，独立 Manifest SHA `7a8fd5198ca1e43c042e6412909d5d37541eb7a599eecbac5343b9f50c0fbec4`；Timer/实时授权/Guard 清理通过 | **PENDING**；Worker2 非业务 32 MiB 牺牲进程与 Watchdog PASS，未进入 Frontend Pod cgroup | 未为该题单独运行；SF-017 代表性采集不替代该题资格 | 未验证 | BLOCKED |
| SF-014：Worker3 双副本 Recommendation ServicePause 30 s | **PASS**，Run `SF-014-ATTEMPT-MARKERTEST-2E663C81`，SHA `d70ad73fc83ea8b5974e88fb74d4d62243ea06efbfc5c016be9dcc6c89d83b87`；实时授权及清理通过 | **PENDING**；Worker3 两个非业务牺牲进程同时暂停并由 Watchdog 恢复 PASS，未暂停 Recommendation | 未为该题单独运行 | 未验证 | BLOCKED |
| SF-015：Master HTTP ReqDelay 250 ms/60 s | **PASS**，Run `SF-015-ATTEMPT-MARKERTEST-5CA29F02`，SHA `c5aef8d3f78ecd235aca75e704e9746c45397f5285293bffad3e18afbe679121`；实际状态机普通请求，无故障头 | **PENDING**；回环 HTTP 证明 Image/Cart 不带头、首页只在授权时带 `250`、撤销后下一请求不带头；未向真实 Frontend 发送故障头 | SF-017 代表性窗口覆盖相同 Master 请求路径，但不证明真实延迟效果 | 未验证 | BLOCKED |
| SF-016：Worker2 Frontend PodMemory 32 MiB/300 s | **PASS**，Run `SF-016-ATTEMPT-MARKERTEST-626899BD`，SHA `15e753daedbc75d911b42c544b57c17407f3b90f75dfcea1affc188176f9a57c`；独立 Action 与 SF-013 隔离 | **PENDING**；相同机制的牺牲进程测试不能自动产生该题资格回执 | 未为该题单独运行 | 未验证 | BLOCKED |
| SF-017：HTTP + Worker3 ServicePause 非重叠 | **PASS**，完整冻结 Manifest SHA 如上；HTTP 子动作零故障头，Worker3 Marker 一次性授权并撤销；先前同入口的 Controller 退出负向测试确认 Guard 取消兄弟 Action，等过 T0 后无迟到 Marker | **PENDING**；两个真实机制均须先各自获批资格，再联合验证 | **PASS**：完整 1800 秒无故障，仅该题代表性路径 | 未验证 | BLOCKED |

五题无害链路的 Controller/Guard 均有独立终态；新源码单测 11/11、最后采集编排单测 4/4、共享授权回归 13/13，Python 编译通过。受控网络断联与 Master 签发后撤销的实际分布式短窗口仍不能用合成结果宣称绝对零竞态；签名 grant 最长 1.2 秒的残余有效期沿用前版公开风险边界。

## 3. 完整无故障采集与归档

v3 全窗口状态 **`FULL_1800_NOFAULT_ARCHIVE_PASS`**。六段各 300 秒，真实 Prometheus Metrics、OpenSearch Logs、Jaeger OpenSearch Trace 依次完成首采、不可变 +60 补齐、+120 最终快照及 +240 晚到审计。总计首采 **54,177 Traces、353,181 Spans、192,620 Logs**，+120 最终快照 **353,186 Spans**，六段 +120 至 +240 新增 Span 均为 **0**；每段有 11 类指标文件。普通固定对照 **7,200/7,200** 条请求均在最终存储匹配到 Proxy Server Span。状态机结果 `LOAD_PASS`，固定对照 `CONTROL_STREAMS_COMPLETE`，Observer `OBSERVER_COMPLETE`，125 个健康样本，Collector Spans/Logs/Metrics refused 计数前后均为 0；Worker3 独立牺牲进程 Watchdog PASS。质量报告 `/data/stage5-v3/ops-competition-2026/rehearsals/SF-017-ATTEMPT-MARKERTEST-867F6CAE-full1800/quality-audit.json` SHA **`6d367785a058acb8eb35bcdfb35ffbb82158d7834b6a6254b0356bdd0d6215ad`**。

Master 原始与控制证据保存在上述 `rehearsals/...-full1800/`，没有写入正式 Case 数据目录。Worker2 系统盘归档路径 `/root/ops-competition-2026/rehearsal-archives/SF-017-ATTEMPT-MARKERTEST-867F6CAE-full1800/`，**3,692 文件、4,579,644,759 B**；在 Worker2 独立执行全文件 SHA 检查和 `schedule.json`、质量报告、首段采集 Manifest 读回，结果 `NOFAULT_ARCHIVE_SHA_AND_READBACK_PASS`。归档 SHA 清单自身为 `7b565e5880cdd873d6ed3a5751f25aec368a7f4fd5b8b96d6e788dcb33a9aa72`；回执 SHA **`89c3206981a77371328243d64b568fffc95cf3a3014bc49de80f8135fe37f5f6`**。合成小文件传输测试独立标为 `SYNTHETIC_ARCHIVE_TRANSPORT_PASS`，没有混入真实采集统计。

本轮末次五机只读检查：4/4 Nodes Ready，Calico/BGP 四节点均 3/3（共 12/12）Established，OpenSearch green，Prometheus healthy；Master/Worker1/2/3/VM1 均无活动竞赛 Timer/Service 或 `netem`/`tbf` 残留。此快照是无故障健康结果，不是未来真实注入前的永久 Safety PASS。

## 4. 存储与重启仍阻断生产

末次各文件系统可用字节：Master `/` 45,201,186,816、`/data/stage5-v3` 42,222,333,952；Worker1 `/` 36,648,353,792、`/data` 16,864,063,488；Worker2 `/` 49,249,390,592、`/data` 17,289,592,832；Worker3 `/` 35,264,606,208、`/data` **10,751,463,424**；VM1 `/` 87,392,063,488。单题当前预算 PASS 不代表五题累计安全。按 Worker 数据盘每题 2 GB 背景敏感性模型，Worker3 五题后预计仅余 **751,463,424 B**，低于 5 GB 底线 **4,248,536,576 B**；建议云平台经审批增加 20 GiB，10 GB 仅作为经额外峰值复核后的较小选项。该盘是 ext4 `/dev/sdb1`、UUID `b40835b4-f109-47d5-8e17-9e56a54e2865`，同时承载运行中 Collector 的本地 PV；本轮没有扩容、卸载、格式化或迁移。是否可以不停机取决于云平台在线扩盘/分区重读能力，不能预先保证。详见 `final-case-qualification-prep-20261019/design/worker3-disk-expansion-plan.md`。

Master 数据盘按五题各 7,350,324,113 B 保守预算，五题后约余 **5,470,713,387 B**，仅比底线多 **470,713,387 B**，不足以吸收失败尝试或额外导出临时文件；也须经审批增加余量或制定受保护 rehearsal 数据的迁移/读回方案。任何容量不足时动态门禁应阻断下一题，不能删除 v1/v2 原始失败记录或降低 5 GB 底线。

未找到可信隔离 VM 的现成 systemd/SSH/boot ID 重启测试环境；本轮没有重启 Master/Worker，也不以 Guard 重启或容器模拟代替。`final-case-qualification-prep-20261019/design/host-restart-maintenance-plan.md` 提供隔离 VM 优先、否则由组委会批准非赛时维护窗口的测试方案。Worker3 重启会涉及 Collector 本地 PV，须单独安排影响与恢复窗口。

## 5. 审批与最终判定

需要组委会分别批准：**(1)** Worker3 云盘扩容及 Master 余量处置；**(2)** SF-013/014/015/016 各机制在精确当前目标上的独立真实故障资格试验，并在全部单机制通过后批准 SF-017 联合资格试验；**(3)** 可信隔离 VM 资源或生产主机维护重启窗口；**(4)** 上述门禁和逐题恢复/采集资格全部通过后的正式 Case 生产许可。真实故障资格边界与停止条件见 `final-case-qualification-prep-20261019/design/real-mechanism-qualification-plan.md`。

本地小型证据索引 `final-case-qualification-prep-20261019/results/last-gate-evidence-index.json` SHA **`78cd7a9f6ad0a738d9451383b481f8ee7a37a4484da8f8762182d260b2130099`**；Master 保存相同报告与冻结原件。任何资格仍为 PENDING 的 Case 均不得取得 `QUALIFIED_FOR_THIS_VERSION` 回执或正式故障许可。**本轮停止于 `PRODUCTION_BLOCKED`，不申请 `READY_FOR_HUMAN_PRODUCTION_APPROVAL`，不启动 SF-013～SF-017 正式生产。**
