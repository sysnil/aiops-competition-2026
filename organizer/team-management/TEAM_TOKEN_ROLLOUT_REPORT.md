# 17 支队伍 Token 与 SSH 交付报告

状态：`TEAM_IP_TOKEN_ACCESS_AND_SCP_VERIFIED`（2026-10-20）。51 服务器已向 `192.168.51.52`–`192.168.51.68` 分别交付 TEAM-01–TEAM-17 的现有独立 Token。未开放任何比赛场次或题目。

## 映射与交付

| 队伍范围 | 对应 IP | Token 文件 | 结果 |
| --- | --- | --- | --- |
| TEAM-01–TEAM-17 | `.52`–`.68`，队伍号每加一 IP 加一 | `/etc/aiops-competition/team.env` | 17/17 已安装、哈希回读通过 |

51 权威 Registry SHA-256：`699067188a30eaf8c09ed92a677565872d9b3415794b21f53d8c134292bafa62`。root-only 凭据包 SHA-256：`234d4d1b6ef012836e1fdbc9b1083a6ff58d2439e7e05806434481eeb9236c04`。批量交付使用既有 `token_dispatch.py --all --apply`，先全量预检再逐台写入；第二次执行为 17/17 `ALREADY_CURRENT`。审计文件为 51 的 `/var/lib/aiops-contest/team-token-delivery/delivery-audit.jsonl`（0600），不含明文 Token。目标文件由脚本核验为 `root:aiops-web`、0640。

## 克隆身份修复与 SSH

交付前只读核对发现：17 台 MAC 唯一、无残留 Token，但 SSH 主机密钥及 machine-id 均为模板复制值。每台旧身份文件备份到该机的 `/root/aiops-team-provision-backup-20261020/`（0700），然后分别生成新 ED25519/ECDSA/RSA 主机密钥和 machine-id。17 台重启后 Web 全部恢复 `active`；SSH 主机密钥和 machine-id 均为 17/17 唯一。

51 新建 root 管理密钥 `/root/.ssh/id_ed25519`（0600），公钥指纹 `SHA256:OL8K4V+kMOiiL6tMs3ePlrfvNvu2IVJv7KFZw8NU6sc`。公钥在各目标机只允许来源 `192.168.51.51`，禁用代理和端口转发。51 的 `/root/.ssh/known_hosts` 固定了 17 台独立主机密钥。17/17 非交互公钥登录通过；51 向每台 SCP 上传测试文件、哈希回读及清理也全部通过。由 51 传文件示例：

```bash
scp /path/to/file root@192.168.51.52:/tmp/
```

51 的管理私钥不得复制到选手机器、选手工程或公开文档。原有共同 root 密码未改动。克隆机主机名仍为 `cnic`；队伍身份以 51 Registry 的 IP/Token 绑定为准，不依赖主机名。

## 授权与隔离验证

17 台均从各自绑定 IP 向 51 `GET /v1/practice/cases` 发出只读请求，得到 HTTP 403 `PRACTICE_NOT_OPEN`，表明已通过 Token 与源 IP 校验但练习场次仍关闭；17 台 Web 健康接口均显示 `team_token_configured=true`。51 自身持 TEAM-01 Token 请求同一接口得到 HTTP 403 `SOURCE_IP_MISMATCH`。未开放 Practice、Semifinal 或 Final，未修改评分或 GT。

52 的旧 SSH 主机密钥在这台 Mac 的 `~/.ssh/known_hosts` 中已替换为经 51 验证的新指纹 `SHA256:NT/gjE5gE/7a38jB93qcmxs5TNIoW7ft8hit5qVR3to`；原文件备份为 `~/.ssh/known_hosts.pre-team-rollout-20261020`。本次核对时 Mac 的 VPN 路径不能直连 52，故 Mac 侧尚未完成实际新 SSH 会话测试；51 到全部 17 台的连接和文件传输均已实测通过。
