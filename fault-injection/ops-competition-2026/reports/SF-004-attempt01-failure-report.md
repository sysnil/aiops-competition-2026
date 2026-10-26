# SF-004 / ATTEMPT-01 观测准入失败记录

**终态：FAILED_OBSERVABILITY_CONTROL。不是 `READY_FOR_IMPORT`，不进入正式题库。**

2026-10-16 04:47:55.627366～05:17:55.627366 UTC 完成连续 1800 秒真实业务与三模态采集。唯一实际故障为 T+900～T+960 首页请求 250 ms 延迟；84 条真实首页请求在注册窗口携带延迟头，之后短租约撤销，`HTTP-RECOVERY` PASS，没有 Worker 故障 Timer。六段质量均 `CHUNK_QUALITY_PASS`，21,475/21,475 条完成请求与公开窗口的 Proxy Span 匹配，边界例外 0。

冻结 Public 的首页 Proxy Server Span 表明故障真实可观测：前/中/后分别 168/84/168 条，p95 为 14.175/266.632/14.461 ms。Image 对照前/中/后 120/60/120 条，p95 为 1.291/1.605/1.396 ms，按预注册规则通过。Cart 对照前/中/后 240/120/240 条，p95 为 **9.420/9.357/14.275 ms**；故障后/故障前比值约 **1.515**，超过冻结的 1.25 上限。其他 8 项准入检查通过，仅 `cart_normal_latency` 为 false。独立判定 `/root/ops-competition-2026/sf004-finalization-20261016/http-admission.json`，SHA-256 `239d0767bfa3c1fe8e4844ce35dbb3b5271fc0db2ca9866f79f5c99fb123ab74`。不得用用户侧首页异常明显、私有注入记录或新的对照选择来覆盖此失败。

Public 六文件完成独立全量读回：Metrics 41,160、Logs 300,311、Spans 437,558、Trace Index 62,962；30/30 分钟三模态非空，文件哈希、时间边界、UID、去重及泄漏扫描 PASS。Public Manifest SHA-256 `8f4aa40786db0119bb30bebae471263a9beb21850c4c60a88feee117600de129`，六文件合计 41,517,810 bytes。Public-only 异常草案有 6 候选/6 事件，保持 `DRAFT_PENDING_HUMAN_REVIEW`，不得进入正式评分；其中 Frontend Proxy 时延是可复查症状，其余 Pod/load-generator 候选需人工排除或确认。

失败尝试仍完整保护：Master 原始目录 `/data/stage5-v3/ops-competition-2026/runs/SF-004-ATTEMPT-01/`，最终证据包 `/data/stage5-v3/ops-competition-2026/packages/SF-004-ATTEMPT-01/`；Worker3 各有原始与输出第二副本。原始 2,671 文件、5,896,657,963 bytes，源 Manifest SHA-256 `1fea082c3ca68ab0b56a9059d3413ddcff501637916f851eb6188e490edf5636`；输出 12 文件、41,580,804 bytes，源 Manifest SHA-256 `894e8f5a8ec46c601aae7de7a25825ad0824a0bc05e33d9dde283716f65023d4`。两机 SHA 与恢复读回 PASS。

五主机残留 `HOST_RESIDUE_PASS`，Master 实时环境 `LIVE_ENVIRONMENT_PASS`，Collector gRPC/HTTP refused 为 0；所有相关文件系统仍高于十进制 5 GB 下限。最低数据盘余量为 Worker1 46,648,545,280 bytes，Worker3 56,554,770,432 bytes。未改写 SF-001/002/010，不访问比赛服务器，不发布 API。

此 Attempt 的冻结阈值、原始采集、Public、私有执行记录和失败判定均不修改。若未来独立设计另一道首页延迟题，必须另行预注册正常对照和时间合同，在看到其故障结果之前完成无故障资格验证；本尝试不能重标为成功。
