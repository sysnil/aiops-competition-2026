# 队伍 Token、服务器 IP 与赛题权限设计（51，DRAFT）

当前状态：`TEAM_IP_TOKEN_ACCESS_AND_SCP_VERIFIED`。2026-10-20 已完成 TEAM-01–TEAM-17 的正式 Token 文件交付、IP 绑定认证与 51 公钥/SCP 验证，详见 `TEAM_TOKEN_ROLLOUT_REPORT.md`。Token 交付不代表比赛场次开放。下文保留交付前的设计与试发历史，历史状态不再表示当前状态。

## 当前权威映射

51 的 `/etc/aiops-contest/team_registry.json` 中有 17 个正式队伍记录；`/root/aiops-team-credentials-20261015.json` 是 root-only 的一次性明文交付包。两者现有 17 个队伍 Token 的 ID 与 scrypt 哈希全部匹配。用户已确认当前**计划分配范围为 `192.168.51.52`–`192.168.51.68`**，即 `TEAM-01` 对应 `.52`，依次到 `TEAM-17` 对应 `.68`；51 Registry 与这一计划 17/17 完全一致。计划 IP 的确认不代替每台实际机器身份、SSH 主机密钥及从该机到 51 的真实来源 IP 验证。

| 队伍 | IP | 队伍 | IP | 队伍 | IP |
| --- | --- | --- | --- | --- | --- |
| TEAM-01 | 192.168.51.52 | TEAM-02 | 192.168.51.53 | TEAM-03 | 192.168.51.54 |
| TEAM-04 | 192.168.51.55 | TEAM-05 | 192.168.51.56 | TEAM-06 | 192.168.51.57 |
| TEAM-07 | 192.168.51.58 | TEAM-08 | 192.168.51.59 | TEAM-09 | 192.168.51.60 |
| TEAM-10 | 192.168.51.61 | TEAM-11 | 192.168.51.62 | TEAM-12 | 192.168.51.63 |
| TEAM-13 | 192.168.51.64 | TEAM-14 | 192.168.51.65 | TEAM-15 | 192.168.51.66 |
| TEAM-16 | 192.168.51.67 | TEAM-17 | 192.168.51.68 | | |

每个队伍恰有一个 `team_id`、`assigned_ip`、`token_id` 与当前有效 Token 哈希；重复、缺失、哈希不符或凭据包过期均使交付停止。`ORGANIZER-TEST` 是绑定 `.50` 的独立预览身份，`DEMO-TEAM-01` 是历史练习身份，两者均不属于 `--all`，绝不写到选手机器。当前正式队伍只有 `PRACTICE`/`SEMIFINAL` 模式，Final 尚未授权。

## 51 访问判定

外部流量只经 51 Nginx `:8080`，其配置用 `$remote_addr` 覆盖 `X-Real-IP`；内部 FastAPI 绑定 loopback。服务端应按如下顺序判定：

1. 从 Bearer Token 解析 `token_id`，在 Registry 找到唯一 ACTIVE 记录，用独立 salt 的 scrypt 哈希常量时间比对密钥；不以浏览器上报的队伍号作为身份。
2. 对 `TEAM-01..17`，要求存在唯一 `assigned_ip`，且经受信代理取得的请求来源 IP 与其完全相同。正确 Token 从错误机器访问应拒绝，正确 IP 配错误 Token 也应拒绝。
3. 检查 `allowed_modes`。Practice 检查开放状态与已发布题目；Semifinal 检查 AM/PM 分配、场次状态与题目列表；Final 的 FT 检查题目开放时间，PK 还检查本轮 champion/challenger 身份与题目时间窗。未发布/未授权题目继续返回不可区分的 404。
4. 对提交维持每队/每题额度、服务端接收时间、幂等键和私有评分隔离；Token 交付不改变这些规则，也不赋予 `FINAL` 模式。

**当前实现：** `app/final_api.py` 在 Final 入口检查绑定 IP；`app/access_control.py` 现已在普通 Practice/Semifinal 授权入口对 `TEAM-01..17` 强制来源 IP 与 Registry 一致，缺失或不符则返回 403。已通过服务端测试、TEAM-01 正确来源和错误来源回归。正式批量交付前仍须逐机验证代理来源 IP、唯一绑定、SSH 主机密钥和 Token；宜提供只返回本队身份及授权范围的安全自检端点，不得返回 Token、GT 或未发布题目。

## Token 文件交付

选手样板机 `aiops-agent-web.service` 读取 `/etc/aiops-competition/team.env`。交付工具默认写这个路径；可指定同目录下其他非隐藏 `.env` 文件，但必须与目标机 systemd 单元的 `AIOPS_TEAM_ENV_FILE` 一致。文件内容仅为 `AIOPS_TEAM_ID=TEAM-xx` 和 `AIOPS_TEAM_TOKEN=<opaque-token>`，目标 `root:aiops-web`、`0640`，父目录不得是符号链接。浏览器不读取明文 Token；它只由本机后端读取并向 51 发请求。

在 51 以 root 执行下列**不输出 Token**的命令：

```bash
python3 /opt/aiops-contest-server/team-provisioning/token_dispatch.py --team TEAM-03
python3 /opt/aiops-contest-server/team-provisioning/token_dispatch.py --all
```

上述为只读计划。目标机 SSH 主机密钥经独立渠道核验并固定到 root 的 `known_hosts`、且已有受授权的非交互 root SSH 密钥后，才能显式执行：

```bash
python3 /opt/aiops-contest-server/team-provisioning/token_dispatch.py --team TEAM-03 --apply
python3 /opt/aiops-contest-server/team-provisioning/token_dispatch.py --all --apply
```

`--all` 的含义是 Registry 中全部 17 个正式队伍，不是所有可见主机。批量先预检所有主机：已固定的 SSH 主机密钥、目标 IP 确在主机网卡、`aiops-web` 用户/组、systemd 读取路径、目标文件状态和是否需要轮换；有一台失败就不向任何主机写 Token。通过后逐台原子写入和回读校验。已存在相同内容时幂等跳过；不同 Token 必须显式 `--rotate`，原文件留 root-only 备份。Token 只通过 SSH 标准输入传递，不进入命令参数、stdout、错误日志或审计。跨 17 台主机无法真正原子提交；若提交阶段中断，审计必须逐台标识成功/失败，禁止把部分成功报告成全量成功。

51 私有审计目录建议为 `/var/lib/aiops-contest/team-token-delivery/`（0700），只写队伍号、IP、Token ID、源文件 SHA、时间、目标路径、结果和非敏感错误码。`TOKEN_FILE_DELIVERY_VERIFIED` 只说明远端文件校验完成；仍须从每台队伍 IP 向 51 做本人身份自检，才可记 `TEAM_IP_TOKEN_ACCESS_VERIFIED`。正式场次、Final 名单和任务开放仍由 51 单独控制。

## 轮换与上线门槛

当前 `manage_competition.py token rotate` 会立即替换 Registry 中的有效哈希，可能在新 Token 尚未送达时中断队伍访问。正式轮换应改为 `PREPARED -> DELIVERED -> VERIFIED -> ACTIVATED` 两阶段流程，旧 Token 只在新 Token 经原绑定 IP 验证后撤销；凭据包必须同步更新，旧包失效。现有脚本的 `--rotate` 只负责目标文件替换，**不应单独视为完整线上轮换协议**。

冻结/全量交付前必须满足：已确认的 17 个计划 IP 与实际机器资产逐台匹配；17/17 SSH 主机密钥按受信指纹固定；受授权 SSH 通道可用；普通 Practice/Semifinal IP 校验回归；队伍 Final 名单与访问时窗获批准；单机与批量交付演练、从正确/错误 IP 的接口验证、Token 吊销演练均通过。当前 `.52` 的试发 Token、测试管理员公钥及 51 的主机指纹记录已清理，其余 16 台未交付；不开放 Final。**52 仍有原始 SSH 主机密钥、machine-id 和静态 `.52` IP；每台克隆机接入网络前必须重新生成身份并配置唯一 IP。**

## 历史试发验证

- 用户确认的 `.52`–`.68` 计划范围与当前 Registry 的 `TEAM-01..17` 顺序映射 17/17 一致；队伍号、绑定 IP 和 Token ID 均唯一。root-only 明文交付包中 17 个队伍 Token 均与当前 Registry 的 salt/hash 匹配。另一个凭据属于 `ORGANIZER-TEST`，未纳入分发计划。
- 初次 `token_dispatch.py --all` 计划返回 17 台、17 个不同 IP、0 台已固定 SSH 主机密钥；`--team TEAM-03` 计划只返回 `.54`。随后用户配置了 `.52` 的可信主机密钥和免密 SSH，`TEAM-01` 单机交付与幂等重试通过；这不意味着其余 16 台已就绪。
- 3 组独立模拟测试通过：重复 IP/过期 Token 阻断、单台与全量预检先于写入、批量预检失败零写入、批量中途失败保留逐台状态。真实 `.52` 文件交付、服务账号可读和从 `.52` 到 51 的 Team/IP 识别已通过；其他队伍及正式题目访问尚未验证。细节见 `TEAM01_DELIVERY_REPORT.md`。
- 源 Registry SHA-256：`699067188a30eaf8c09ed92a677565872d9b3415794b21f53d8c134292bafa62`；私有交付包 SHA-256：`234d4d1b6ef012836e1fdbc9b1083a6ff58d2439e7e05806434481eeb9236c04`。这些哈希用于版本核对，不包含明文 Token。

上述为 2026-10-19 的历史试发记录，当前状态以 `TEAM01_TEMPLATE_CLEANUP_REPORT.md` 为准；清理后 Registry SHA 已变化。
