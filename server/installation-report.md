# 院智能运维技能大赛比赛数据服务器安装报告

生成日期：2026-10-12（Asia/Shanghai）  
服务器：`192.168.51.51`  
主机名：`cnic`

## 1. 最终状态

**COMPETITION_DATA_SERVER_PREPARED**

系统依赖、独立数据盘、Mock 数据、只读查询 API、Token 鉴权、安全隔离、限流、SDK 和基本 17 客户端并发测试已通过，等待真实比赛数据。

当前没有真实比赛数据、正式 17 队 Token、HTTPS、跨机器联调、提交系统或评分系统，因此本服务器不标记为 `COMPETITION_FULLY_READY`。

## 2. 服务器资源

- 操作系统：Ubuntu 24.04.2 LTS x86_64
- 内核：6.8.0-58-generic
- 虚拟化：VMware
- CPU：4 vCPU，Intel Xeon Silver 4314 @ 2.40 GHz
- 内存：15 GiB；验收时可用约 14 GiB
- Swap：4 GiB；验收时未使用
- 系统盘：100 GB，根文件系统 96 GiB，约 83 GiB 可用
- 数据盘：200 GB，`/dev/sdb1`，ext4，卷标 `AIOPS_DATA`
- 数据盘 UUID：`b54b1161-c8ef-43d2-b883-c691824bd84c`
- 挂载点：`/data`，约 186 GiB 可用
- 持久挂载：已按 UUID 写入 `/etc/fstab`
- 挂载安全选项：`nodev,nosuid,noexec,nofail`
- IP：`192.168.51.51/24`
- 默认路由：`192.168.51.1`

数据盘是在确认其为空白裸盘后建立 GPT 单分区并格式化。原始 `/etc/fstab` 备份为 `/etc/fstab.pre-aiops-data-20261012`。

没有修改 IP、DNS、默认路由、SSH、安全组或防火墙，也没有执行发行版升级、内核升级或重启。

## 3. 软件环境

- 系统 Python：3.12，保持不变
- 独立 Python：CPython 3.11.17
- uv：0.13.0
- Git：2.43.0
- Nginx：1.24.0
- DuckDB：1.5.6
- FastAPI：0.143.0
- Uvicorn：0.54.0
- PyArrow：26.0.0
- Polars：2.0.0
- pandas：3.0.6
- NumPy：2.4.6
- Pydantic：2.14.1
- HTTPX：0.28.1
- psutil：7.2.2

项目路径为 `/opt/aiops-contest-server`，虚拟环境为 `/opt/aiops-contest-server/.venv`。依赖由 `pyproject.toml` 和 `uv.lock` 完整锁定，没有向系统 Python 全局安装第三方包。

没有安装 Kubernetes、Prometheus、Jaeger、OpenSearch、Redis、GPU 组件或大型模型框架。

## 4. 服务部署与隔离

- systemd 服务：`aiops-contest-api.service`，active/enabled
- Nginx：active/enabled
- FastAPI：仅监听 `127.0.0.1:8000`
- Nginx：仅监听 `127.0.0.1:8080`
- SSH：维持原有 `*:22`
- API 系统用户：`aiops-api`，UID 999，无登录 Shell、无 sudo
- Uvicorn Worker：1 个，避免多 Worker 同时建立高内存 DuckDB 扫描
- systemd 内存上限：6 GiB
- DuckDB 单连接配置：2 线程、768 MB 内存上限
- systemd 自动恢复：`Restart=on-failure`

systemd 启用了 `NoNewPrivileges`、`ProtectSystem=strict`、`ProtectHome`、内核与控制组保护、能力清空、受限地址族等约束。Mock/发布数据对 API 用户只读，Staging 为 root-only；缓存与索引目录单独可写。

意外终止 Uvicorn 主进程后，systemd 已验证能自动启动新进程并恢复健康检查。

## 5. 目录与数据权限

```text
/data/aiops-contest/
├── public/
│   ├── cases/
│   └── manifests/
├── mock-only/
│   ├── cases/
│   └── manifests/
├── staging/       # root:root 0700
├── indexes/       # aiops-api 可写
└── cache/         # aiops-api 可写，含 DuckDB 临时目录
```

服务配置和 Token 哈希位于 `/etc/aiops-contest`，权限为 `root:aiops-api 0750`。Token 哈希数据库为 `0640`。本机 Mock 客户端明文 Token 夹具为 `root:root 0600`，API 用户无法读取。

标准答案、Ground Truth、故障注入时间表、注入命令和私有评分数据均未创建或存储。

## 6. API

OpenAPI 文档由 FastAPI 自动生成。已实现：

| 接口 | 状态 | 说明 |
|---|---|---|
| `GET /healthz` | PASS | 无敏感信息的健康检查 |
| `GET /v1/cases` | PASS | 返回当前 Token 可见的已发布题目 |
| `GET /v1/cases/{case_id}/metadata` | PASS | 只返回公开元数据白名单 |
| `GET /v1/cases/{case_id}/topology` | PASS | 返回公开 Service/Pod/Node 拓扑 |
| `GET /v1/cases/{case_id}/metrics` | PASS | 时间、指标、Service、Pod、Node、聚合和分页 |
| `GET /v1/cases/{case_id}/logs` | PASS | 时间、Service、Pod、级别、关键词、Trace ID 和分页 |
| `GET /v1/cases/{case_id}/traces` | PASS | 时间、Service、状态、操作、调用边和分页 |
| `GET /v1/cases/{case_id}/traces/{trace_id}` | PASS | 利用 Trace 索引获取完整调用链 |

除 `/healthz` 和 OpenAPI 文档外，`/v1` 查询要求 Bearer Token。客户端不能提交 SQL、文件路径或排序字段；SQL 条件均参数化，字段和聚合函数采用服务端白名单。

稳定排序包含时间戳与模态身份字段。默认分页 100 行，单次最多 1000 行，页码最多 10000，一次查询最多覆盖 3600 秒。DuckDB 查询超时为 3 秒，服务端总查询并发为 12，每队并发为 4，每队每分钟 60 次作为 Mock 压测起点。

API 请求日志只记录 request ID、队伍 ID、方法、路径、状态码和耗时，不记录 Authorization Header 或完整 Token。日志泄漏检查通过。

## 7. Token 鉴权

- 每个 Token 使用安全随机 ID、secret 和独立 salt
- 服务端使用 scrypt 哈希并以常量时间比较
- 支持创建和禁用 Token
- Token 记录包含 enabled 和 mock-only 状态
- 17 个现有 Token 全部标记为 `MOCK_ONLY`
- 未生成正式比赛 Token
- Mock Token 仅用于当前本机测试，不应下发给参赛队伍

限流按照队伍身份独立计数，不按照队伍顺序分配固定优先级。当前实现为单 Worker 内存限流，符合当前单实例部署；未来如扩展多实例，需要改为共享限流状态。

## 8. 数据格式

### Metrics

Parquet 字段：`timestamp, metric_name, value, labels, service, pod, node, unit, metric_type, source`。

保留 `Counter/Gauge/Histogram` 类型与来源信息。原始 Counter 不会被自动解释为速率；API 的 `raw/avg/min/max/sum` 结果明确区分。

### Logs

Parquet 字段：`timestamp, service, pod, severity, body, attributes, trace_id, span_id`。

支持时间、Service、Pod、级别、关键词和 Trace ID 查询。缺失关联字段的正式导入数据应保持为空，不能编造。

### Traces

Parquet 字段：`trace_id, span_id, parent_span_id, parent_service, service, operation_name, start_time, duration_ms, status, attributes, resource_attributes`。

导入时保留原始 ID 和时间精度，验证 Parent/Child 关系与重复 Span，按 Trace ID 排序并使用受控 Row Group；同时生成 `trace_index.parquet`。

### Metadata

公开元数据仅包含 Case ID、Schema 版本、时间范围、可用模态、公开服务、公开拓扑、数据来源、Manifest 哈希和分类。API 不返回内部目录、文件路径或任何私有故障信息。

## 9. Mock 数据

- Case：`MOCK-SF-001`
- 活跃版本：`mock-v2`
- 分类：`MOCK_ONLY`
- 窗口：连续 60 分钟
- Metrics：960 行
- Logs：240 行
- Spans：240 行
- Trace 索引：60 个 Trace ID
- 服务：frontend、checkout、payment、database

Manifest、全部 Parquet 文件和 Trace 索引均有 SHA-256。Schema、时间范围、重复数据、Trace 父子关系、日志与 Trace 跨模态关联均已验证。

这些数据完全虚构，只用于功能与性能测试，不是真实半决赛或决赛题目。

## 10. 正式数据导入与冻结流程

`scripts/import_case.py` 实现：

1. 读取输入 Manifest 并验证不可变哈希。
2. 校验各文件 SHA-256。
3. 校验 Case ID、版本和一小时时间范围。
4. 校验 Metrics/Logs/Traces Schema。
5. 检查重复记录、时间越界、Trace 父子关系和跨模态 ID。
6. 拒绝私有故障/答案字段和不安全文件名。
7. 在 root-only Staging 中排序并用 Zstandard 规范化 Parquet。
8. 生成 Trace 索引、行数和新 SHA-256。
9. 完成全部复验后原子发布不可变版本。
10. 已存在的 Case/版本不会被覆盖；失败时保留 Staging 原因和原始输入。

使用合成 `PUBLIC_SAMPLE` 数据执行了 `--validate-only` 测试，状态为 `VALIDATED_ONLY PASS`，未向公开题目目录发布。由于没有真实比赛数据，不能声称完成了真实数据导入验证。

## 11. 测试结果

### 功能与安全

- Pytest：5/5 PASS
- Mock 数据校验：PASS
- SDK Metrics/Logs/Traces 示例：PASS
- Mock Agent 示例：PASS
- 未认证请求：401
- 非法 Token：401
- 未发布 Case：404
- 越界时间：400
- 超大分页：400
- 路径穿越：404
- SQL 注入字符串：作为普通参数处理，未执行
- 长 DuckDB 查询超时与中断：PASS
- 稳定分页重复查询：PASS
- API 用户写入发布数据：拒绝，PASS
- API 用户读取 Staging：拒绝，PASS
- Token 日志泄漏检查：PASS
- 明文 Mock Token 对 API 用户隔离：PASS
- 仅回环监听：PASS
- systemd 异常退出恢复：PASS
- 幂等安装脚本重复执行：PASS
- 重复执行时 Token 哈希保持不变：PASS

### 17 客户端 Mock 并发

- 客户端：17
- 混合请求：204
- HTTP 错误：0
- 错误率：0%
- 吞吐：约 16.0 QPS
- p50：约 1039.7 ms
- p95：约 1511.2 ms
- p99：约 1774.0 ms
- CPU 平均：约 29.6%
- CPU 瞬时峰值：100%
- 内存峰值：约 806 MiB
- 限流测试：48 次 200，随后 17 次 429，PASS

性能测试使用的是很小的 Mock 数据。该结果不代表未来完整一小时真实日志和 Trace 的性能；第一道真实样板题导入后必须重新测试，并根据真实负载调整 60 次/分钟等配额。

## 12. 交付文件

- 安装脚本：`/opt/aiops-contest-server/scripts/setup_server.sh`
- 正式导入：`/opt/aiops-contest-server/scripts/import_case.py`
- 数据校验：`/opt/aiops-contest-server/scripts/validate_case.py`
- Mock 生成：`/opt/aiops-contest-server/scripts/generate_mock_case.py`
- Token 管理：`/opt/aiops-contest-server/scripts/manage_tokens.py`
- 并发压测：`/opt/aiops-contest-server/scripts/benchmark_api.py`
- Python SDK：`/opt/aiops-contest-server/client/`
- 示例：`/opt/aiops-contest-server/examples/`
- 系统清单：`/opt/aiops-contest-server/system-inventory.txt`
- 验收记录：`/opt/aiops-contest-server/validation-results.txt`
- Mock 压测结果：`/opt/aiops-contest-server/mock-benchmark-results.json`
- 导入管线测试：`/opt/aiops-contest-server/import-pipeline-test-results.json`

## 13. 后续待完成

1. 导入并验证第一道真实样板题。
2. 为 17 支队伍生成、审计并安全下发正式 Token。
3. 根据比赛网络环境配置 HTTPS、内网地址和访问控制。
4. 验证 17 台参赛服务器到数据服务器的真实网络联通。
5. 使用正式一小时数据重新执行容量、并发和长查询测试。
6. 对接比赛提交与评分系统。
7. 正式开放前确认云平台安全组和证书策略。
8. 当前主机名与 Machine ID 看起来来自通用克隆模板；制作后续镜像或投入正式环境前，应由管理员流程确保每台机器具有唯一主机身份。该操作本次未执行，以避免未经授权修改主机身份或网络相关配置。

