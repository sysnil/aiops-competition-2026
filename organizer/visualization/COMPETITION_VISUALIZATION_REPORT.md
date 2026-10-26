# 组委会赛题可视化部署报告

## 部署结果

已在 `192.168.51.51` 独立部署只读 Streamlit/Plotly Web：

- 代码：`/opt/aiops-competition-visualization/`
- 服务：`aiops-competition-visualization.service`，开机启动
- 监听：`127.0.0.1:8502`，不在 Nginx/8080 发布
- 启动/检查：`systemctl start aiops-competition-visualization`、`systemctl status aiops-competition-visualization`
- 组委会访问：先执行 `ssh -N -L 18502:127.0.0.1:8502 root@192.168.51.51`，然后打开 `http://127.0.0.1:18502`

旧版来源为靶场 Master 的 `/root/stage5-v2-case-explorer/app.py`，技术栈是 Streamlit、Plotly、Pandas，原来使用 Stage5-v2 DEV 的专用缓存。本版复用了时序图、服务依赖 Sankey、Trace Waterfall、实体/时间筛选的交互方式，重新实现了比赛 Manifest 发现与 DuckDB/Parquet 只读查询。未复制旧 DEV 缓存，也未复制、重建或修改 51 上的比赛原始数据。

## 数据适配

51 上的 Public 根目录为 `/data/aiops-contest/public`。已导入题目的 Manifest 位于 `manifests/SF-xxx.json`，数据位于 `cases/<case_id>/<version>/`；SF-001 版本为 `public-v1`。只有 Manifest 标记 `published` 且 `PUBLIC_COMPETITION_DATA`，并具备四个声明文件时，Web 才判定数据就绪。新题导入后刷新页面即可切换，无须重部署。

| 数据 | 实际字段/关联 | SF-001 行数 |
| --- | --- | ---: |
| `metrics.parquet` | `timestamp, metric_name, value, labels, service, pod, node, unit, metric_type, source` | 80,358 |
| `logs.parquet` | `timestamp, service, pod, severity, body, attributes, trace_id, span_id` 等 | 567,346 |
| `traces.parquet` | `trace_id, span_id, parent_span_id, parent_service, service, operation_name, start_time, duration_ms, status, resource_attributes` 等 | 817,529 |
| `trace_index.parquet` | `trace_id, start_time, span_count`；以 `trace_id` 关联完整 Span | 119,091 |

Public Topology 直接读取 Manifest 内 11 份 Kubernetes 身份/放置快照与已观测的 Service 调用边。SF-001 窗口为 **2026-10-14 09:12:25.156656–10:12:25.156656 UTC，整 60 分钟**。四个 Parquet 文件的实际 SHA-256 和大小均与 Public Manifest 一致；半开窗口查询行数与 Manifest 逐项一致。实际 SF-001 最大 Trace 为 160 Span，低于本版 5,000 Span 的 Waterfall 查询上限。

| 题号 | 51 上的数据状态 |
| --- | --- |
| SF-001 | READY，真实 Public Metrics/Logs/Traces/Topology 可查询 |
| SF-002 | 数据未就绪，未导入 51 |
| SF-003～SF-009 | 数据未就绪，正式题尚未发布 |
| SF-010 | 数据未就绪；Master 上已有 Public 导出，但**本次没有导入或发布到 51** |
| SF-011～SF-012 | 数据未就绪，正式题尚未发布 |

Master 的 SF-010 导出路径是 `/data/stage5-v3/ops-competition-2026/exports/SF-010-ATTEMPT-07/public/`，不是 51 本地目录；Web 不会跨主机直接读取它。后续应通过既有导入/校验流程导入 Public bundle 后再显示。

所有视图共用侧栏 UTC 时间窗、Service、Pod 与 CallEdge 筛选。Metrics 按分钟聚合，`*_total` 计数器展示相邻分钟差分速率并略过重置；Pod-only 指标通过 Public 拓扑映射 Service。Logs 提供级别分布、每分钟数量、关键字和分页原文；未知级别记 `UNKNOWN`。Trace 列表从 Index 获取 ID，单击表格行或选下拉框后读取该 ID 的完整 Span Waterfall；依赖图由归档的 `parent_service → service` 生成。CallEdge 选择限定 Trace/拓扑，并在未指定 Service/Pod 时将相关两端用于 Metrics/Logs 过滤。

审核员可在侧栏**临时输入**候选类型、实体与起止分钟。区间会叠加在 Metrics/Logs 图上；Review 页展示相同窗口的相关 CallEdge、Logs、Trace 以及较早窗口对比。候选信息只在当前浏览器会话使用，不读写 Private GT、注入记录或评分规则，不提供 `APPROVED` 操作。

## SF-001 截图与验收

以下截图均由 51 的 Linux 无头浏览器访问 `127.0.0.1:8502` 实际生成，不是旧靶场截图：

![SF-001 Metrics](reports/screenshots/SF-001-metrics.png)

![SF-001 Trace Waterfall](reports/screenshots/SF-001-waterfall.png)

![SF-001 审核区间](reports/screenshots/SF-001-review.png)

其余截图：`reports/screenshots/SF-001-overview.png`、`SF-001-logs.png`、`SF-001-topology.png`。浏览器验收无页面错误；CPU 时序图非空，Trace 表格行点击可切换 Waterfall；SF-002 明确显示“数据未就绪”。原始校验、查询、目录边界单元测试 4/4 PASS，六视图 Streamlit 执行无异常。

## 安全与现有服务

Web 以 `aiops-api` 账号运行，但 systemd 挂载命名空间只允许读取 Public 归档，并将 `/etc/aiops-contest`、原 API/评分项目、staging、mock 与索引目录设为不可访问。已在服务进程的挂载命名空间实测：Public Manifest 可读；Token、competition state、grading 和 staging 均不可读。端口只监听 `127.0.0.1:8502`，`IPAddressDeny=any` 且仅放行 localhost；访问 `192.168.51.51:8502` 连接被拒绝。未从具体参赛队伍主机另做探测，但非回环监听不存在，队伍网络没有 Web 入口。

原 `aiops-contest-api.service`、`nginx.service` 和新服务均为 active；原 API `/healthz` 返回 200，练习接口在未授权请求下仍返回 401。没有修改赛题开放状态、Token、SQLite、提交服务、Private GT、评分逻辑或四个原始 Public Parquet 文件。

## 尚未解决

- SF-002～SF-012 尚无 51 上可用的 Public 导入；SF-010 只能在完成既有校验导入后切换。
- Private GT 草案未接入。本版仅允许审核员手动输入候选区间；如需自动读取 GT，必须另建严格的组委会权限服务，不能复用当前 Public Web 的读边界。
- 大规模筛选使用限量查询：Logs 每页 300 条，Trace 列表展示最新 120 个 ID；仍可缩小时间/实体范围查看所需数据。

## 后续更新：异常类型与选手手册对齐

Public 页的会话内人工候选类型现从比赛服务器已发布的 `anomaly-taxonomy-v1.json` 同步为 11 个类型 ID 和中文名称，不再使用 `Latency / Errors / Resource` 等泛称。该 taxonomy 副本的 SHA-256 为 `e972da4a2f82407fb30f985463e61d6943ce3fda043ef76846b03be71ca7282a`，与 51 上选手 Schema 所用文件一致。`OTHER_OBSERVABLE_ANOMALY` 需填写具体可观测偏差；人工候选仍只存在当前浏览器会话，不写入 GT。

Private GT 的只读展示现由**另一个**需口令的组委会服务 `/opt/aiops-competition-review/` 提供，未放宽本 Public 服务的文件读取边界。当前 SF-001 的 13 条 DRAFT 预览事件全部属于已发布的 11 类，其详情见独立的 `REVIEW_OVERLAY_REPORT.md`。
