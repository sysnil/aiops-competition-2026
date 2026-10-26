# SF-011 ATTEMPT-03 单 HTTP 候选说明

**状态：DRAFT，未执行、未生成正式题。** SF-011 ATTEMPT-01/02 的 ServicePause + HTTP ReqDelay 双故障均按原准入合同判为失败。ATTEMPT-02 的 Service 故障期间 Cart 正常对照 p95 从 16.604 ms 升至 21.300 ms，比值约 1.283，超过 1.25；不能删除该窗口、改变阈值或用后续结果追认。

ATTEMPT-03 候选仅使用已通过真实资格的首页 HTTP ReqDelay：普通业务 `GET /` 增加 250 ms，持续 60 秒，计划 T+900。负载保持 LOW-STABLE：0.7 sessions/s，Image、Cart、Home、Recommendation 四条固定普通业务流各 1/s；30 分钟窗口与 Image/Cart 对照样本、p95 和请求/Span 身份规则不变。该机制在 SF-010 ATTEMPT-07 的真实执行和 SF-004 ATTEMPT-05 的正式准入中已有证据，但当前目标身份、Collector、五主机空间和恢复仍须重新检查。

新代码位于 `/root/ops-competition-2026/sf011-http-adapter-v3-20261018/` 与 `/root/ops-competition-2026/sf011-http-formal-v3-20261018/`，设计登记位于 `/root/ops-competition-2026/low-stable-v4-20261017/design/sf011-attempt03-retry-draft.json`。Master 上 38 项适配器、10 项采集测试通过。日志导出对 OpenSearch 查询边界 1 ms 内的邻接记录显式计数并按 `[start,end)` 排除，超过查询精度仍拒绝；旧 Attempt 转换和准入不变。

当前候选与 SF-004 的故障机制和作用路径相同。即便将来完成生产，也应在题库多样性分析中标注此重复，不得只凭不同随机种子、题号或起始时刻宣称独立异常类型。正式执行还取决于新的输入哈希冻结、完整五主机无故障资格、Worker3 归档空间和新鲜安全门禁。
