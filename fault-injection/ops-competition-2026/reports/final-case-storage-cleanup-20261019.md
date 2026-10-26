# 决赛生产存储清理记录（2026-10-19）

状态：`WORKER3_DUPLICATE_REHEARSAL_DATA_COMPACTED`

本次只清理 Worker3 `/data` 上两份旧无故障演练归档中的重复原始观测数据。Master 上的原始数据逐文件与归档 source manifest 核对，全部匹配；Worker3 归档的 manifest、历史校验记录、控制文件和报告保留。历史校验记录证明删除前的归档状态，不再代表 Worker3 当前仍有完整原始数据。

| 演练 | Worker3 删除目录 | Master 保留的原始目录 | 文件数 | 逻辑字节数 | Source manifest SHA-256 | Master 复核 |
| --- | --- | --- | ---: | ---: | --- | --- |
| SF30-NOFAULT-20261015 | `/data/rca-archives/ops-competition-2026/SF30-NOFAULT-20261015/raw` | `/data/stage5-v3/ops-competition-2026/rehearsals/SF30-NOFAULT-20261015` | 2,564 | 5,880,259,290 | `66d23ed19405a66df24d72dc3f48c004d9f6f4d33d98d7f2820878cb525bb9a7` | 0 mismatch |
| SF01-PREATTEMPT04-20261014 | `/data/rca-archives/ops-competition-2026/SF01-PREATTEMPT04-20261014/data` | `/data/stage5-v3/ops-competition-2026/pre-attempt04` | 3,117 | 6,055,861,012 | `b0902de1733136421c8a8f802922167531a57a362cec7586316596bb83addcf9` | 0 mismatch |

删除前确认上述 Worker3 路径没有子挂载、符号链接或进程打开的文件。只执行针对这两个精确目录的 `find -xdev -depth -delete`，没有触碰 `/data/otel-storage`、在线 Collector、科研冻结资产、正式 Case、Private GT 或预测。

Worker3 `/data` 删除前可用 `10,757,607,424` bytes；删除后复测可用 `22,653,210,624` bytes，净增加约 `11.90` GB。Master `/data/stage5-v3` 复测可用 `42,222,333,952` bytes；Master 原始样本读回成功。

清理后：4/4 Kubernetes 节点 Ready；Jaeger、OpenSearch、OTel Collector、Prometheus Pod 均 `1/1 Running`；Worker3 没有待触发的 `ops-sf*` Timer。未执行真实故障或正式生产。

剩余容量风险：按此前每题 Worker3 后台增长 2 GB 的五题模型，当前约有 `7.65` GB 的 5 GB 底线以上预算缓冲。Master 的五题累计模型仍接近底线，正式生产前须按逐题峰值重新测量，不能将本次清理视为生产安全门禁通过。其他重启生命周期、真实适配器资格和人工许可门禁仍保持原状态。
