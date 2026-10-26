# 院智能运维技能大赛：决赛 Web 组委会操作指南

## 1. 访问与健康检查

Web 地址：

`http://192.168.51.50:8088/`

服务检查：

```bash
systemctl status aiops-agent-web --no-pager
curl -sS http://192.168.51.50:8088/api/health | python3 -m json.tool
```

重启：

```bash
systemctl restart aiops-agent-web
journalctl -u aiops-agent-web -n 100 --no-pager
```

当前项目目录：`/opt/aiops-competition/participant-starter/`。运行记录保存在项目 `runs/` 和 Web state root；不要在比赛现场手工删除。

## 2. 进入决赛上半场

1. 打开 Web，点击左侧“决赛上半场”。
2. 确认页面显示身份和开放状态。`NOT_OPEN` 时普通队伍不能运行；只有 51 明确授予的 `internal_preview` 身份可内部联调。
3. 从“决赛任务”下拉框选择 FT-01、FT-02 或 FT-03。Case 与 UTC 窗口由 51 下发，不手工输入。
4. 选择 Baseline 或 LLM Agent，按需开启 Skill/MCP。
5. 点击“启动真实 Agent”。不要重复点击；运行中可用停止按钮结束子进程。
6. 页面顶部的三题卡片会显示已运行模式、异常数和完成状态；分数只显示 51 实际返回的值。

## 3. 观看调查过程

中间区域依次显示：

- 当前步骤、分析耗时、Public API 请求数和 provider token usage。
- 调查时间线：API、工具、Skill、MCP、校验和最终输出。
- 服务拓扑：仅由当前任务 Public topology 生成。
- 多模态证据：Metrics、Logs、Trace Waterfall、异常与证据、异常图表。
- 右侧结果卡：异常实体、现象类型、Case、Final Task、证据校验和提交/评分。

Token 为“未提供”时表示服务商没有返回 usage，不等于 0。

## 4. 查看三题结果与评分

1. 从左侧“历史运行”选择对应 FT run。
2. 页面会明确标记“回放/延迟回放”，不应对外宣称为实时调查。
3. 查看右侧“提交/评分”。若为 `--`，表示未实际提交或服务端没有返回成绩。
4. 详细原始评分以 run directory 中的 `submission.json` 为准，不以截图或口头报分为准。
5. `SUBMISSION_LIMIT_REACHED` 表示服务端的正常次数限制，禁止更换身份或修改幂等性 Key 绕过。

## 5. 进入 PK 页面

1. 点击“决赛 PK”。
2. 检查“当前轮次”。若显示 `NOT OPEN`，说明 51 未创建/开启活动轮次，50 无权自行开放。
3. 从下拉框选择当前身份可见的 PK 任务。正式现场必须以 51 当前 Round/Task API 的返回为准。
4. 选择 Agent 后启动，观看调查时间线、两种以上证据、图表、耗时和请求数。
5. 正式 PK 不应向参赛队伍页面展示对方未提交内容或私有评委分。

## 6. 如何开启 PK 轮次

50 Web 是 Agent 运行/展示端，不是 51 的比赛控制平面。开启轮次必须由有权限的 51 管理员使用已审核的管理流程，完成：

1. 选定 round/task、两支队伍、统一 Case 和 UTC 窗口。
2. 确认两队 Token 只能查看本轮授权数据。
3. 开启 Round 后用 `GET /v1/final/pk/rounds/current` 检查 51 实际状态。
4. 重新加载两台选手 Web，确认任务、窗口和轮次一致。

本指南不给出伪造的开启按钮或未经授权的管理 API。

## 7. 双队现场展示

正式验收时使用两台独立选手 VM、两个队伍 Token 和两个独立 run directory。建议投屏方式：

- 左半屏：擂主 Web。
- 右半屏：挑战者 Web。
- 组委会单独的只读投屏页：只显示 51 允许发布的轮次和锁定结果。

当前 50 只有本机队位，界面的“未连接第二台 VM”是真实状态，不得在演示时解读为已完成双队联调。

## 8. 查看评分、确认胜者和切换下一轮

1. 等待两队提交进入服务端终态，不以前端本地完成作为评分完成。
2. 在组委会权限视图中核对 Submission ID、状态、自动核验和已公布评委分。
3. 只有 51 已锁定两份答案后，才由有权限的轮次管理员确认胜者。
4. 胜者确认后重新读取 current round，确保 Web 显示服务端已确认结果。
5. 再由 51 管理员创建/开启下一轮，两台 VM 重新加载并核对新的 round ID、task ID、Case 和时间窗口。

50 Web 不会也不应自行确认胜者、修改评委分或开启下一轮。

## 9. 赛前检查清单

- [ ] 51 Final Task/PK Round 状态经总裁或管理员确认。
- [ ] 两台选手 VM 使用不同队伍身份，无组委会 Token。
- [ ] 两台 Web `/api/health` 均为 ok。
- [ ] 任务下拉框、Case、UTC 窗口和 round ID 一致。
- [ ] 试运行不读取 organizer-reference、GT 或本地 Parquet。
- [ ] 投屏屏幕以 1920×1080 实机检查字号、图表、中文字体和全屏按钮。
- [ ] 备用 Baseline 可在 LLM 不可用时独立运行。
- [ ] 确认 429 退避与总超时，不在现场重复连点启动。
- [ ] 确认回放标识，不将历史动画宣称为实时 Agent。
- [ ] 准备记录 Submission ID 和服务端评分 JSON 的表格。

