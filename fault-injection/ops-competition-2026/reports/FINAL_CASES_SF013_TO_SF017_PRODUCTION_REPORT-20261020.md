# SF-013～SF-017 决赛题生产与 Public 交接（2026-10-20）

**结论：5/5 题完成真实 1800 秒采集、Admission、Public 校验和两机归档；5/5 Public 包已传至 51 的受保护暂存区并校验 SHA。0/5 的评分 GT 获得人工批准，0/5 被本轮发布到选手 API。** `READY_FOR_IMPORT` 不应代替 GT 审核或赛题发布批准。

Master 原始数据：`/data/stage5-v3/ops-competition-2026/runs/<Attempt>/`；Public 和 Private 导出分别位于 `/data/stage5-v3/ops-competition-2026/exports/<Attempt>/public/` 与 `private/`。51 只接收六个 Public 文件，位置为 `/data/aiops-contest/staging/<Case>-incoming/`，目录均为 `root:root 700`。本轮未导入比赛数据库、未开放 AM/PM、未传输 GT。

| Case / Attempt | 真实故障及 UTC 效果窗口 | Metrics / Logs / Spans | Public 字节 | 归档 Worker | GT 草案：归并 / 提议计分 | 当前状态 |
| --- | --- | ---: | ---: | --- | ---: | --- |
| SF-013 / ATTEMPT-05 | Frontend Worker2 单 Pod 32 MiB，05:48:36–05:53:36 | 40,680 / 188,665 / 346,533 | 31,837,015 | Worker1 | 1 / 1 | Public 暂存；GT 待人工批准 |
| SF-014 / ATTEMPT-01 | Recommendation 两副本 ServicePause，06:49:06–06:54:06 | 41,856 / 189,297 / 316,341 | 29,692,523 | Worker2 | 13 / 5 | Public 暂存；8 个排除事件待人工裁定 |
| SF-015 / ATTEMPT-01 | 首页 HTTP ReqDelay 250 ms，首条 07:54:10、末条 07:59:09，210 条获授权请求 | 42,360 / 194,949 / 353,011 | 32,488,842 | Worker3 | 12 / 5 | Public 暂存；7 个排除事件待人工裁定 |
| SF-016 / ATTEMPT-01 | Frontend Worker2 单 Pod 32 MiB，08:54:43–08:59:43 | 42,360 / 196,467 / 356,902 | 32,887,727 | Worker1 | 5 / 2 | Public 暂存；3 个排除事件待人工裁定 |
| SF-017 / ATTEMPT-04 | HTTP 13:55:16–13:59:14（168 条）；ServicePause 14:01:17–14:05:17，非重叠 | 41,589 / 193,788 / 325,966 | 30,538,728 | Worker2 | 11 / 5 | Public 暂存；6 个排除事件待人工裁定 |

总计 208,845 条 Metrics、963,166 条 Logs、1,698,753 条唯一 Spans；五份 Public 共 157,444,835 字节。每题 `capture-terminal.json` 均为 `FORMAL_CAPTURE_COMPLETE_PENDING_ADMISSION`，`admission.json` 均为 `ADMITTED_FOR_GT_DRAFT`，`archive-result.json` 均为 `TWO_HOST_ARCHIVE_SHA_AND_READBACK_PASS`。所有 Worker 真实适配器效果均按计划撤销并验证清理；HTTP 故障头只加在获授权首页请求，正常 Image/Cart 对照不带故障头。

每题 Collector Metrics、Logs、Spans receiver refused 增量均为 0。每题 Image 和 Cart 各 1,800 个已发请求，分别 1,800/1,800 与最终 Proxy Trace 匹配。六个 300 秒段的哈希链、+60/+120/+240 Trace 审计和 Public/Private 泄漏检查通过。SF-017 的一个采集段存在 534 条字节等价重复 Span（0.885%）；v8 读链保留原始 OpenSearch 页与每条重复文档 ID/内容哈希，只导出唯一 Span，超过 2% 或内容冲突仍拒绝，超过 0.5% 明确记质量警告。

## Public 完整性

| Case | Public `manifest.json` SHA-256 | 51 目标 |
| --- | --- | --- |
| SF-013 | `3328c73eaf8533d343b6452cb4cd14d3708b2adacc9757b9f593941a8895ab7c` | `/data/aiops-contest/staging/SF-013-incoming/` |
| SF-014 | `7ce9501fe6dd0751566466cd0220aec64c5d89a06f4ee9bd03c35b4fc7665cfb` | `/data/aiops-contest/staging/SF-014-incoming/` |
| SF-015 | `2e36f67f8e95e0a70fdcb10612cc89c4c5a00255549f583489dadee5af0a04b2` | `/data/aiops-contest/staging/SF-015-incoming/` |
| SF-016 | `66797252cef3c83f06477bf8c59aec694b732c7bd30b23b55af36335522546a0` | `/data/aiops-contest/staging/SF-016-incoming/` |
| SF-017 | `1f772f68bf55bedb3239d4fc6d67a485f81ba109b504c300b6b0dd3d7bf39dd0` | `/data/aiops-contest/staging/SF-017-incoming/` |

51 上每个目录恰有 `metrics.parquet`、`logs.parquet`、`traces.parquet`、`trace_index.parquet`、`topology.json`、`manifest.json` 六个文件；传输时逐文件比对 Manifest SHA/字节数并检查 Parquet 首尾标记，传输后 51 独立计算的五个 Manifest SHA 与上表一致。Private、注入计划、控制日志和 GT 均未传输。未认证请求 `/v1/semifinal/cases` 返回 HTTP 401；该检查不替代持队伍 Token 的正式发布隔离验收。

## 采集链路修复与失败记录

SF-017 先前 ATTEMPT-01 因 Worker 缺少 HTTP 子动作资格回执在 T0 前 fail closed；ATTEMPT-02 因 OpenSearch flood-stage 只读造成首段零 Trace，在 T0 前停止；ATTEMPT-03 因五主机存储快照超过 30 秒新鲜度被拒绝。三次原始终态均保留，没有拼接为 ATTEMPT-04，也没有据此补造 Public 数据。

OpenSearch 位于 Worker1 `/data`，曾触发 95% flood-stage，Jaeger Span 和 Logs 索引被置为只读，最后入库 Trace 停在约 10:17 UTC。按用户已授权的旧观测数据整理，仅通过 OpenSearch API 删除 2026-09-22、23、24 的六个旧按日 Span/Logs 索引，约 13.55 GB；没有删除正式 Case 原始目录、GT、归档或 10 月索引。Worker1 `/data` 可用空间从 11.64 GB 升至 25.19 GB 后，只解除当前日两个索引的只读锁，未改 85%/90%/95% 水位。**这三个日期的在线历史遥测已不可再通过 OpenSearch 查询**；已冻结 Case 包未修改。

Collector 重试导致同一 Trace/Span 以不同 OpenSearch 文档 ID、相同完整内容重复入库。新版本仅归并严格相同内容，原始页与重复审计保持可复核；冲突、超过预注册 2% 上限、缺失观测、Collector 拒收仍 fail closed。修复后的独立 1800 秒无故障 Run `SF-017-ATTEMPT-MARKERTEST-8C2D5D42` 为 `FULL_1800_NOFAULT_ARCHIVE_PASS`：六段完整、7,200/7,200 正常请求匹配、三类拒收 0、Worker3 牺牲进程 Watchdog PASS、Worker2 3,650 文件/4,489,190,182 字节全量 SHA 及读回 PASS。该 Run 没有真实故障，也不作为正式 Case。更早失败演练及 SF-017 三次 T0 前失败均保留原终态。

当前正式版本的关键 SHA：`formal_controller.py` `bf540874de212e2829e377fcf35369227cdffec8b685df208ac9f550272da59c`；`production_run.py` `18141740387db86e59eaf7f84606047abdc8692bfcbede47fbd697654507e503`；v8 `trace_storage_reader.py` `6bdb1f99515bd5d3f594bac16becdf183b2611c9eb5b90fa0f87724cab0f43dc`；v8 `trace_identity.py` `3908d319b3b98e837172a768c367e0ad857ca391079e0782314f1b85e977c072`；v3 Observation Gate `564341ced29eccafe725d858cad3dd0deb7237b115a8738c682ad0f402747fa8`。完整源码/合同 SHA 以每个 Attempt 的 `freeze-manifest.json` 为准；SF-017 ATTEMPT-04 freeze SHA 为 `ba549998e461a8dfd3b54bc00c1f9bacaa4131f1ec7075b81892f441d8eff73d`。

## 最终安全与待审核事项

末次检查：4/4 Kubernetes Nodes Ready，Frontend/Recommendation/Collector/Prometheus/Jaeger/OpenSearch 关键 Pod Ready，OpenSearch green；Master、Worker2、Worker3 均无待触发本批竞赛 Timer。正式运行结束后 Master `/data/stage5-v3` 可用 **13,909,209,088 B**，Worker1 `/data` **22,319,415,296 B**，Worker2 根盘 **19,470,966,784 B**，51 `/data` **191,324,262,400 B**，均高于 5 GB 底线。SF-013 与 SF-016 对同一 Frontend Worker2 Pod 使用相同 32 MiB/300 秒机制，仅随机种子和采集时间不同，应由组委会评估题目多样性，不能称作新的故障类别。

Private `anomaly-candidates.json`、`anomaly-gt-draft.json` 与 `review-notes.md` 在各自 Master `exports/<Attempt>/private/`。SF-013 的 1 个提议事件可进入人工审核；SF-014/015/016/017 的未入选归并事件必须逐项有人工作出保留、合并或排除理由后，才能确认每题评分 GT 不超过 5 个。当前一律 `DRAFT_PENDING_HUMAN_REVIEW`，**不得导入正式评分或向队伍发布**。下一阶段可由 51 管理员独立导入已校验 Public，组委会审核 GT、SF-013/SF-016 相似性及正式场次访问控制。
