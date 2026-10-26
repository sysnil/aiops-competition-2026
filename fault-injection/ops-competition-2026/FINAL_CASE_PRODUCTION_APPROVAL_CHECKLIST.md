# SF-013～SF-017 生产许可前安全门禁 Checklist

**决策：`PRODUCTION_BLOCKED`。** 本阶段真实业务故障 0 次、新正式 Attempt 0 个、生产主机重启 0 次；没有冻结计划或批准生产。继续沿用上一轮已通过的 Worker2 v4 授权证据，不把本轮新审计或 Worker3 Marker 当成五题正式故障资格。

权威逐项证据：`/root/ops-competition-2026/final-case-production-approval-20261019/results/approval-evidence-manifest.json`，SHA-256 **`2bf879353d073adcce92f12e4d8cfd91103939345211a86bdd6442f995ae6879`**。该清单记录 18 份本轮代码、3 份 DRAFT 合同和 15 份证据的完整 SHA；不含私钥或 GT。

## 强制门禁

| 门禁 | 判定 | 实际依据 |
|---|---|---|
| Worker2 SF-013 v4 实时授权 | 已通过无害验证 | 保留上一轮 19/19 Marker、11/11 单测、1800 秒无故障演练及 Watchdog 牺牲进程自测；本轮未重跑这些结果作为新生产许可。v4 版本 Manifest SHA `35c1cf2e4668970194b7d9b88580560cefec4e47e33ae4350a7c8a464abe016f`。 |
| Worker3 受限 SSH/Marker 控制链 | **Marker-only PASS** | 独立来源 `192.168.53.55`、强制命令、固定 Master 主机/签名公钥、私钥 0600。Worker3 新增场景 **10/10 PASS**：正常、拒绝、撤销、双向断联、Guard 退出、两个 Attempt 及修订后的最终源码正常/拒绝回归；所有拒绝场景原定时间后无 Marker、无 pending Timer/Service。受限 SSH 不能执行 `id`。`results/worker3-marker-assessment.json` SHA `5ca7c9cab48b81f1a5f4096fab1814efd4b3d24f6f2d7d4b8f2c444055a474de`。 |
| Worker3 凭据与跨主机代码 | PASS | Master/Worker3 五份共享代码 SHA、签名公钥一致；Master 仅有两条分别限定 Worker2/Worker3 来源 IP 的受限 key。Worker3 原安装回执把状态文字误写为 `WORKER2_...`，原件保留；新增只读实际文件/权限核验 PASS。Worker3 SSH 规则可按备份精确回滚且保留 Worker2 规则，dry-check PASS，**未执行回滚**。 |
| Master/Worker 真正重启生命周期 | **未通过** | 未找到可独立启动 systemd 的 VM/rootfs：Master 有 `runc`，但它单独不能复现宿主机 PID 1、transient Timer、cgroup、SSH 和网络恢复；Mac 也没有已配置的 VM 工具。本轮未重启生产节点。此前 boot ID 变化与 Guard 重启的 Marker 结果不能冒充主机重启。维护测试方案：`/root/ops-competition-2026/final-case-production-approval-20261019/design/restart-maintenance-window-plan.md`，须组委会另行批准。 |
| SF-013～SF-017 全部正式入口绑定实时授权 | **未通过** | `results/five-case-entrypoint-audit.json` 和合成租约谓词 `results/legacy-lease-bypass-probe.json` 证实历史 Service/POD/HTTP 入口仍能只凭本地 4 秒租约通过判断，未对新五题创建可信新 Run Manifest。SF-015 HTTP 机制是 Master 负载请求头，不存在 Worker 故障 Timer；不得把 Worker3/Worker2 Marker 验证错误套用到 HTTP。 |
| 签发/撤销竞态 | **未通过** | 临时目录与一次性测试密钥复现：撤销恰在 Master 查询 Controller/Guard 期间写入时，现有代码仍可能签发；已签名 grant 在模拟 Master 立即失联后约 90 ms 仍有效，1.21 s 后被拒；Worker 在效果前收到本地撤销则拒绝回调。详见 `results/grant-race-analysis.json` SHA `711360b18ad01c78a30b397513ed041d65a3e762bfaf7b0b36b7f608d51e39a4`。 |
| 五题累计空间敏感性 | **未通过** | 当前各盘实际可用均 >5 GB，但两份不一致的 DRAFT 归档分配在后续题目均可能越线；数值见下表。不能删除历史归档或降低十进制 5 GB 底线。 |
| 当前靶场与历史数据 | PASS（只读快照） | 4 Nodes Ready、13 个关键 Pod Ready、Calico 4 节点各 3/3 BGP Established、Prometheus healthy、OpenSearch green、Collector grpc/http refused 0；五机无活动竞赛 Service/Timer 或 `netem`/`tbf` 残留。历史十题 10/10 Public Manifest、60/60 文件 SHA 匹配，旧 SF-013 ATTEMPT-01/02 未覆盖。 |

## 五题实际执行资格

当前拓扑：Frontend 两副本分别在 Worker1/Worker2，frontend-proxy Pod 在 Worker2，Recommendation 两副本均在 Worker3。以下“作用位置”来自当前 Kubernetes Pod/EndpointSlice 与历史执行代码，不是从服务名推断；正式执行前仍须刷新 UID、容器和进程身份。

| Case | 草案机制与作用位置 | 授权/执行入口审计 | 注入资格 |
|---|---|---|---|
| SF-013 | 当前权威回退方案为 Worker2 Frontend PodMemory32MiB/300s；PodCPUStress 仍未资格。 | v4 Worker2 控制链和 Marker 已通过；v4 适配器源 SHA 改变，真实机制资格不能自动沿用 v3。早期预注册写 60s，后续目标方案为 300s，正式窗口须统一。 | **BLOCKED**：重启、竞态及新机制资格未通过。 |
| SF-014 | Worker3 上两副本 Recommendation ServicePause30。 | Worker3 新增受限授权 Marker 通过；旧 `sf007-real-adapter-v7` 的 `fault_worker.fire` 仍仅看本地租约，新 SF-014 正式 Timer/Guard/适配器未绑定到该控制链。 | **BLOCKED**：旧入口可绕过、无新机制资格。 |
| SF-015 | Master 业务负载给普通首页请求加 HTTP ReqDelay250ms/60s 标记；被调用的 frontend-proxy Pod 当前在 Worker2。 | 历史 `sf011-http-adapter-v5` `run_registry` 明确 `no_worker_timer=True`；其 Master 控制器/负载仍使用本地 4 秒 HTTP lease，Worker 实时授权对此 **NOT_APPLICABLE**。 | **BLOCKED**：Master 本地请求头效果入口尚无新的实时撤销/授权合同。 |
| SF-016 | Worker2 Frontend 单 PodMemory32MiB/300s，Worker1 正常副本。 | Worker2 v4 授权协议已验证，但 v4 `run_registry` 明确锁定 SF-013；旧 `sf012-pod300-adapter-v4` 仍用本地租约。未注册 SF-016 专属 Controller/Timer/Guard 和 Manifest。 | **BLOCKED**：Case 专属绑定/资格缺失。 |
| SF-017 | 两个非重叠效果：Master 本地 HTTP ReqDelay 与 Worker3 ServicePause30。 | 必须分别满足 SF-015 的 Master HTTP 门禁与 SF-014 的 Worker3 门禁；一个 Marker 通过不能代替另一条入口。旧组合无新双 Action 隔离合同。 | **BLOCKED**：两条入口均未完成正式绑定。 |

因此新增五题正式可注入数为 **0/5**。旧入口本身未改写，历史 READY Case 亦未被追认；在未来新计划中必须显式拒绝引用这些本地租约入口，不能用同名启发式或 Marker 结果代替真实适配器。

## 当前容量与逐题预测

2026-10-19 07:35 UTC 五主机新快照可用空间（十进制 GB）：Master 根盘/数据盘 **45.23/51.31**；Worker1 **36.68/18.00**；Worker2 **53.87/17.75**；Worker3 **35.28/11.11**；VM1 根盘 **87.39**。使用历史单题 7,350,324,113 B 峰值/归档承诺、每机一次 2 GB 后台保留、每题 Worker 数据盘额外 2 GB 的敏感性情形，得到下列**非生产许可**结果：

| 累计完成 | 较新的 Worker 系统盘 DRAFT 归档目标 | 该版越线 | 较早预注册 DRAFT 归档目标 | 该版越线 |
|---|---|---|---|---|
| SF-013 | Worker1 `/` | 无 | Worker1 `/data` | 无 |
| SF-014 | Worker2 `/` | 无 | Worker2 `/` | 无 |
| SF-015 | Worker3 `/` | 无 | Worker2 `/` | Worker1 `/data` 仅 4.65 GB，缺 **0.35 GB** |
| SF-016 | Worker1 `/` | Worker3 `/data` 仅 3.11 GB，缺 **1.89 GB** | Worker3 `/` | Worker1 `/data` 缺 **2.35 GB**；Worker3 `/data` 缺 **1.89 GB** |
| SF-017 | Worker2 `/` | Worker3 `/data` 仅 1.11 GB，缺 **3.89 GB** | Worker2 `/` | Worker1 `/data` 缺 **4.35 GB**；Worker3 `/data` 缺 **3.89 GB** |

按较新系统盘草案完成五题，Worker3 数据盘至少还需 **3,887,003,136 B** 可用空间才能在上述敏感性情形下守住 5 GB；这是数学下限，不含额外突发余量。若保留较早预注册分配，Worker1 数据盘还至少需 **4,353,266,065 B**。建议先经批准确定唯一归档分配，再提供超过这些下限的实际安全余量或经授权迁移；不得自动删除旧 Case、失败证据或通过降低门槛求 PASS。两版投影及精确逐盘数据见 `results/two-storage-allocations-final.json`，SHA `c3e59a74a96a0f41d431d2a6290acf6ecc82824199ecb9609550522e3e156117`。

## 签发后竞态与改进选择

现有 Worker2/Worker3 grant 最长 **1.2 秒**，正常 Marker 实测签发到效果约 90 ms。取消在签发前若已记录，Master 会拒绝；但合成测试证明撤销落在 Master 单元查询与签名之间时，当前无再次撤销检查，仍可能签发。签发后 Master 断联不会撤销已在 Worker 内存的有效签名；Worker 只会在本地撤销已送达或签名过期时拒绝。效果回调失败时一次性 Action 已消费，不能凭旧授权重试；独立 Watchdog 牺牲进程自测已通过，但新正式故障仍需资格验证。

优先修正候选：Master 的撤销与签发共用 Attempt 级 `decision.lock`，在查询 Controller/Guard 后、签名/消费前再核查撤销；形成新代码 SHA 并重新做“撤销恰在检查中”负向测试。对于**签发之后**的不可消除分布式窗口，组委会可选择接受量化上限并保留效果前本地撤销检查，或设计第二次短确认/提交握手；后一方案会改变协议，必须独立审核和全套回归。不得擅自延长有效期或把旧租约当第二确认。本轮仅审查并提供方案，未改动已验证的 v4 授权代码。

## 新代码、合同与审批下一步

本轮 Worker3 扩展主要 SHA：`auth_protocol.py` `23841f92...58b8de0`、`master_authorizer.py` `4815c1cc...8e646c9`、`worker_auth.py` `eb3126ef...9fe0ab`、最终 `marker_worker.py` `d8e44658...9d4ce2`。Worker3 受限 SSH 凭据安装回执 SHA `ad04229de1a2e4c779387353eb49646d7ac81603f7449b6b3734247b70a16298`；原 Worker2 规则保持。完整代码/合同/证据 SHA 以开头机器 Manifest 为准。两份冲突的归档 DRAFT SHA：较新系统盘方案 `e3db2c3a1be584f1d8f79777866cd83455e666e53273c9a96c61e0084b152f49`，较早预注册方案 `fbf21de16253d5e205b0768946e71f289750c3cecff8a66096956b21b5c5ea2d`。维护窗口方案 SHA `faf819d6ad4b93e33d22db7603c778b1b250cd4627c6f05975e371b907714326`。

下一步顺序：先经组委会提供可信隔离 VM 或批准无害维护重启测试；修复并复测签发/撤销竞态；对 SF-014/016/017 的 Worker Effect 入口及 SF-015/017 的 Master HTTP 入口形成专属、不可绕过的冻结前代码和无害联合证据；确认唯一归档策略及扩容/迁移，逐题刷新身份、空间和健康门禁。完成这些**强制门禁**后才可重新评估是否提出 `READY_FOR_HUMAN_PRODUCTION_APPROVAL`，该状态本身仍需组委会单独批准。本阶段到此停止，不执行真实故障或正式新 Case。
