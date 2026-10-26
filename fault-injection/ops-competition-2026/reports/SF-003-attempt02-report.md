# SF-003 / ATTEMPT-02 正式生产执行报告

**状态：READY_FOR_IMPORT；异常 GT 仍为 `DRAFT_PENDING_HUMAN_REVIEW`。**

正式窗口：2026-10-16 03:26:49.724275 至 03:56:49.724275 UTC，连续 1800 秒。MEDIUM 1.4 sessions/s、Image 1/s、Cart 2/s，种子 72003。故障组合为 **单个 Frontend PodMemoryStress**，不是 SF-010 的 Service/HTTP 双事件复刻。

## 故障与准入

- 目标：Worker2 上 Frontend Pod UID `d4bb49b4-6fb8-4a07-aaf6-e2caff5df974`；正常副本 UID `6305d17a-27f0-4dbf-8fc7-7238acb3be91` 在 Worker1。计划 T+600，配置 32 MiB、60 秒。
- 实际 03:36:50.049142 UTC 开始，原 2 秒调度合同最坏偏差 0.364 秒；实际持续 60.000 秒，03:37:50.164 UTC 撤销，单个受管内存 holder 被终止，目标业务进程与 Pod UID 未重建，T+840 恢复预检 PASS。
- 公开目标 Pod 内存中位数：前窗 135,143,424 bytes（20 样本），故障窗 171,257,856 bytes（4 样本），恢复窗 135,213,056 bytes（12 样本）。目标增加 36,114,432 bytes，达到事前 16 MiB/5×MAD 与连续 4 样本判据；正常副本同期中位数保持 138,506,240 bytes。
- 正常业务 Server Trace：目标副本前/中/后 1,628/331/884 条，正常副本 1,530/293/954 条。两副本 CPU/内存和 UID 连续性、正常对照、公开根因信号、注入与恢复共 10 项准入检查均 PASS。准入结果 SHA-256 `0268ec58acced86ec2bafa93064d66f16c063844a4cfa93a00801776ffc355e9`。

## 数据质量与 Public

- 六个 300 秒分段全部 `CHUNK_QUALITY_PASS`，Trace 使用 `JAEGER_OPENSEARCH_STORAGE` 与不可变 +5/+60/+120/+240 秒审计。21,326 条完成请求全部匹配公开窗口内的真实 Proxy Span，边界例外 0。
- 140 次安全样本；Collector 工作集峰值 343,654,400 bytes，gRPC/HTTP refused 均为 0。最终 `LIVE_ENVIRONMENT_PASS`、五主机 `HOST_RESIDUE_PASS`，Worker2 Pod Timer 和内存 holder 均不活动。
- Public 六文件：Metrics 41,160 条、Logs 298,746 条、Spans 433,676 个、Trace Index 62,528 条；合计 41,109,913 bytes。独立逐行读回确认 30/30 分钟三模态非空、半开时间窗、Schema、Pod UID、Trace/Span 唯一性、哈希和 Private 泄漏扫描 PASS。Public Manifest SHA-256 `2dda9c2d1cb4dd912df3efe5e5d2dcc8fb9b6ae2505f49f24682d2a2d22b1b8e`。

## Private 草案与归档

异常程序只读取 Public，生成 5 个候选（5 个未合并事件）。其中目标 Frontend Pod 的 `MEMORY_WORKING_SET_INCREASE` 有明确 Metrics 曲线；另 4 个涉及 load-generator Pod 的候选可能是背景波动，必须人工排除或确认，不能自动作为正式评分 GT。草案 SHA-256 `65a0654b5313fbdd479acaa316fedc2291e493f493a4a57c6646f2a24a493371`。注入计划、真实执行记录和 Worker2 原始逐秒内存样本均仅保存在 Private。

- Master 原始数据：`/data/stage5-v3/ops-competition-2026/runs/SF-003-ATTEMPT-02/`；Master 最终包：`/data/stage5-v3/ops-competition-2026/packages/SF-003-ATTEMPT-02/{public,private}/`。
- Worker3 第二副本：`/data/rca-archives/ops-competition-2026/SF-003-ATTEMPT-02/{raw,outputs}/`。原始 2,638 文件、5,844,894,469 bytes，源 Manifest SHA-256 `188e7c34b32fdc3013b08389f2ab14d0b5a6d7353f7076a5448ec14f347d626f`；最终 13 文件、41,191,853 bytes，源 Manifest SHA-256 `ecdb5d3626cbcdfcb9e1ff54eb79a802d65fc71c2be9c33d05c317486943842a`。Master 和 Worker3 均完成全文件 SHA 与恢复读回，Worker3 还验证了 Parquet 首尾及 Manifest。
- 最终可用空间（bytes）：Master 根盘 45,668,642,816 / 数据盘 226,368,954,368；Worker1 根盘 69,937,184,768 / 数据盘 47,834,578,944；Worker2 根盘 68,761,477,120 / 数据盘 48,812,425,216；Worker3 根盘 61,913,923,584 / 数据盘 63,104,991,232；VM1 根盘 87,397,507,072。均高于十进制 5 GB 下限。

ATTEMPT-01 因监控器启动宽限内未及时启动采集器，在故障前终止；原始日志与哈希保留，没有作为题目。ATTEMPT-02 是唯一成功的 SF-003 正式采集。未导入 192.168.51.51，也未开放任何正式 API。
