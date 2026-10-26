# 17 支队伍 LLM Key 分配与验证

状态：`LLM_CONFIG_17_OF_17_VERIFIED / ENDPOINT_17_OF_17_PASS`（2026-10-20）。

源文件为 Mac 下载目录中的 `比赛(1).xlsx`，SHA-256：`959e2d7259d983ef9ba49be74d8d65d4582818569e63efa375c7344445fdac82`。工作表 `Sheet1` 的 A1:A20 包含 20 个互不重复、格式一致的 Key，无标题行。按原顺序将 A1:A17 分给 17 队；A18:A20 保留在原 Excel 中备用，未传至 51 或任何队伍机。本报告和审计不含 Key 明文或其前缀。

| Excel 行 | 队伍 | 服务器 |
| ---: | --- | --- |
| 1 | TEAM-01 | 192.168.51.52 |
| 2 | TEAM-02 | 192.168.51.53 |
| 3 | TEAM-03 | 192.168.51.54 |
| 4 | TEAM-04 | 192.168.51.55 |
| 5 | TEAM-05 | 192.168.51.56 |
| 6 | TEAM-06 | 192.168.51.57 |
| 7 | TEAM-07 | 192.168.51.58 |
| 8 | TEAM-08 | 192.168.51.59 |
| 9 | TEAM-09 | 192.168.51.60 |
| 10 | TEAM-10 | 192.168.51.61 |
| 11 | TEAM-11 | 192.168.51.62 |
| 12 | TEAM-12 | 192.168.51.63 |
| 13 | TEAM-13 | 192.168.51.64 |
| 14 | TEAM-14 | 192.168.51.65 |
| 15 | TEAM-15 | 192.168.51.66 |
| 16 | TEAM-16 | 192.168.51.67 |
| 17 | TEAM-17 | 192.168.51.68 |

配置：`https://yyt.s-flag.com/v1`、`kimi-k3`、`AIOPS_AGENT_LLM_TEMPERATURE=1`。该端点对温度 0 返回 HTTP 400（只允许 1）；温度 1 的第一个 Key 返回合法 HTTP 200。Agent 增加了可选环境变量温度覆盖，其他模型默认温度不变；17 台部署的 `llm_client.py` SHA-256 为 `f3c6209532fff7abde5d1de0e02a6d17e86ea92509ab532835f85cc55026d382`，旧版分别备份于各机器 `agent/backups/temperature-20261020/`。

每队配置原子写入 `/etc/aiops-competition/agent-llm.env`，权限 `0640 root:aiops-web`，父目录 `0750 root:aiops-web`。Web/Agent 以 `aiops-web` 身份读取；Key 经 Mac→51→对应主机的 SSH 标准输入传输，不进入命令参数或 51 的文件。51 只保留不含密钥的 0600 审计：`/var/lib/aiops-contest/llm-key-delivery/audit.jsonl`，共 17 条。17 台配置文件及读回哈希验证通过，17 个运行时 Key 互不相同，17 台 Web 健康接口均为 `llm_configured=true`。

17 台分别从本机向模型端点发送最小 HTTPS 请求，均返回 HTTP 200 且响应结构合法。52 的完整 Web LLM Agent 练习运行 `web-20261020T111205Z-875ecc` 调用模型 1 次、生成 EX-P01 三个答案并自动提交，返回 `PREVIEW_COMPLETED`、10.0/10；这是练习预览成绩，不是正式比赛成绩。该运行的 16 个产物文件经逐文件扫描，未包含本队 API Key。51 队伍 Registry SHA-256 仍为 `fce19e97db840f54d302a5e53d6439201b414aeec3270b449f5eaaf7d73f1ccd`，未因分发变动。

选手在本机 Web 刷新后可选择 **LLM Agent**；网页只显示是否配置成功，不返回 Key。原 Excel 仍保存在 Mac 的 `Downloads`（目录权限 0700）；后续备用 Key 轮换需单独登记并覆盖相应队伍的旧配置，不应直接修改现有文件或将备用 Key 放进选手工程。
