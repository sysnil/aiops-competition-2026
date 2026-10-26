# SF-005 Public 监控数据导入报告

**状态：`SF005_PUBLIC_IMPORT_VERIFIED`。** 仅指 SF-005 ATTEMPT-04 冻结 Public 数据已传至 51、注册并通过最终读回及 API 后端查询；没有开放选手访问、批准 GT 或修改成绩。

## 源目录与资格

Master 生产总账 `/root/ops-competition-2026/plans/SF-003-to-SF-012-production-v2-ledger.json` 将 **SF-005 ATTEMPT-04** 标为 `READY_FOR_IMPORT`，Public Manifest SHA-256 为 `3bddfc396be4bb0a6002d1bbd959c44bf770a692e916dfd5c71da31fafd756f2`。本次只使用：

`/data/stage5-v3/ops-competition-2026/packages/SF-005-ATTEMPT-04/public/`

源数据为 `CONTEST_EXCHANGE_V2`，窗口 `2026-10-16T11:23:55.458636+00:00` 至 `11:53:55.458636+00:00`（右端不含，1800 秒）。目录恰有六个 Public 文件，总计 **38,974,195 bytes**：

| 文件 | Master 与 51 原始暂存 SHA-256 | 行数 / 大小 |
| --- | --- | ---: |
| `manifest.json` | `3bddfc396be4bb0a6002d1bbd959c44bf770a692e916dfd5c71da31fafd756f2` | 4,576 B |
| `metrics.parquet` | `992a3a14dcf770ac4304b548187f28bcdd9e055b96b990282c74a4bd032291f0` | 38,425 / 198,447 B |
| `logs.parquet` | `b582a2e5bb9ee0d07a7f9e64d3a2cdde90640ca6a78b01748ff58110e3261150` | 281,046 / 10,073,827 B |
| `traces.parquet` | `39121200abf15ff5493348a345026aafbfec06dcb0d0a32c4d0e3914c169c0f2` | 420,364 / 26,630,875 B |
| `trace_index.parquet` | `e3cc9b9d8303bf2b504f37e46a0b78db596a1875c0c34be66bc31f3bb5548c30` | 60,102 / 1,939,509 B |
| `topology.json` | `13d21fcf28480219dda9f9af816081a566274dfd9405eb4d65ddf2c0bced03d4` | 126,961 B |

Master 的 `source-validation.json` 与 51 的 `SF-005-transfer-validation.json` 均为 `PUBLIC_SOURCE_VALIDATED`：六文件白名单与 SHA/大小、UTC 纳秒时间戳、30/30 分钟 Metrics/Logs/Traces 覆盖、Trace/Span 唯一身份、Trace Index 与真实 Span 集合、Public/Private 泄漏扫描全部 PASS。原数据有 1 组完全重复 Metrics 行和 21,986 个未解析父 Span；它们作为源特性记录并保留，没有删改或用 Private 数据修复。

## 迁移、兼容转换与注册

51 导入前不存在 `/data/aiops-contest/public/manifests/SF-005.json` 或 SF-005 Case 目录。Master→51 TCP/22 不通，本次通过现有已验证主机身份的 Mac 双 SSH 控制通道，以 `scp -3` 仅中转上述六个 Public 文件，Mac 未持久保存数据，也没有写入密码或 Token。

51 root-only 原始目录：

`/data/aiops-contest/staging/SF-005-incoming-20261017/`

51 使用现有 `validate_exchange_v2.py` 校验 PASS，之后以 `prepare_exchange_v2.py` 生成兼容暂存：

`/data/aiops-contest/staging/SF-005-compat-public-v1-20261017/`

现有 `import_case.py --validate-only` 先得到 `VALIDATED_ONLY`、四类行数与源一致；随后正式导入器以 `PUBLISHED` **登记数据目录**。这里的 `PUBLISHED` 是内部 Case 存储状态，不等于队伍题目发布。最终路径：

- Case：`/data/aiops-contest/public/cases/SF-005/public-v1/`
- Manifest：`/data/aiops-contest/public/manifests/SF-005.json`

最终 Manifest SHA-256：`283dd2566691a56e3f74efeda13e2d60cd17d7c368345203451ee89f787c5733`，其中 `source_manifest_sha256` 精确等于 Master 源 Manifest。归一化后的 Parquet 因稳定排序/Zstd 重编码而有不同文件哈希，但行数保持 Metrics **38,425**、Logs **281,046**、Spans **420,364**、Trace Index **60,102**。`validate_case.py` 从最终路径读回 `PASS`。新 Case 目录为 `0750 root:aiops-api`，Manifest/Parquet 为 `0440 root:aiops-api`；原始暂存仍为 `0700 root:root`。导入器初始产生的 `root:root 0700` 新目录曾阻止 API 账号遍历；仅对新建的 SF-005 目录和文件按现有 SF-003 权限规则调整，文件字节未变。

## 51 端查询与安全验证

`aiops-api` 身份直接调用运行中 API 使用的 `CaseStorage` 与组织者 Public 读路径：

| 查询 | 51 Public 结果 |
| --- | ---: |
| Q04 Recommendation 服务端 Span | 3,331 |
| Q05 Recommendation ERROR 日志 | 9 |
| Q06 Frontend → Product Catalog 错误 Span | 58 |

Metrics 查询、Trace 列表与 Waterfall、Frontend Pod 指标曲线和调用边也均非空；四个归一化 Parquet 的实际 SHA/大小与最终 Manifest 一致。可复核的成功记录在 51：

`/opt/aiops-contest-server/reports/import-20261017/SF-005-backend-query-validation-v4.json`

验证脚本先后有两次**脚本执行环境**失败：第一次因 root-only 报告目录不可由 `aiops-api` 读取脚本；第二次因 `app` 模块同名导致导入冲突。两次均发生在数据查询之前，原失败输出保留，随后在新的 `v3`/`v4` 文件中验证 PASS。没有重导或覆盖 SF-005。

HTTP `/healthz` 返回 200；匿名 SF-005 metadata 与 traces 请求仍返回 401。原因是队伍访问门禁未开放，本轮没有使用队伍 Token 绕过它。因此**已验证 API 存储后端可读，但没有宣称选手 HTTP 端到端请求现在返回 200**。

比赛状态文件 SHA-256 仍为 `8c42b7af13079334d90d719bc6eb9e5826a2ea0b4303d4d7e45185360a992fd8`：practice=`CLOSED`，semifinal `published_cases=[]`，AM/PM=`NOT_OPEN`。SF-001、SF-002、SF-003、SF-010 的导入 Manifest SHA 前后完全相同。Nginx 账号不能直接读取新 Case，API 服务账号可读；未导入 Private 注入计划或 GT，正式 GT 目录没有 SF-005 已批准文件，历史评分未改。

**后续**：待组委会按既有流程审核 GT、受控导入查询绘图 Question 包并开放对应场次后，才能做选手 Token 的 HTTP 端到端测试。本次只消除了 Q04～Q06 所需 SF-005 Public 监控数据缺失这一阻断。
