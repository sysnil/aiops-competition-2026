# SF-013～SF-017 决赛观测准入修订与生产状态

**状态：`PRODUCTION_BLOCKED`; SF-013～SF-017 新正式题 0/5、真实故障注入 0、新 Public/Private 包 0、51 传输 0。** 2026-10-20 02:29 UTC 复核 Master：`runs/` 仅有 SF-013 历史 ATTEMPT-01/02，`packages/` 无 SF-013～SF-017。原定 10 小时目标已过，不能将 DRAFT 合同或无故障采集当作五道题。

## 前瞻性放宽范围

独立 DRAFT 合同 `final-case-observation-v1-20261020/design/collection-contract-draft.json` SHA-256 `ed5acbc46b95d8fb468b37eebdfa1b968a03fb4686751ebc51c893675593c3a9`；只读检查器 `scripts/assess_collection.py` SHA-256 `fd217c60e5f21abc61f0ccf308d6900c70af1e6fa4bac25bd25f0431ac5c10b4`。它们没有修改已冻结历史 Case 或当前 v5 生产代码。

- 仍需完整 1800 秒、六个时间段，每段 Metrics/Logs/Traces 非空，Trace 来源、窗口及 Manifest 哈希链一致；Collector receiver refused 增量必须为 0。
- 只对普通 Image/Cart 正常对照允许最多 1% 的总体未成功请求，每 300 秒段仍须至少 270 个真实成功请求；有身份的对照请求至少 99% 在最终 `+240` Trace 快照中匹配真实 Proxy Server Span。未匹配 Trace ID 原样列出，不生成替代 Span。
- `+120` 后、`+240` 内到达的 Span 可占 `+120` 最终 Span 数的至多 0.5%；采集时间与晚到数量必须记录，`+240` 不可变快照才是候选规范输入。
- 故障目标在 During 允许真实失败、超时及目标 Server Span 缺失，但这些请求必须保留并由独立公开的客户端/资源证据解释。故障注入和恢复、目标身份、候选异常的 Public 证据、GT 人工审核、五机安全与十进制 5 GB 磁盘底线均为独立硬门禁，不由本合同放宽。

三项合成边界测试通过：有限对照/Span 缺口可以进入 Admission 审核；整段模态缺失、Collector 拒收、某段正常对照不足或晚到超限均阻断。检查器实际只读 v5 无故障 Run `SF-017-ATTEMPT-MARKERTEST-22991414`，返回 `DATA_COLLECTION_ACCEPTED_FOR_ADMISSION`、`issues=[]`；Image/Cart 六段各 300 次成功、对照 7,200/7,200 匹配 Proxy Span、晚到比例 0、Collector refused 增量 0。该结果只证明 DRAFT 判据能读取现有真实数据，不构成真实故障 Case 的准入。

## 当前执行阻断

版本绑定的真实业务机制资格仍缺失：v5 Watchdog 牺牲进程自检、Marker 和 1800 秒无故障采集已经通过，但 Frontend PodMemory 300 秒、Recommendation ServicePause 300/240 秒、真实首页 ReqDelay 300/240 秒及 SF-017 联合动作均未在 v5 正式入口完成独立效果和恢复验证。现有 v5 控制器也未接通这五题的机制级 Admission、Public/Private Parquet 导出、GT 草案和异机正式归档。历史 SF-003～SF-012 的真实机制结果不能伪造 `QUALIFIED_FOR_THIS_VERSION` 回执。

2026-10-20 02:29 UTC 只读检查：4/4 Kubernetes Nodes Ready；Frontend、Recommendation、Collector、Prometheus、OpenSearch、Jaeger Pod Running；OpenSearch green；Master `/data/stage5-v3` 可用 `46,693,163,008` bytes；Master 无待触发 `ops-sf*` Timer。未变更 51 的数据库、API、GT、发布状态，也未向 51 传输未完成的题目。

下一步需要先实现并用无害/牺牲进程验证同版机制资格执行器，随后逐机制做受控真实资格试验并保存效果、恢复、身份和 Public 证据；再把新观测合同与正式捕获、Admission、导出及归档流程集成和冻结。只有这些门禁通过，才能逐题执行新的 1800 秒正式 Attempt；每题 Public 校验通过后方可传到 51 受保护暂存区，Private GT 仍须人工审核。
