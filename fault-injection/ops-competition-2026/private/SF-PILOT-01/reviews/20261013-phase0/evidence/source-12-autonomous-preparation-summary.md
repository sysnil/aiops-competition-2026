# Stage5-v3 Autonomous Preparation: 最终报告

**结论：`PREPARATION_BLOCKED_NO_TWO_CLEAN_PASS`。** 三个预先版本化的无故障候选共保留五段 10+5+10 分钟原始窗口，但没有同版本两次 `CLEAN_QUALIFICATION_PASS`。因此四层故障机制的八次预期注入 **0 次执行**，Injection/Observability/Business Impact 的故障资格判定均为 `NOT_RUN`，不建议进入 Stage5-v3 正式 Pilot 冻结审核。本轮没有正式 DEV/TEST Case、真实 RCA 预测或真实 GT 解封，也未访问 Stage5-v2 TEST。

全部长期数据、代码与结果位于 `/root/stage5-v3-autonomous-preparation/`；Stage5-v1/v2 冻结目录和历史结果保持只读。C1/C2/C3 的失败窗口均保留原始 JSON、探针逐点记录、路由证据、运行状态和捕获清单，未压缩或删改。

| 阶段 | 状态 | 真实结果 |
| --- | --- | --- |
| A HTTP 503 | **PASS** | 修复前 2/3/1 次代理 `503 UC`；修复后 3 分钟短窗和四段完整窗口均 0 次。 |
| B 负载与对照 | **PARTIAL** | Recommendation 双副本和目标调用边覆盖恢复；Image/Cart p95 对照未在两个种子中持续满足 1.25。 |
| C Clean Gate | **BLOCKED** | C1 有客户端错误与 +60 s 捕获竞态；C2/C3 两种子均违反 0.25-core Worker3 增量上限，部分对照还越过 1.25。 |
| D 四层故障 | **NOT_RUN** | 全局两段 Clean PASS 前置条件不存在；未冻结八次故障注册，也未注入。 |
| E 机制资格 | **NOT_RUN** | 无真实故障直接证据、Business Impact 或恢复曲线可作新资格结论。 |
| F DRAFT 执行器 | **PASS（合成）** | 40/40 离线测试；56 个合成候选、394 条哈希链事件，DEV/TEST 分别完成合成评分。 |

## A. 代理错误与修复

失败请求的原始 Envoy access log 均为 `503 UC upstream_reset_before_response_started{connection_termination}`，上游是 Frontend Service VIP。示例 C1 Trace ID `46c4632413121d85213ade464457c266` 仅有 frontend-proxy ingress/egress 两个 Span，没有 Frontend 或 ProductCatalog Server Span；不能据此将错误归给 ProductCatalog 或某个 Frontend Pod。失败请求涵盖状态机和固定普通业务流。两个 Frontend Pod 持续 Ready、无重启，EndpointSlice 正常。Frontend 响应声明 5 秒 keep-alive，原代理没有配置较短的上游 HTTP 空闲超时；“旧连接复用与服务端关闭竞态”是证据支持的解释，**不是单请求已证明的 Pod 根因**。后续 FIN 抓包在失败请求之后启动，记录 512 个 Frontend 发起的 FIN 和相应代理 FIN、0 RST，因此不被用来反推该失败请求的具体后端。

修复只修改 frontend-proxy 的 Frontend 上游连接策略：`idle_timeout: 2s`、`max_requests_per_connection: 10`，没有添加 retry 或忽略 503。修复模板先在现有 Envoy 中 `--mode validate` 通过，原模板与 Deployment 均备份；新 Pod 因滚动更新一度落到 Worker3，使 Image 对照不再独立，随后通过受审计的 nodeSelector 恢复到原 Worker2 位置，并重新证明 7/7 当前 Pod UID、Calico 路由和真实业务包。整个验证期有独立定时回滚；五个后修复窗口原始日志均为 0 次同型 503 后保留修复配置、关闭 timer。最终 Deployment revision 3、Proxy Pod Ready 在 Worker2，原 revision 1 与配置备份可回滚。[Envoy 上游超时说明](https://www.envoyproxy.io/docs/envoy/latest/faq/configuration/timeouts)与[连接请求数选项](https://www.envoyproxy.io/docs/envoy/latest/api-v3/config/core/v3/protocol.proto.html)用于校验配置语义。

| 原始代理诊断窗口 | `503 UC` |
| --- | ---: |
| 历史 Phase1 V1 / V2 | 2 / 3 |
| C1 新窗口 | 1 |
| 修复后短窗（1,711 次状态机请求） | 0 |
| C2-S1 / C2-S2 / C3-S1 / C3-S2，各 25 分钟 | 0 / 0 / 0 / 0 |

原始证据：`snapshots/*proxy-diagnostics/`、`snapshots/c1-proxy-full-diagnostics/`、`snapshots/proxy-frontend-finrst.pcap`、`snapshots/proxy-upstream-repair-c2b-preparation/`、`runtime/proxy-c2b-apply/`、`runtime/proxy-c2b-worker2-pin/`、`runtime/proxy-repair-finalization.json`。[详细诊断](proxy-diagnosis.md)保留了未证明之处。

## B/C. 五段无故障资格窗口

状态机操作和固定对照均请求已验证的 OpenTelemetry Demo 3.0.0 真实接口。实际 Trace 中 Recommendation Server Span 的直接父调用来自 **Frontend**，而非假设中的 ProductCatalog→Recommendation 直接调用。Frontend 两个 Pod 各自复用到一个 Recommendation Pod 的连接；代理上游定期轮换后，Frontend 两副本流量更均衡，Recommendation 两 Pod 在 C2/C3 的每个 Before 分钟均达到 30 条普通业务 Trace ID。C3 移除了未被选为正常对照、却额外给 Worker3 增负的 2 rps 固定 Product Detail 流；状态机中的真实商品浏览、推荐与结账未删。

| 窗口 / seed | 状态机速率 | 弱 Recommendation Pod Before 最低/min | Checkout→Cart Before 最低/min | Image / Cart 最大 p95 比值 | Worker3 新增 CPU | Clean 判定 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| C1-S1 / 851201 | 1.8/s | 16 | 50 | 1.423 / 1.826 | 未审计 | BLOCKED：1 次状态机 503；Before 首读 +61.1 s，抢在 +60 s 补齐之后；覆盖和对照不合格。 |
| C2-S1 / 851211 | 1.5/s | 32 | 40 | 1.260 / 1.262 | +0.353 core | BLOCKED |
| C2-S2 / 851212 | 1.5/s | 38 | 39 | 1.202 / 1.089 | +0.343 core | BLOCKED |
| C3-S1 / 851221 | 1.4/s | 39 | 37 | 1.325 / 1.086 | +0.294 core | BLOCKED |
| C3-S2 / 851222 | 1.4/s | 36 | 32 | 1.076 / 1.691 | +0.339 core | BLOCKED |

五段窗口均使用独立种子，C2 两段之间、C3 两段之间的计划、状态机、固定流生成器 SHA 分别完全一致；没有拼接不同版本的成功片段。C3 在任何窗口前固定了 24 项输入/代码 SHA（见 `design/clean-c3-candidate-manifest.json`），并限制了两组准确 ID/种子配对。三个候选分别是本轮上限，不再自动提出第四版或降低阈值。

当前公开路径证明涵盖 Checkout Worker3→Cart Worker2、Image Worker2 本地、Frontend Worker1/2→Cart Worker2 等 7/7 调用边。每段无故障窗口的 W3→W2 探针均为 Before 120/120、During 60/60、After 120/120 有效，TIMEOUT/SCHEDULE_MISS/控制连接失败均为 0；超时绝未补为零吞吐。C2/C3 的 Prometheus、OpenSearch Logs、Jaeger OpenSearch 原始 Traces 和 Kubernetes 元数据均按完整窗口采集；首次读取从窗后 +20 s 提前至 +5 s，固定 +60 s 不可变 Trace 补齐继续工作，C2-S1 Before 实际新增 80 条延迟 Span。C1 的 Before 补齐因首读在 +61.1 s 才完成而失败，**不能倒填为采集 PASS**。这个 +5 s 修复应在未来 v3 协议冻结前显式审核，当前 DRAFT 仍写 +20 s。

C2/C3 的完整 public leak audit 连同延迟补齐页分别扫描 1,248 / 1,286 / 1,135 / 1,139 个 JSON，私有字段命中 0；C1 只有首次 capture 的 877 文件审计，因补齐失败不宣称全链路审计通过。正常对照的实际成功请求与边 Span 都达到每分钟至少 30，但严格“每个 During/After 分钟 p95 比值 ≤1.25”在 C2 的 30 分钟桶中分别被 Image 1 次、Cart 2 次越过；C3 再出现 Image 1 次、Cart 2 次越过。所有越界均按原规则保留。噪声分布见 `results/control-noise-c2-only.json`、`results/control-noise-c3-only.json`，这些是描述统计，**没有修改 Admission 或 Clean Gate**。

Worker3 的新增 CPU 由 Prometheus 独立运行前 2 分钟参考与 25 分钟采集窗口的节点 CPU 中位数比较，参考查询属于事后只读的**资源开销分析**，不冒充 Before/During/After 实时捕获。C3 两种子 +0.294/+0.339 core 均超过预登记的 0.25；ProductCatalog 逐 Pod 额外 CPU 从 C2-S1 约 +0.076 降到 C3-S1 +0.059 core，Collector 同期约 +0.037 降到 +0.029，但这些可见 Pod 差额不足以解释全部节点增量，不能断言剩余 CPU 均由状态机造成。需补一轮节点宿主进程、探针和遥测开销的独立归因。资源上限在本轮未改。

图表均使用真实采集数据：[C1 曲线](clean-c1-curves.html)、[C2-S1 曲线](clean-c2-s1-curves.html)、[C2-S2 曲线](clean-c2-s2-curves.html)、[C3-S1 曲线](clean-c3-s1-curves.html)、[C3-S2 曲线](clean-c3-s2-curves.html)、[C3-S1 CPU](c3-s1-cpu-curves.html)、[C3-S2 CPU](c3-s2-cpu-curves.html)、[C3-S1 探针](c3-s1-probe-curves.html)、[C3-S2 探针](c3-s2-probe-curves.html)。失败 503 的原始 Trace 和 Envoy 日志在上述 `snapshots/` 目录；无真实故障期曲线可提供。

## D/E. 机制资格未运行

| 根因层级 | 待验证机制 | 两个故障种子 | 直接公开根因证据 | Injection / Observability / Impact | 资格建议 |
| --- | --- | --- | --- | --- | --- |
| SERVICE | Recommendation 服务范围 process stall | NOT_RUN | NOT_EVALUATED | NOT_RUN / NOT_RUN / NOT_RUN | 保持 DRAFT `CONDITIONAL` |
| POD | Recommendation 单 Pod memory pressure | NOT_RUN | NOT_EVALUATED | NOT_RUN / NOT_RUN / NOT_RUN | 保持 DRAFT `CONDITIONAL` |
| NODE | Worker1 CPU contention | NOT_RUN | NOT_EVALUATED | NOT_RUN / NOT_RUN / NOT_RUN | 保持 DRAFT `CONDITIONAL` |
| NETWORK_PATH | W3→W2 10% 有界 loss | NOT_RUN | NOT_EVALUATED | NOT_RUN / NOT_RUN / NOT_RUN | 保持 DRAFT `CONDITIONAL` |

无故障窗口不能证明注入是否生效、公开直接根因信号是否足够或恢复时长是否合格；不据此升级任何机制到 READY。其余未验证机制仍按现有 Stage5-v3 DRAFT 的 CONDITIONAL/BLOCKED 保留，也未执行 Stage5-v2 TEST、Stage5-v3 DEV/TEST 或 RCA 正式预测。

## F. DRAFT 协议执行器

独立 `scripts/draft_executor.py` 与 `draft_executor_cli.py` 默认且仅允许 synthetic/dry-run。固定 32 Primary + 24 Reserve 候选，单向尝试/分割状态转换逐条写入不可覆盖事件、输入 SHA-256 与前一事件哈希；重载时重新核对链和输入。公开 Variant A/B1/B2 的候选与非网络证据不变量、预测 ranking/root layer/entity/evidence references、工程失败终态、独立 DEV/TEST GT 屏障、可信 Admission、Primary 优先与 Reserve 顺序、SHORTFALL/SPLIT_ABORTED 均有合成测试。主指标、Prediction Coverage、Coverage-adjusted Exact@1、根层分组、NetworkPath 假阳性与 A→B1 / B1→B2 配对分母由同一冻结选择表计算。`NO_PREDICTION` / `INFERENCE_FAILURE` 保留在适用分母，候选空间缺失与预登记方法前置阻塞单列。

Master 的 compile 与 **40/40** 单元测试通过；完整合成 DEV+TEST 为 **56 候选、394 事件**，两个 split 均 `SCORES_FROZEN`，事件链头 `5735baabf19941f483de5bd4dc99b549624c5b9c567c42a061b582d4f8cc6cd9`。合成 GT 不是真实 TEST GT。无损归档工具通过完整哈希验证、损坏阻断和恢复测试，**本轮真实原始窗口没有执行清理/压缩**。生产角色挂载/权限隔离、真实方法 runner、真实公共转换与正式 GT 解封凭据仍需实现和审核；离线 PASS 不能等同正式 Pilot 可执行。

## 最终状态与下一步

最终 `snapshots/five-host-final.json` 为 `HOST_RESIDUE_PASS`，`snapshots/health-final.json` 为 `STAGE5_BASE_HEALTH_PASS`：4 Node Ready、Calico BGP 各 3/3、现有 k6 HTTP failures 0、Prometheus Ready、OpenSearch green、Jaeger OpenSearch 存储与 Collector Ready。修复后的 Proxy revision 3 Ready 于 Worker2，最终 7/7 UID/route/packet 证明通过；故障进程、netem/qdisc 和回滚 timer 均无残留。Master 约 17 GB 可用。本轮 `faults_injected=0`、`formal_cases_created=0`、`GT_reads=0`、`real_rca_predictions=0`、`contracts_frozen=false`。

下一步应先独立定位 Worker3 节点 CPU 增量中宿主进程、探针和 Collector 的份额，并以新的预注册无故障方案同时验证 Rec ≥30/min、目标边 ≥30/min、正常对照稳定性与资源预算；若认为 1.25 的“全窗任一分钟超标即失败”不适用于正常抖动，需要依据独立 clean 数据设计、人工审核并**前瞻性**验证新规则，不能回溯性放行本轮窗口。还需在正式 v3 冻结前审查 +5 s 首读、代理配置和新拓扑，并完成真实执行器的角色隔离集成。**当前不建议正式冻结 Stage5-v3 或启动 DEV/TEST。**

| 审计锚点 | SHA-256 |
| --- | --- |
| Stage5-v2 冻结清单（只读） | `6ebafa764b904950c00904d06cebae68c66727a8cf703158aa259bb728cd6452` |
| C3 独立 Clean 候选清单（非 v3 protocol freeze） | `535db34abd8f9893fb9258e442db979779301aafa888c5aeb7bfb8f197d0aa89` |
| 代理修复最终清单 | `2e95ea1303ea7adc673acb3799b787366aef6a8fac8cc8944a0aa3864aaa856f` |
| 最终 health / 五主机残留 | `f27eab4dd63dd15572e319c75fde1c48ec4f17302ac46b047f14c60f7c361034` / `ee8a7024f794cb27241b9e6687aa6734ef7a3dd0874aca868b2f430bfbf78aa7` |
| 最终离线检查 / 机器汇总 | `3a3d2a3bfc45d8386debf7b6c462e9ed1ef036274d6861787d0e47728019171f` / `b275ce1babf865e7c4a07cc1d5dabcfa0cef7c2517555fa7a4ca0dcb7cebe89b` |

机器可读总表：`results/autonomous-preparation-summary.json`；各窗口 `capture-manifest.json`、`completion-manifest.json` 与路由、探针和源代码 SHA 共同提供逐文件审计。此报告结束本轮，等待人工审核后再决定下一批资格实验或 Stage5-v3 协议修订。
