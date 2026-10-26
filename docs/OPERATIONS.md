
# 部署与运维边界

## 版本化恢复

1. 先在隔离环境安装各子项目依赖；server的`pyproject.toml`/`uv.lock`和participant的`requirements.txt`分别保留。
2. 按源Manifest恢复外部Public数据，校验真实Parquet哈希及版本。不要用缺失数据或模拟文件冒充READY数据。
3. 在目标主机本地创建凭据配置，参考`deploy/examples/`；真实文件不入Git。
4. 明确配置Public、Private GT、队伍权限、评分数据库、答案归档和只读快照路径。Git中的历史计划不自动写回生产库。
5. 在测试库验证提交、队列、Breakdown、持久化和权限，再配置systemd。源unit中的`/opt`、`/etc`、`/var/lib`及用户/组绑定需要人工映射。

## 原环境布局

| 用途 | 原地址或目录 |
|---|---|
| 比赛API | 51的8080，内部FastAPI 127.0.0.1:8000 |
| 比赛项目 | `/opt/aiops-contest-server/` |
| Public数据 | `/data/aiops-contest/public/` |
| 正式GT | `/etc/aiops-private-grader/`下受限目录 |
| 主评分数据库 | `/var/lib/aiops-grading/submissions.sqlite3`，未入Git |
| 官方选手Demo | `/opt/aiops-competition/participant-starter/` |
| Web运行历史 | `/var/lib/aiops-agent-web/runs/`，未入Git |
| 靶场生产代码 | Master的`/root/ops-competition-2026/` |
| 靶场大数据 | Master的`/data/stage5-v3/ops-competition-2026/`，未入Git |

## 控制命令来源

组委会PK控制脚本在`server/scripts/final_event_control.py`；实际`aiops-final`包装命令需要按目标目录安装。没有安装包装器时可在原部署布局下调用脚本，不能在本机随意指向生产数据库试跑。

队伍Token与LLM凭据的单机/批量分发源码在`organizer/team-management/`。生产registry、API key表、选手密码表与SSH授权材料均被排除，不能从本仓库直接恢复旧凭据。

故障生产不得由普通安装、测试或提交动作触发；每次需要独立预检、目标身份验证、看门狗、cleanup、恢复与残留检查。
