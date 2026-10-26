# 私有评测服务

本模块只部署在组织方可信环境。它读取审核 GT、执行确定性匹配和 40+20+20 评分，并仅将已经通过 Public 数据真实性核验的证据交给 LLM 计算最后 20 分。

## 启动

演示 unit 为 `aiops-private-grader-demo.service`，监听 `127.0.0.1:9001`。配置文件 `/etc/aiops-private-grader/grader.env` 必须由 `root:aiops-grader` 持有并设置 `0640`：

```text
PRIVATE_GRADER_GT_DIR=/etc/aiops-private-grader/gt
GRADER_SHARED_TOKEN=<随机内部凭证>
GPT_BASE_URL=<OpenAI-compatible chat completions URL>
GPT_MODEL=<经管理员确认的准确模型标识>
GPT_API_KEY=<密钥>
GPT_TIMEOUT_SECONDS=30
GPT_MAX_OUTPUT_TOKENS=500
GPT_RETRIES=2
```

禁止将环境文件加入 Git、复制到 50 机器或写入报告。

```bash
systemctl daemon-reload
systemctl enable --now aiops-private-grader-demo
curl -fsS http://127.0.0.1:9001/healthz
```

`POST /internal/v1/grade` 需要内部 Bearer 凭证，不由 Nginx 对外暴露。当前 loopback 部署避免了明文跨主机通信；迁移到独立可信主机时必须增加经过批准的 TLS/mTLS 或等价受保护链路，并配置最小网络 ACL。

## GT 文件

每个 Case 一个 `<case_id>.json`。最低结构：

```json
{
  "case_id": "CASE-ID",
  "classification": "OFFICIAL_PRIVATE_GT",
  "review_status": "APPROVED",
  "gt_version": "gt-1",
  "rubric_version": "rubric-1",
  "time_tolerance_seconds": 120,
  "events": []
}
```

每个事件必须包含 `gt_event_id`、`fault_type`、`entity_type`、`entity_id`、`accepted_entity_aliases`、`accepted_fault_type_aliases`、`observable_start_time`、`observable_end_time` 和 `evidence_rubric`。

`DEMO-001.json` 标记为 `SYNTHETIC_DEMO_ONLY`，只接受网关传入的 DEMO 模式，绝不能进入正式榜单。当前没有 SF-001 的 APPROVED GT。

## LLM Judge 约束

- 不参与事件匹配，不改类型、实体或时间分；
- 只接收已核验证据和单事件 Rubric；
- 固定低随机性请求、输出 JSON、有限重试和并发；
- `FULL=1.0`、`PARTIAL=0.5`、`NONE=0.0` 严格绑定；
- 超时、HTTP 错误、非法 JSON、矛盾比例和越界值均转为待评测；
- `requires_human_review=true` 时不产生正式分数。

单元测试通过伪造响应覆盖 FULL、比例矛盾、越界、非法 JSON、超时和 Prompt Injection 数据边界。
