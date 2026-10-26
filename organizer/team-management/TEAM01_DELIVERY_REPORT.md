# TEAM-01 单机 Token 交付验证

状态：`TEAM01_TOKEN_FILE_DELIVERY_VERIFIED`，不代表 Final 题目已开放。目标为 `TEAM-01 -> 192.168.51.52`，文件 `/etc/aiops-competition/team.env`。本次仅处理这一队；16 个其他队伍未操作。

## 执行与修复

最初在 52 上执行 `stat` 返回文件不存在：当时只完成了 SSH 主机密钥信任，尚未执行 Token 下发。随后用户完成 51→52 的 SSH 公钥认证。51 预检确认 52 网卡持有 `.52`、目标目录为 `root:aiops-web`、`aiops-agent-web.service` 指向预定 Token 路径且目标文件不存在。

首次单机下发工具在写入前失败。只输出错误类型/行号的诊断定位到远端安装分支遗漏 `hmac` 导入；失败期间目标文件和临时文件均未生成。修复代码后，51 的 3 组模拟测试再次通过，重试结果：

- 2026-10-19 15:16:15 UTC：`TOKEN_FILE_DELIVERY_VERIFIED`，`TEAM-01` 状态 `INSTALLED`。
- 52 文件属性：`0640 root:aiops-web`，服务账号 `aiops-web` 可读；安装工具用目标内容 SHA-256 回读核对，未在日志或报告中输出 Token。
- 52 发出带本队 Token 的只读 `GET /v1/final/tasks` 请求，51 记录 `team_id=TEAM-01`、`source_ip=192.168.51.52`、`assigned_ip_match=true`，Request ID `bc25d9e192912fd2148a3076ef8517d5`。HTTP 404 符合当前 Final 未授权/未开放状态，不得解释为 Final 可访问。
- 2026-10-19 15:18:30 UTC：再次执行单机工具返回 `ALREADY_CURRENT`，目标内容未重复替换。

51 Team Registry SHA-256 保持 `699067188a30eaf8c09ed92a677565872d9b3415794b21f53d8c134292bafa62`，私有凭据包 SHA-256 保持 `234d4d1b6ef012836e1fdbc9b1083a6ff58d2439e7e05806434481eeb9236c04`。17 队 Final 权限仍为 0；Practice 为 CLOSED，Semifinal AM/PM 均为 NOT_OPEN。root-only 交付审计位于 `/var/lib/aiops-contest/team-token-delivery/delivery-audit.jsonl`，包括失败尝试与最终成功，不含明文 Token。

52 现已包含 TEAM-01 的 Token、SSH host key 与管理员 `authorized_keys`。后续队伍机器应从下发前的干净模板克隆，或者在隔离网络中逐台清除旧队伍凭据、重新生成机器/SSH 身份并核验 IP，然后再按各队映射下发；不可直接复制当前 52 的完整磁盘作为其他队伍服务器。
