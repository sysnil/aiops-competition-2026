# 半决赛正式 GT 审核建议与评分公平性完善报告

日期：2026-10-15  
状态：`SEMIFINAL_SCORING_DEMO_READY`（保持不变）

> 本报告给出 SF-001 草稿的人工审核建议，但不构成 GT 批准。原始 13 事件草稿未修改，未写入 `APPROVED`，也未用于正式排行榜。升级到 `SEMIFINAL_SCORING_READY` 仍需组织方双人审核、签字冻结和真实正式提交验收。

## 1. 审核范围与方法

审核对象：`/etc/aiops-private-grader/gt-staging/SF-001/anomaly-gt-draft.json`。

- 草稿状态：`DRAFT_PENDING_HUMAN_REVIEW`
- 候选数：58
- 已归并草稿事件：13
- 源 SHA-256：`ff48d969e676d01ff1134809d23ec225f0f1e9893709e928f9f707be04fd6f9a`
- 本轮完成后 SHA-256：相同
- `formal_scoring_gt=false`
- 引用回查：草稿记录为 `PASS`

审核时逐项复核了实体、观测窗口、基线/参考值、异常样本数、Trace/Span 身份、日志严重程度和 Public 引用。审核原则是“独立可观测异常”，而不是故障注入次数；同一链路异常在调用边、上游 Service、下游 Service、Metrics、Logs、Traces 中的传播表现不应全部变成必须分别发现的评分项。

## 2. 13 个草稿事件逐项审核建议

| 草稿事件 | 实体与时间 | 建议 | 依据与后续动作 |
|---|---|---|---|
| EVENT-0001 | CallEdge `checkout → payment`，10:10:31–10:11:21 | **ACCEPT** | 48 次配对调用、4 条独立引用，p95 19.492ms 对基线 4.565ms，约 4.27 倍；实体和窗口明确。建议作为 `CALL_EDGE_LATENCY_INCREASE` 独立候选。 |
| EVENT-0002 | CallEdge `frontend → product-catalog`，09:29:25–09:29:27 | **MERGE** | 930 次调用、101 错误、p95 94.496ms 对基线 16.475ms，异常真实；但与 EVENT-0012 的 product-catalog 服务端错误/延迟是同一短窗口的客户端视角，若同时作为必答项会重复计分。建议并入 EVENT-0012 的调用边佐证和可接受实体关系。 |
| EVENT-0003 | CallEdge `frontend → recommendation`，09:24:25–09:29:21 | **ACCEPT** | 连续五分钟，多个分钟 p95 从约 60 秒到 298 秒，错误数最高 49/49，对基线 8.712ms，证据强且边界清晰。建议作为这一长时段链路异常的主评分事件。 |
| EVENT-0004 | CallEdge `frontend-proxy → frontend`，09:24:25–09:29:21 | **MERGE** | p95 约 8 秒、每分钟 44–49 个错误，异常真实；时间与 EVENT-0003 完全重合，是同一请求链路向上游传播的表现。作为独立必答项会要求选手重复报告同一事件。并入 EVENT-0003 的传播证据。 |
| EVENT-0005 | CallEdge `load-generator → frontend-proxy`，09:24:27–09:30:22 | **MERGE** | p95 约 15 秒、无错误，是 EVENT-0003/0004 的更上游延迟表现，并在 09:29 后延续。建议仅作端到端影响证据，不作独立评分分母。 |
| EVENT-0006 | CallEdge `recommendation → product-catalog`，09:29:25–09:29:27 | **MERGE** | 调用边错误与延迟均有真实 Span，但与 EVENT-0012、EVENT-0013 同一两秒窗口及同一调用链。建议并入 EVENT-0012，保留为精确的调用边证据。 |
| EVENT-0007 | Pod UID `d4bb49b4-...`（frontend Pod），09:47:55–09:52:25 | **ACCEPT** | 19 个连续异常样本，工作集约 169.5MB，对基线中位数 134.1MB、独立参考 131.8MB，超过 16MiB 阈值；与其他 Trace 事件时间隔离。建议类型 `MEMORY_USAGE_INCREASE`，正式 GT 同时登记 Pod 名称别名。 |
| EVENT-0008 | Service `checkout`，10:10:31–10:11:21 | **MERGE** | 4 个 `PaymentService/Charge` client Span，时间、样本和 EVENT-0001 相同，是调用边异常的 Service 级表示。建议并入 EVENT-0001，并允许 Service `checkout` 作为上层实体部分得分，而非另设分母。 |
| EVENT-0009 | Service `frontend`，09:24:25–09:29:27 | **NEEDS_REVIEW** | 一个草稿事件混合了 09:24–09:29 的 recommendation 长延迟/错误和 09:29:25 的 product-catalog 短错误；属于两个不同观测窗口，不能作为单一可复现评分项。应先拆分，再分别并入 EVENT-0003 与 EVENT-0012；拆分前不建议纳入正式分母。 |
| EVENT-0010 | Service `frontend-proxy`，09:24:25–09:29:21 | **MERGE** | server/client Span 的 p95 约 8 秒，229 个错误样本，异常真实；与 EVENT-0004 使用相同链路和时间，是 Service/Edge 两种表现。建议并入 EVENT-0003。 |
| EVENT-0011 | Service `load-generator`，09:29:26.704–09:29:27.053 | **REJECT**（独立项） | 只有 2 个无错误 Span，p95 1043ms 对参考 23.525ms。观测值异常但样本过少，且属于 EVENT-0012 短窗口的上游传播；证据可保留为辅助，不应成为选手必须单独发现的事件。 |
| EVENT-0012 | Service `product-catalog`，09:29:25–09:30:00 | **ACCEPT** | GetProduct 983 Span/101 错误，ListProducts 337 Span/142 错误；p95 分别 61.911ms、1012.369ms，对参考 13.252ms、2.153ms。服务端实体与异常显著，建议作为短时 product-catalog 服务异常主事件，允许错误/延迟等价类别。 |
| EVENT-0013 | Service `recommendation`，09:29:25–09:29:27 | **MERGE** | 142 Trace 错误、p95 约 1.04 秒，另有 142 条显式 ERROR 日志，证据充分；日志明确指向 product 查询连接失败，与 EVENT-0006/0012 是同一调用链的跨模态表现。建议并入 EVENT-0012 的 Logs/Traces Rubric，不重复计分。 |

建议统计：

- `ACCEPT`：4 个（0001、0003、0007、0012）
- `MERGE`：7 个（0002、0004、0005、0006、0008、0010、0013）
- `REJECT`：1 个（0011 作为独立项）
- `NEEDS_REVIEW`：1 个（0009，需拆分后归并）

若组织方采纳上述建议，预计形成 4 个独立评分事件。这个数量来自 Public 观测去重，不是把两次故障注入机械映射成两个 GT。正式审核人仍可调整，但必须记录理由、版本和签名。

## 3. 答案数量上限修订

发现的冲突：草稿/GT Schema 最多 16 个事件，实际草稿 13 个；原 v3 答案 Schema、Pydantic 网关和匹配器仅允许 10 个，Agent 默认甚至只输出 5 个。即使选手完全发现全部草稿事件，也无法提交满分答案。

修订结果：

- answer v3 JSON Schema：10 → **16**
- v3 Pydantic `AnswerV3`：10 → **16**
- 确定性匹配器：10 submissions/16 GT → **16/16**
- 50 机 `config.yaml output.max_anomalies`：5 → **16**
- LLM final tool Schema 自动继承 16
- GT Schema 上限继续为 16
- 历史 answer v2 上限继续为 10，不改写语义或历史记录

未放宽的限制：每事件仍为 1–20 条证据；单记录 16KiB；客户端答案约 120000 字节；网关 128KiB；重复事件去重；独立多报每条扣 5 分；队伍频率、并发和正式提交次数保持。

满分可达性测试使用合成、人工构造 GT，分别提交 1、13、16 个完全正确事件，三组均得到 **100/100**。16 事件确定性匹配测试约 4.5 秒，仍在受控范围内；正式赛前应结合最终 GT 数量继续做性能基线。

## 4. Baseline `PENDING_JUDGE` 根因与修复

旧实现的直接原因：

1. 五个匹配事件按顺序调用 Judge。
2. 网关到私有 grader 的总请求超时只有 30 秒，而单事件模型调用自身也允许 30 秒并带重试。
3. 任一事件失败会让整份答案成为 `PENDING_JUDGE`。
4. 已成功事件没有事件级持久化；网关重试会从头再次调用全部事件。
5. `PENDING_JUDGE` 没有自动有界任务重试路径。

已实施：

- 新建私有 SQLite：`/var/lib/aiops-private-grader/judge-results.sqlite3`。
- 按 submission、GT 版本、GT 事件、事件内容/证据/Rubric/模型/Prompt 指纹缓存结果。
- 单事件成功立即持久化；后续只重试失败事件。
- 同一 submission 的重复请求串行化，避免并发双重评分。
- 不同 submission 共用全局 `JUDGE_CONCURRENCY=2` 有界信号量。
- 网关私有评分请求超时提高到 180 秒；网络级重试仍幂等。
- 增加 `WAITING_JUDGE` 中间状态、任务级 2 次重试、2 秒递增退避。
- SQLite 增加 `judge_attempts`、`judge_progress_json`、`judge_failure_code`，迁移保留历史行。
- 失败分类包括配置缺失、连接失败、HTTP 错误、超时、非法 JSON、非法结构响应和私有服务不可用。
- 重试耗尽后保持 `PENDING_JUDGE`，不会自动给予零分或满分。

真实 Baseline 五事件复测：

- submission：`SUB-D18A9B6D7302460BAFC4`
- 15 秒内完成，状态 `PREVIEW_COMPLETED`
- Judge 进度：5/5 完成，0 失败
- 五个事件均各调用一次并持久化
- 对相同 submission 直接重复评分耗时 0.043 秒，`cached=5`，没有再次调用模型

旧 `PENDING_JUDGE` 历史记录未覆盖，保留审计事实。

## 5. 多事件、多模拟队伍测试

自动化并发测试同时启动 3 个模拟队伍，每队 4 个事件，共 12 个事件：

- 三队均得到 100/100。
- 实测最大同时 Judge 调用数为 2，没有突破全局上限。
- 单事件合成超时后，同提交第二次评分只重试该失败事件；先前成功事件从缓存读取。
- 幂等第二次评分得到与第一次相同的结果。
- 1、13、16 事件归一化均为 100，未因事件数量改变满分尺度。

51 机完整测试：51 项全部通过（随后新增练习模式测试单独 13 项通过）。50 机 Agent 测试：16 项全部通过。

## 6. 三种 Agent 在相同 Public 数据上的重新比较

没有重新运行故障注入，也没有向任何 Agent 提供 GT、注入记录、隐藏时间或审核建议。三种模式继续使用此前同一 SF-001 Public 数据及同一答案；本轮用修复后的 Judge 重新提交。

| 模式 | 提交异常数 | v3/证据 | 13 事件草稿 PREVIEW | 分项（现象/实体/时间/证据） | 多报扣分 |
|---|---:|---|---:|---|---:|
| Baseline | 5 | 通过/通过 | 20.000 | 9.231 / 3.077 / 3.077 / 4.615 | 0 |
| LLM Agent | 1 | 通过/通过 | 6.922 | 3.077 / 1.538 / 1.538 / 0.769 | 0 |
| LLM+Skill+MCP | 1 | 通过/通过 | 0.769 | 0 / 0 / 0 / 0.769 | 0 |

这些仍是未审核 13 事件草稿的预览结果，不是准确率结论。按本轮人工建议做定性覆盖复核：

- Baseline 覆盖了 frontend/recommendation 长时 Trace 表现和 frontend Pod 内存异常；两个 kube-apiserver 日志事件可能成为独立多报；对四个建议主事件的实体粒度仍不完整。
- 纯 LLM 的 recommendation 错误日志位于 EVENT-0013，若该事件并入 EVENT-0012，可作为 product-catalog 短时异常的跨模态支持，但实体不是建议主实体。
- 完整模式输出的 `default`/calico 09:15 日志突增不覆盖四个建议主事件，正式 GT 若采用四事件方案可能构成多报。

在正式 GT 未批准前，没有据此调 Prompt、硬编码时间或修改 Agent 算法。

## 7. SF-001/SF-002 练习题状态

SF-001：

- Public Case 已发布。
- 新 v3 提交统一标记 `PREVIEW`，不计正式次数、不进正式排行榜。
- 新提交必须显式使用 v3；历史 v2 行继续可读，但不再接受新的 SF-001/SF-002 v2 答案。
- 数据库中存在 4 条早期 SF-001 `OFFICIAL` 历史行，这是旧路由部署前的审计记录，本轮未篡改。当前及未来练习提交不会增加该计数。

SF-002：

- 当前 `/v1/cases` 只有 SF-001，SF-002 Public 数据尚未导入。
- 路由、v3 Schema、PREVIEW 模式和评分器代码可复用；真实数据导入后的 Metadata/Topology/Metrics/Logs/Traces、证据哈希和提交回归仍为 **PENDING**。
- GT 未批准时只能 PREVIEW 或待审核，不能产生正式分数。

## 8. 安全、兼容和回滚

- 原始 SF-001 GT 草稿 SHA-256 未变化。
- 未修改 Public 数据、私有注入计划或历史评分。
- 事件缓存位于私有 grader 可写目录，`root/aiops-grader` 边界不变，API 用户不能读取。
- 公开 API 只返回汇总进度和失败类别，不返回事件级 Judge 结果、GT、Rubric 或匹配明细。
- LLM 的 FULL/PARTIAL/NONE 标准和证据真实性校验未放宽。
- v2 数据库行、Fault Taxonomy 和历史查询均保留。
- 备份位于 `/opt/aiops-contest-server/backups/gt-finalization-20261015/` 和 50 机 Starter 的 `backups/gt-finalization-20261015/`。

## 9. 正式评分验收仍缺少的条件

1. 两名授权审核人逐项确认本报告建议，处理 EVENT-0009 的拆分归并。
2. 为最终独立事件冻结主类型、等价类型、实体别名、上层实体、观测边界、证据 Rubric 和容差。
3. 将独立审核文件写为新版本，记录审核人、时间和 SHA-256；不得覆盖源草稿。
4. 至少一个真实练习题 GT 设置为 `APPROVED` 后，完成一次真实 `COMPLETED` 提交验收。
5. SF-002 Public 数据导入并完成同样的证据与 GT 审核。
6. 使用正式并发规模验证 Judge 队列、外部模型配额、超时和人工接管流程。
7. 正式题 SF-003～SF-012 发布后逐题验证 30 分钟窗口与短异常的 ±120 秒容差。

在上述条件完成前，状态保持 `SEMIFINAL_SCORING_DEMO_READY`。

## 10. 关键交付路径

- 本报告：`/opt/aiops-contest-server/SEMIFINAL_GT_AND_GRADING_FINALIZATION.md`
- 事件缓存：`/var/lib/aiops-private-grader/judge-results.sqlite3`
- 51 机备份：`/opt/aiops-contest-server/backups/gt-finalization-20261015/`
- 50 机备份：`/opt/aiops-competition/participant-starter/backups/gt-finalization-20261015/`
- Baseline 新结果：`/opt/aiops-competition/participant-starter/runs/semifinal-v3-baseline-accepted/grading-result-retry.json`
- LLM 新结果：`/opt/aiops-competition/participant-starter/runs/semifinal-v3-llm/grading-result-finalization.json`
- 完整模式新结果：`/opt/aiops-competition/participant-starter/runs/semifinal-v3-full-accepted/grading-result-finalization.json`

