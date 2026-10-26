# 智能运维技能大赛比赛数据服务器

这是比赛数据服务器工程，技术栈为 FastAPI + DuckDB + Parquet + Nginx。目前已发布
SF-001 Public 数据，并增加经过认证的答案提交网关。服务器没有 SF-001 Private GT 或
故障注入计划；自动评分模块的部署、权限和状态见 `grading/README.md`。

## 服务地址与安全边界

- FastAPI：`127.0.0.1:8000`
- Nginx：`192.168.51.51:8080`（仅批准的比赛内网）
- OpenAPI：`http://127.0.0.1:8080/docs`
- systemd：`aiops-contest-api.service`
- API 用户：`aiops-api`，无登录 Shell、无 sudo
- 发布数据对 API 进程只读；Staging 仅 root 可访问
- 公共数据接口匿名只读；只有 `/v1/submissions` 允许受认证 POST
- 未开放公网；私有演示评测服务只监听 `127.0.0.1:9001`

## API

Public 查询接口无需 Token：

- `GET /healthz`
- `GET /v1/cases`
- `GET /v1/cases/{case_id}/metadata`
- `GET /v1/cases/{case_id}/topology`
- `GET /v1/cases/{case_id}/metrics`
- `GET /v1/cases/{case_id}/logs`
- `GET /v1/cases/{case_id}/traces`
- `GET /v1/cases/{case_id}/traces/{trace_id}`

评测接口：

- `GET /v1/anomaly-taxonomy`（半决赛 v3 可观测异常类型）
- `GET /v1/fault-taxonomy`
- `POST /v1/submissions`（必须使用每队独立提交凭证）
- `GET /v1/submissions/{submission_id}`（只能查看本队提交）

查询时间不得超过题目的一小时窗口；分页、最大行数、查询超时、每队频率、每队并发和总查询并发均在 `/etc/aiops-contest/config.yaml` 中配置。客户端不能提交 SQL 或文件路径。

## 标准数据 Schema

Metrics：`timestamp, metric_name, value, labels, service, pod, node, unit, metric_type, source`。`metric_type` 保留 Counter/Gauge/Histogram 语义，原始值与 API 聚合结果明确区分。

Logs：`timestamp, service, pod, severity, body, attributes, trace_id, span_id`。不存在的关联字段应保留为空，禁止编造。

Traces：`trace_id, span_id, parent_span_id, parent_service, service, operation_name, start_time, duration_ms, status, attributes, resource_attributes`。导入时按 Trace ID 排序、使用受控 Row Group，并生成 `trace_index.parquet`，避免完整 Trace 查询无条件全量扫描。

## 管理操作

```bash
systemctl status aiops-contest-api nginx
journalctl -u aiops-contest-api -f
/opt/aiops-contest-server/.venv/bin/python /opt/aiops-contest-server/scripts/validate_case.py CASE_DIR MANIFEST
```

`manage_tokens.py` 支持创建和禁用 Token。服务端 Token 数据库只保存带独立 salt 的 scrypt 哈希。`mock-client-tokens.json` 是 root-only 的本机压测夹具，不是正式 Token，也不得下发给队伍。半决赛新答案使用独立的 v3 Schema 和 Anomaly Taxonomy；历史 v2 故障类型答案仍可解析、查询和复核，但不会被静默转换为 v3。

正式数据通过 `import_case.py` 在 Staging 中校验 SHA-256、Schema、时间窗口、重复数据、Trace 关系、跨模态关联和私有字段，全部通过后原子发布。输入包不会被删除，已发布版本不会被静默覆盖。

## 客户端示例

```bash
export AIOPS_ENDPOINT=http://127.0.0.1:8080
export AIOPS_TOKEN='管理员提供的 Token'
PYTHONPATH=/opt/aiops-contest-server \
  /opt/aiops-contest-server/.venv/bin/python /opt/aiops-contest-server/examples/query_metrics.py
```

Mock 测试和 Mock 压测通过不代表正式一小时全量数据已经完成性能验收。首道真实样板题导入后必须重新执行 17 队并发测试。
