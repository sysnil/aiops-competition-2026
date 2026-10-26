# 半决赛查询绘图题交付报告

**状态：`TRANSFER_VERIFIED`（仅指题目与 GT 暂存包完成传输和校验；未发布、未导入评分）。**

验证时间：2026-10-17 12:11 UTC。Master 源目录：
`/root/ops-competition-2026/semifinal-qa-benchmark-v1/`。

## 源数据检查

- 源 `sha256-manifest.json`：`3027e15e37fd34595b920fd9e36f3d6eb7d62ea9954b838e81734f401e2bab8d`；111 项原始生成文件全部读回匹配。
- Q01～Q10：10 道正式候选题，Case 分配 SF-003 3 道、SF-005 3 道、SF-010 4 道。EX-P01/EX-P02 分别来自 SF-001/SF-002，单独列为练习示例。
- 12/12 Question 和 GT 符合各自 JSON Schema；每题恰好 3 问。独立复算 12/12 题、36/36 答案通过，12 个逐题 Public-first solver 均为 `PUBLIC_FIRST_SOLVER_PASS`。
- 10 份正式 GT 的 `review_status` 均为 `DRAFT_PENDING_HUMAN_REVIEW`；正式评分 GT 目录中没有 SF-003、SF-005 或 SF-010 的已批准文件。
- 源目录没有独立的 `private/grading-rubrics/`：评分细则原本内嵌于每份 GT。交付程序从这 12 份原始 GT 无损派生独立 rubric JSON，同时派生 canonical chart data JSON；源 GT 未改变。

| Case | 源 Public Manifest SHA-256 | 51 原始 Public 状态 |
| --- | --- | --- |
| SF-001 | `8ad8c5eb25e48a33ebf776a40db0f3fbe62284ee7c26df3e4d04139532db0eaa` | 已导入，来源哈希相同，复用 |
| SF-002 | `458645d012ff5c6ffac1d9676a0275a15ab9da5bac403c1d3f5b3afa5b11011f` | 已导入，来源哈希相同，复用 |
| SF-003 | `2dda9c2d1cb4dd912df3efe5e5d2dcc8fb9b6ae2505f49f24682d2a2d22b1b8e` | 已导入，来源哈希相同，复用 |
| SF-005 | `3bddfc396be4bb0a6002d1bbd959c44bf770a692e916dfd5c71da31fafd756f2` | **未导入；本轮未传原始监控数据** |
| SF-010 | `6598702cfb9edd0cd5476de44e46c5c75191f12583c67c34c86e75a6e8d47a96` | 已导入，来源哈希相同，复用 |

Master 对上述五套冻结 Public 的 Manifest 与其声明文件重新计算了 SHA-256 和大小。51 已导入的四套 Case 用其 Public Manifest 的 `source_manifest_sha256` 核对，而非误用导入转换后自身的 Manifest 哈希。

## 分包与 51 落盘

Master 交付目录：
`/root/ops-competition-2026/semifinal-qa-benchmark-v1/delivery/semifinal-qa-v1/`。

| 包 | 文件数 | 压缩包 SHA-256 | 51 压缩包和解包路径 |
| --- | ---: | --- | --- |
| Public | 19 | `ce0e63d1ae12a3737a85ebe5aa789bf5780df6885311f12682aedca300f02b9e` | `/data/aiops-contest/staging/semifinal-qa-v1/public-package.tar.gz`；`.../public/` |
| Private | 115 | `66eea0a99271adfa90a18e54c9ca378bc053898fad0281062bdb61ce6fee8c04` | `/etc/aiops-private-grader/import-staging/semifinal-qa-v1/private-package.tar.gz`；`.../private/` |

Public 包含 10 个正式题面、2 个练习题面、Question Schema、Question Manifest、选手手册增补草案、两张练习图和公开字段/统计口径说明。Public 扫描未发现正式答案、正式参考图、Private GT 或注入机制信息。

Private 包含 12 份 GT、10 张正式参考图、12 份独立 rubric、12 份 canonical chart data、GT Schema、内部审核报告、完整源哈希清单、源 Public Manifest、独立复算记录及 Reference Solvers。正式题答案只在 Private 包。

51 端先验证两个压缩包的 SHA-256 和大小，再检查归档成员没有越界路径或链接，随后解包并逐文件重算：Public **19/19 PASS**，Private **115/115 PASS**。交付文件清单的 SHA-256 分别为：

- Public `DELIVERY_MANIFEST.json`：`16175007e51c17c3b4608f30384859b4c078501a1b6f6ab0b76e5a0ccf4fc9a9`
- Private `DELIVERY_MANIFEST.json`：`7d32aa21733e0d51bfaf63a1715d22877d39bae8524296f132be4a80c1ff6da0`

51 的实际验证记录：

- `/data/aiops-contest/staging/semifinal-qa-v1/public-verification.json`
- `/etc/aiops-private-grader/import-staging/semifinal-qa-v1/private-verification.json`
- `/etc/aiops-private-grader/import-staging/semifinal-qa-v1/package-hashes.json`

Master 到 51 的直连 TCP/22 不通。使用 Mac 上**既有已授权、已核验主机密钥**的两个 SSH 控制通道，以 `scp -3` 将包直接中转到 51；Mac 未持久保存交付包，也没有在命令、日志或报告中写入密码/Token。未创建账号、关闭认证或修改 SSH 策略。

## 隔离与发布状态

- Public 暂存位于 51 现有 `/data/aiops-contest/staging/` 下，目录为 `root:root`、0700；`aiops-api` 无法读取暂存题面。
- Private 暂存位于 `/etc/aiops-private-grader/import-staging/` 下，目录为 `root:aiops-grader`、0750，文件为 0640；`aiops-grader` 可读，`aiops-api` 与 Nginx 的 `www-data` 均不可读。Nginx 未配置到暂存目录的静态映射。
- 现有 API、Nginx、可视化服务均保持 active。比赛状态文件 SHA-256 为 `8c42b7af13079334d90d719bc6eb9e5826a2ea0b4303d4d7e45185360a992fd8`；practice=`CLOSED`，semifinal AM/PM=`NOT_OPEN`，正式 published_cases 为空。
- 未调用题目发布接口、未写正式 GT、未更改历史评分、未向 50 或选手机器传输 Private 包。

## 51 Codex 交接

51 Codex 可从上述两个**解包目录**读取题目与 GT，并分别用暂存目录内的 `DELIVERY_MANIFEST.json` 和 `verify_delivery.py` 复查。此交付仅为受控导入准备；不得把 Private 解包目录挂到 Public API/Nginx，也不得把 `DRAFT_PENDING_HUMAN_REVIEW` 改成 `APPROVED`。

**剩余限制：SF-005 原始 Public Case 尚未导入 51。** Q04～Q06 的在线查询/评分端到端测试必须等另行授权的 SF-005 原始 Public 导入与 Manifest 校验完成；不能以本次小型题目包冒充那套监控数据。本报告的 `TRANSFER_VERIFIED` 不表示题目已发布或在线评分已可用。
