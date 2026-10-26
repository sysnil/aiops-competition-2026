# AIOps Agent Visualization Web

这是现有 `participant-starter/main.py` 的独立可视化层。它不复制 Agent 算法，也不替换 CLI：后端通过无 Shell 的受控子进程调用原 `main.py`，增量读取运行目录中的真实 JSONL，并通过 SSE 推送到 React + ECharts 大屏。

## 访问与服务管理

- 默认地址：`http://<队伍机内网IP>:8088/`
- 50 测试机：`http://192.168.51.50:8088/`
- 启动：`sudo systemctl start aiops-agent-web`
- 重启：`sudo systemctl restart aiops-agent-web`
- 状态：`sudo systemctl status aiops-agent-web --no-pager`
- 日志：`sudo journalctl -u aiops-agent-web -f`

Web 运行文件位于 `/var/lib/aiops-agent-web/runs/`；CLI 原运行文件仍位于项目的 `runs/`，互不覆盖。

## 功能边界

- 半决赛界面可选择新版“查询·图表·三问”或原有“异常检测 Agent”。前者直接展示真实运行生成的 `chart.png` 和三问答案，后者可选 Baseline/LLM、Skill 和 MCP。
- “启动”直接执行现有 `main.py`；“停止”向整个 Agent 进程组发送 SIGTERM，超时才 SIGKILL。
- 运行中展示 API、Tool、LLM、Skill、MCP、证据校验和最终答案事件。
- 历史运行以 4× 速度回放，并始终显示“延迟回放”，不会冒充实时执行。
- 决赛 PK 页面已读取真实 `/v1/final/rounds/current` 状态。没有 OPEN 轮次时明确阻断，不会用动画或伪数据冒充双方对战。正式自主执行需待真实轮次数据、对手身份和可信 Token 计量完成联调后开启。
- Token 只累计模型 API 实际返回的 `usage`；没有 usage 时显示“未提供”。

## 事件 JSONL

每行是独立 JSON：

```json
{
  "seq": 3,
  "ts": "2026-10-16T04:54:07.858408Z",
  "type": "api.request",
  "phase": "OBSERVE",
  "status": "403",
  "source": "agent",
  "delivery_mode": "live",
  "payload": {
    "request_id": "req-0001",
    "method": "GET",
    "path": "/v1/practice/cases",
    "latency_ms": 53.827,
    "status": 403
  }
}
```

`delivery_mode` 只允许 `live` 或 `replay`。LLM 事件不会包含 Prompt、Messages、模型隐藏推理或密钥。

## 标准化复制到其他队伍机

1. 将整个 `webapp/` 复制到该机的 `participant-starter/webapp/`。
2. 安装 `webapp/deploy/aiops-agent-web.service` 到 `/etc/systemd/system/`。
3. 把 unit 中 `--host` 改为该队伍机获批的内网 IP；不要绑定公网地址。
4. 创建系统用户 `aiops-web` 和目录 `/var/lib/aiops-agent-web/runs`。
5. 仅授予 `aiops-web` 对 team/LLM env 文件的组读取权限，文件保持 `0640`，目录保持 `0750`。
6. 执行 `systemctl daemon-reload && systemctl enable --now aiops-agent-web`。
7. 验证 `/api/health`、场次状态、一次真实 Agent 运行、SSE、历史回放和浏览器大屏。

每台机器继续使用自己的 `AIOPS_TEAM_TOKEN` 和选手 LLM 配置，禁止复制 Judge/Private GT 凭证。

## 安全设计

- 浏览器从不接收 Team Token 或 LLM Key。
- 只允许固定的 `main.py` 参数，不拼接 Shell 命令。
- Case ID、问题长度、请求预算和模式均由 Pydantic 校验。
- 单机最多一个活动运行，降低资源争用和重复提交风险。
- 同源访问，无 CORS；CSP、frame、MIME、referrer 和 no-store 响应头已启用。
- systemd 使用专用低权限用户、只读项目目录和只写状态目录。
- Web 不改变 51 的比赛场次、Public 数据、评分器或 GT。
