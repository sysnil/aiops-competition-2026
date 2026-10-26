# 自动提交与评测系统部署说明

## 1. 架构与边界

系统由三部分组成：

1. `192.168.51.50` 的 `submit.py`：本地校验答案、计算规范 JSON SHA-256、提交并轮询结果。
2. `192.168.51.51` 的评测网关：复用现有 FastAPI，经 Nginx 仅开放受控提交路径；负责队伍认证、Schema 校验、Public 证据回查、SQLite 历史和异步任务。
3. 私有评测服务：本次合成演示服务仅监听 `127.0.0.1:9001`，以独立 `aiops-grader` 用户读取合成 GT。网关用户不能读取 GT。正式比赛应将该服务迁移到经授权的可信环境。

公开 `/v1/cases` 查询仍匿名且只读。提交凭证与查询 API 的 `X-Team-ID` 完全分离；自报队伍 ID 不能用于提交认证。

## 2. 目录和权限

- 程序：`/opt/aiops-contest-server/grading/`
- 提交数据库：`/var/lib/aiops-grading/submissions.sqlite3`
- 原始答案：`/var/lib/aiops-grading/answers/`
- 提交凭证哈希：`/etc/aiops-contest/submission_tokens.json`
- 网关环境：`/etc/aiops-contest/grading-gateway.env`
- 私有评测环境：`/etc/aiops-private-grader/grader.env`
- 私有 GT：`/etc/aiops-private-grader/gt/`

答案和 SQLite 由 `aiops-api` 拥有，权限为 `0640`；私有 GT 和模型配置为 `root:aiops-grader 0640`。API 服务账号不属于 `aiops-grader` 组，因此不能读取 GT 或模型密钥。

## 3. 对外接口

### `GET /v1/anomaly-taxonomy`

返回半决赛公开、版本化的可观测异常类型。无需提交凭证。`OTHER_OBSERVABLE_ANOMALY` 必须人工复核，不能用作规避枚举的万能分类。

### `GET /v1/fault-taxonomy`

保留给历史 v2 答案和后续根因定位赛制，不作为半决赛 v3 的评分分类。

### `POST /v1/submissions`

必须带 `Authorization: Bearer <每队独立提交凭证>`。请求包含：

- `case_id`
- `schema_version`，半决赛当前为 `3.0`；历史 `2.0` 仍兼容
- `idempotency_key`
- `answer_sha256`
- `answer`

同一队伍重复使用同一幂等键和同一答案时返回原任务；同一幂等键对应不同答案时返回 409。正式 Case 默认每队每题最多 3 次，DEMO 不计入正式次数。

### `GET /v1/submissions/{submission_id}`

必须使用创建该提交的队伍凭证。其他队伍查询统一返回 404。公开响应只含总分、四项汇总和不泄漏 GT 的版本标识，不返回 GT 事件、隐藏时间、匹配关系或私有 Rubric。

状态：`RECEIVED`、`VALIDATING`、`GRADING`、`PENDING_GT_APPROVAL`、`PENDING_JUDGE`、`PENDING_REVIEW`、`PREVIEW_COMPLETED`、`COMPLETED`、`REJECTED`、`FAILED`。`PREVIEW_COMPLETED` 只表示草案 GT 预评测，不是正式成绩且不进入排行榜。

## 4. Schema 与证据真实性

`schemas/answer-v3.schema.json` 和服务端 Pydantic 模型共同限制：

- 半决赛 v3 单答案最多 16 个事件（与 GT Schema 一致），历史 v2 仍为 10 个；每事件 1～20 条证据；
- 文件由 Nginx 限制为 128 KiB，客户端限制为 120000 字节；
- 时间必须含时区，开始不得晚于结束，并须在 Case 窗口内；
- `task_type` 固定为 `SEMIFINAL_ANOMALY_DETECTION`；
- `anomaly_type` 来自 `anomaly-taxonomy-v1.json`，并要求填写现象描述；
- 禁止在 v3 中提交 `suspected_fault_type`，避免把注入机制或根因当成半决赛目标；
- 实体、字段数、字段名和记录长度有界；文件路径、SQL、命令字段被拒绝；
- 重复证据被拒绝，不能用重复记录放大证据质量。

服务端不信任 `request_id`。每条 evidence 以稳定 `record_sha256` 回查对应 Public Parquet 记录，再逐字段比较提交摘要。字段顺序不影响摘要；NULL、UTC 时间和浮点值按服务端规范化规则处理。验证后的完整记录才会传入私有 Judge。

## 5. 评分规则

正式评分只接受 `review_status=APPROVED` 且事件集合非空的 GT。未审核 SF-001/002 草案只能进入隔离的 `PREVIEW` 流程，并返回 `PREVIEW_COMPLETED`。正式 GT、预览 GT、Public 数据和答案历史位于彼此隔离的目录。

v3 事件采用确定性最大权重一对一匹配。实体和时间是主要匹配依据，异常类型仅作同等候选的弱排序；因此分类不完全准确时，正确的实体和时间仍可独立得分。同实体同时间的不同可观测现象不会互相去重。

- 异常发现与现象识别：40 分；
- 异常实体：20 分；
- 时间：20 分，默认两个边界各 10 分，容差 ±120 秒；
- 证据质量：20 分，仅此项由 LLM 输出 `FULL/PARTIAL/NONE`；
- 漏报按 GT 数量归一化为 0；独立多报每条扣 5 分；完全重复候选去重且不重复扣分。

历史 v2 仍使用原来的故障类型分项和历史规则，数据库迁移会保留既有记录、版本字段和公开查询结果，不做隐式改写。

Judge 超时、非法 JSON、矛盾等级、越界值或服务不可用时，结果为 `PENDING_JUDGE`，不会自动给 0 或满分。需要人工复核时为 `PENDING_REVIEW`。

## 6. 模型配置

私有服务从环境变量读取：

```text
GPT_BASE_URL=
GPT_MODEL=
GPT_API_KEY=
GPT_TIMEOUT_SECONDS=30
GPT_MAX_OUTPUT_TOKENS=500
GPT_RETRIES=2
```

密钥不得写入源码、报告、Shell 历史或 Nginx 日志。系统 Prompt 将答案与遥测声明为不可信数据，输入被长度限制并置于 `DATA_START/DATA_END` 中；响应经过固定枚举、比例、布尔值和长度校验。

## 7. 服务管理

```bash
systemctl status aiops-contest-api aiops-private-grader-demo nginx
systemctl restart aiops-contest-api
systemctl restart aiops-private-grader-demo
nginx -t && systemctl reload nginx
journalctl -u aiops-contest-api -n 100 --no-pager
journalctl -u aiops-private-grader-demo -n 100 --no-pager
```

API 重启后会自动重新入队 `RECEIVED/VALIDATING/GRADING` 任务。私有服务与网关以独立随机共享凭证认证，且仅通过 loopback 通信。

## 8. 测试

```bash
cd /opt/aiops-contest-server
/opt/aiops-contest-server/.venv/bin/python -m unittest discover -s grading/tests -v
curl -fsS http://127.0.0.1:8000/healthz
curl -fsS http://127.0.0.1:9001/healthz
```

自动化测试使用合成 GT；SF-001 草案仅可由显式的只读构建脚本复制为带 `DRAFT_PREVIEW_ONLY` 标识的隔离预览 GT。构建前后核对源文件 SHA-256，绝不覆盖、批准或改写源草案。

## 9. 正式 GT 审核接口

正式 GT 是受控 JSON 文件，不通过公共 API 上传。组织方应执行双人审核，并确认：

1. `case_id` 与发布题目一致；
2. `review_status` 为 `APPROVED`；
3. `gt_version`、`rubric_version` 和容差已冻结；
4. 每个事件具有唯一 ID、可接受实体别名、可接受类型别名、可观测时间和证据 Rubric；
5. 文件放入私有目录并设为 `root:aiops-grader 0640`；
6. 审核记录保存在公共网关之外。

未满足以上条件时，网关只能返回 `PENDING_GT_APPROVAL`。

## 10. 回滚

本次部署前的文件已保留：

- `/opt/aiops-contest-server/app/main.py.pre-grading-20261015`
- `/etc/nginx/sites-available/aiops-contest.pre-grading-20261015`
- `/etc/systemd/system/aiops-contest-api.service.pre-grading-20261015`

回滚时先停止 `aiops-private-grader-demo`，恢复上述三个文件，执行 `systemctl daemon-reload`、`nginx -t`，再重启 API 和重载 Nginx。SQLite 与答案目录应先归档，不要直接删除，以保留审计证据。
