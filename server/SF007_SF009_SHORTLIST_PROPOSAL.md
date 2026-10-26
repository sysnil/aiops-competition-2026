# SF-007 / SF-009 人工审核后精简候选（待确认）

**状态：PROPOSAL_ONLY。** 不修改 `FINAL-STAGE3-HYBRID-GT` 08:56 UTC 冻结快照、14 份首次预测、评分或历史审核账本。最新人工账本写于冻结之后：SF-007 SHA-256 `1fb70ee9cd2c842405fc7f335b6e79298ba366ea353f04d04a5c85e4cd87cd26`，SF-009 SHA-256 `c697e9f7e53036a01006ad084dab34fabec609b2a015fb43c7686253209c469c`。

筛选依据是：人工排除优先、保留候选重新计算事件边界、同窗的真实 Trace 样本与 p95/错误信号、持续时间，以及是否与目标注入窗重叠。分钟统计只归属其实际覆盖的候选时间窗。这里的“明显”是下一批 GT 候选的审核标准，不是根据上一批 Agent 得分反选题目；任何新测试必须先锁定候选及评分版本，再运行 Agent。

## 建议保留

| Case | 自动组 | 实体 | 人工状态与证据 | 新版建议类型（保持现有 11 类） |
| --- | --- | --- | --- | --- |
| SF-007 | EVT-0001 | frontend | 8 条 `CHECKED`、1 条 `REJECTED`；保留成员联合时间约 34.7 秒，最多 680 spans/分钟、81 errors，p95 相对基线最高约 2048 倍。 | `TRACE_LATENCY_INCREASE`；有独立请求错误证据时可接受 `REQUEST_ERROR_INCREASE`。 |
| SF-007 | EVT-0002 | frontend-proxy | 4 条 `CHECKED`；原组约 58.2 秒，人工修订的 ANOM-0010 把候选范围扩到约 120 秒；最多 891 spans/分钟、57 errors。**扩展边界需再次核查。** | `TRACE_LATENCY_INCREASE`；有独立请求错误证据时可接受 `REQUEST_ERROR_INCREASE`。 |
| SF-009 | EVT-0002 | frontend | 4 条 `CHECKED`、ANOM-0004 仍 `PENDING_REVIEW`；只用已核查成员重算为约 34.3 秒，最多 621 spans/分钟，p95 最高约 2129 倍。待审核成员不自动补入。 | `TRACE_LATENCY_INCREASE`。 |
| SF-009 | EVT-0004 | frontend-proxy | 4 条 `CHECKED`；约 55.0 秒，故障期 810 spans/分钟、42 errors，p95 最高约 241 倍。 | `TRACE_LATENCY_INCREASE`；有独立请求错误证据时可接受 `REQUEST_ERROR_INCREASE`。 |

建议每题 **2 个计分事件**，均低于 5 个。`TRACE_ERROR_BURST` 是生产侧原始标签；如保留其“请求错误增加”语义，映射到现有 `REQUEST_ERROR_INCREASE`，不增加新枚举。`TRACE_LATENCY_INCREASE` 已在现有枚举中。服务实体不应直接映射为 `CALL_EDGE_ERROR_INCREASE`，除非有独立的调用边实体及证据。

## 建议排除或暂缓

| Case | 自动组 | 处理建议与原因 | 需要确认 |
| --- | --- | --- | --- |
| SF-007 | EVT-0003 product-catalog | 4/4 原候选均被人工 `REJECTED`，直接排除。 | 无新增判断。 |
| SF-007 | EVT-0004 recommendation | 2 条 `CHECKED`、4 条 `REJECTED`；保留成员时间联合仅约 **0.273 秒**。分钟聚合有变化，但不足以证明该 0.273 秒就是稳定的服务级异常窗口。 | 是，排除会覆盖两条 `CHECKED`。 |
| SF-009 | EVT-0001 accounting | 1 条 `CHECKED`，约 13.0 秒，但证据分钟仅 17 spans、p95 约 2.26 倍。较其他候选弱。 | 是，排除会覆盖 `CHECKED`。 |
| SF-009 | EVT-0003 frontend-proxy 早期窗口 | 1 条 `CHECKED`，约 59.0 秒，05:07 的 p95 约 8.24 倍、1065 spans；**位于 05:13 故障注入之前**，不能把故障期 42 errors 算到此事件。可能是独立真实异常，建议单独确认是否纳入比赛 GT。 | 是，证据较强但不属于本次注入影响。 |
| SF-009 | EVT-0005 product-catalog | 1 条 `CHECKED`、1 条 `REJECTED`；保留时间约 **0.232 秒**。 | 是。 |
| SF-009 | EVT-0006 recommendation | 2 条 `CHECKED`、1 条 `REJECTED`；保留时间约 **0.202 秒**。 | 是；不能仅因它是故障目标就放宽可观测窗口要求。 |

若组委会希望计入独立但不由本次注入导致的异常，可将 SF-009 EVT-0003 加回，形成 **3 个**计分事件；它必须标为独立异常，且仍需复核是否为正常背景波动。其他短时事件不能只凭分钟级 p95 推断一个不足 1 秒的精确异常起止。

## 冻结前仍需解决

1. 明确批准上表中对已 `CHECKED` 候选的组级排除，以及 SF-007 EVT-0002 人工扩展的时间边界。
2. 如多条原候选最终组成一个计分事件，记录明确的人工归并决定与 `candidate_ids`；不能把 Stage 2 自动归并直接称为 `HUMAN_MERGED`。
3. 重新验证每个保留事件的实体、类型、时间和 Public 证据；记录被拒绝成员的原因及审核账本 SHA。对有 `PENDING_REVIEW` 的 SF-009 ANOM-0004 不作已接受处理。
4. 评分器需在新版本中拒绝零分配对占用 GT 事件，并正确统计误报。现有 08:56 批次的首次评分保持不变。
5. 下一批继续使用选手已有 11 类异常枚举；只有无法表达且经组委会审定的新现象才考虑改枚举。目前这两题没有显示必须修改枚举的证据。
