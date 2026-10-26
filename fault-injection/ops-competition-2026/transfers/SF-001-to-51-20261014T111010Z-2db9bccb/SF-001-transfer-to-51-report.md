# SF-001 Public 数据传输至比赛服务器交接报告

最终状态：**SF001_TRANSFER_READY**。六个 Public 文件已传输、在目标端独立读取校验，并原子切换到暂存路径。本轮仅数据交接；数据库导入、API 发布和参赛样板机访问均未执行。

报告生成时间（Master UTC）：2026-10-14T11:15:21Z。

## 位置与授权

| 项目 | 实际结果 |
|---|---|
| 源服务器 | root@192.168.53.52 |
| 源目录 | /data/stage5-v3/ops-competition-2026/runs/SF-PILOT-01-ATTEMPT-04/public |
| 目标服务器 | root@192.168.51.51（使用用户明确提供的授权账号；凭据不写入报告或文件） |
| 后续 Codex 应读取的具体路径 | /data/aiops-contest/staging/SF-001-incoming/ |
| 源文件数量 / 字节 | 6 / 77,945,722 |
| 目标文件数量 / 字节 | 6 / 77945722 |
| Public Manifest SHA-256 | 8ad8c5eb25e48a33ebf776a40db0f3fbe62284ee7c26df3e4d04139532db0eaa |
| 目标数据盘 | /dev/sdb1 / ext4 / /data |
| 目标盘 UUID | b54b1161-c8ef-43d2-b883-c691824bd84c |
| 目标盘可用空间 | 199407005696 bytes（约 199.407 GB） |
| 目录与文件权限 | root:root；目录 0700；六个文件 0600 |

源目录没有额外文件、软链接或非普通文件。目标目录同样严格为六文件白名单。源 Manifest、大小及数据规模均与指定值一致。没有复制源父目录。

## 网络、传输方式与时间

Mac 到两端的授权 SSH 实际成功，目标主机使用已有 known_hosts 身份校验。Master 直连 192.168.51.51:22 的只读连接检查超时，因此采用 Mac 内存管道中继：Master → 加密 SSH → Mac 管道 → 加密 SSH → 比赛服务器。两端均正常认证，没有修改路由、SSH 配置或绕过认证。Mac 未落盘保存任何 Public 数据文件。

| 时间/性能项目 | 记录 |
|---|---|
| 传输开始（Mac 中继 UTC） | 2026-10-14T11:10:10.779170+00:00 |
| 传输完成（Mac 中继 UTC） | 2026-10-14T11:10:18.269916+00:00 |
| 目标首个文件写入确认 UTC | 2026-10-14T11:10:13.234299+00:00 |
| 目标末个文件写入确认 UTC | 2026-10-14T11:10:20.398679+00:00 |
| 目标原子切换完成 UTC | 2026-10-14T11:12:57.527706+00:00 |
| 传输用时（单调时钟，含准备和逐文件写入校验） | 7.490509 秒 |
| 实际数据量 | 77945722 bytes |
| 有效平均速率 | 10405930.66 B/s，约 10.406 MB/s |
| 源内容审计耗时 | 27.998 秒 |
| 目标独立审计耗时 | 42.610 秒 |

各端 UTC 按原始时钟记录，不用跨机器时间戳差估算传输耗时；速率基于中继单调时钟。完整校验耗时不混入网络传输速率。

临时目录为 `/data/aiops-contest/staging/.SF-001-incoming-20261014T111010Z-2db9bccb.partial`。文件使用排他创建逐个写入，写满后 fsync、重新计算 SHA-256；每个完成文件只在哈希正确后命名。所有六文件完成独立校验后，使用 Linux renameat2(RENAME_NOREPLACE) 原子改名，绝不覆盖已存在目标。临时目录已不再存在，未留下 .part 或 READY 标记额外文件。本轮无断线或重传。

## 六文件逐项 SHA-256 比较

| 文件 | 字节 | 源 SHA-256 | 目标 SHA-256 | 比较 |
|---|---|---|---|---|
| metrics.parquet | 428270 | f23e4f418381b153c6b863444f9a39a88a89033fe9097e846b004dfd4f2cad3f | f23e4f418381b153c6b863444f9a39a88a89033fe9097e846b004dfd4f2cad3f | PASS |
| logs.parquet | 20787419 | 9ec942570e905d120769000ad8cb4f8a74254150209a18468f6cba2172cdc8f2 | 9ec942570e905d120769000ad8cb4f8a74254150209a18468f6cba2172cdc8f2 | PASS |
| traces.parquet | 52632712 | a35452a8ef7dca2db3914c1a840131f1acb202fb74135f3c9511fc2dbb4e1c96 | a35452a8ef7dca2db3914c1a840131f1acb202fb74135f3c9511fc2dbb4e1c96 | PASS |
| trace_index.parquet | 3860927 | acfe812df2f1368e35c67a6e58cd68b15e26c46507e5e578dfc40e29546fa249 | acfe812df2f1368e35c67a6e58cd68b15e26c46507e5e578dfc40e29546fa249 | PASS |
| topology.json | 230380 | 0da34eb05e9b0ff535c4d661bf5bb21362e4c77e1993eeebd89ba583578fcf98 | 0da34eb05e9b0ff535c4d661bf5bb21362e4c77e1993eeebd89ba583578fcf98 | PASS |
| manifest.json | 6014 | 8ad8c5eb25e48a33ebf776a40db0f3fbe62284ee7c26df3e4d04139532db0eaa | 8ad8c5eb25e48a33ebf776a40db0f3fbe62284ee7c26df3e4d04139532db0eaa | PASS |

## Parquet、Schema 与时间窗口

源文件通过 SSH 只读流入 Mac 内存，在已有独立 Python 环境中校验；未运行 RCA 方法。目标端使用已有 `/opt/aiops-contest-server/.venv/bin/python`，仅运行独立 PyArrow 文件校验，不导入应用模块、不连接数据库、不安装或修改依赖。

| 文件 | 实际行数 | Row groups | Header/Footer | 全量解码 |
|---|---|---|---|---|
| metrics.parquet | 80358 | 17 | PASS | PASS |
| logs.parquet | 567346 | 114 | PASS | PASS |
| traces.parquet | 817529 | 164 | PASS | PASS |
| trace_index.parquet | 119091 | 1 | PASS | PASS |

源与目标的 Arrow Schema、字段可空性、UTC 纳秒时间类型和实际时间戳边界逐项一致。Metrics/Logs/Spans 均在 `[2026-10-14T09:12:25.156656Z, 2026-10-14T10:12:25.156656Z)` 内，60 个分钟计数与 Manifest 一致。119,091 个 Trace Index 与 817,529 个 Span 身份及计数完全一致；保留真实 Span 持续时间和索引结束时间，不修改原始数据。未知字段与 NULL 原样保留。

源 PyArrow 版本 `22.0.0`；目标 PyArrow 版本 `26.0.0`。两端全量内容检查均为 `PASS_FULL_CONTENT`，包括 Parquet 全部行和 JSON；没有把注入计划、GT 或控制记录带入六文件包。

## 私有文件排除与暂存安全

明确未传输 injection-plan.json、injection-record.json、anomaly-candidates.json、anomaly-gt-draft.json、review-notes.md、私有 GT、Watchdog/Controller/Worker 调度记录及约 11 GB 原始采集目录。目标暂存目录只含六个明确列出的 Public 文件；校验代码经 SSH 内存执行，未作为第七个文件写入目标。

Nginx 当前只监听 127.0.0.1:8080 并代理本机 API；API 只监听 127.0.0.1:8000。实际 API 使用 aiops-api 用户，数据访问根仅为 public/mock-only，不扫描 staging。staging 为 root:root 0700。已实际验证 aiops-api 与 www-data 对目标 metrics.parquet 读取被拒绝，而已授权 root 可读取。没有改变现有权限配置、发布 Manifest 或开放目录给参赛者。

## 服务与数据保护验证

传输前后 API/Nginx 配置及应用代码 SHA-256、服务 MainPID/启动时间和索引文件 inode/大小/mtime 完全相同。目标 public/cases/SF-001 与 public/manifests/SF-001.json 均未创建；原有 staging 样例保持原位。没有运行 import_case.py，没有连接 DuckDB，没有重启 Nginx/FastAPI 或修改比赛服务配置。未访问 192.168.51.50，未进行故障注入，未删除或修改任何 ATTEMPT 历史资产。

## 交接

下一阶段比赛服务器 Codex 应通过已授权管理员权限读取：

`root@192.168.51.51:/data/aiops-contest/staging/SF-001-incoming/`

本轮只完成传输；下一阶段可依据 READY 状态开始单独授权的导入工作，不把本状态误解为已导入或已发布。

Master 证据目录：`/root/ops-competition-2026/transfers/SF-001-to-51-20261014T111010Z-2db9bccb`。`handoff-manifest.json` 为最终交接状态；transfer-result.json 保留“写入完成、等待独立校验”的中间终态，后续目标校验与原子切换记录完整保留，不覆盖过程记录。

Master 报告：`/root/ops-competition-2026/reports/SF-001-transfer-to-51-report.md`。
