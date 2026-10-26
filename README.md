
# 院智能运维技能大赛 2026

组委会内部代码与题库归档。该仓库含正式题目、标准答案、评分规则和故障机制资料，保持Private。

2026年10月26日从比赛服务器51、靶场Master和组委会测试机50采集只读快照，整理代码及必要配置；没有重跑题目、注入故障、修改GT或历史评分。

## 从哪里开始

| 内容 | 目录 | 说明 |
|---|---|---|
| 比赛API和私有评测器 | [server](server) | 51现行FastAPI、Parquet查询、鉴权、提交队列、QA/决赛评分及测试 |
| 故障注入与采集 | [fault-injection](fault-injection) | Master生产脚本、版本化adapter、watchdog、cleanup、采集及导出支持代码 |
| 半决赛题库 | [questions/semifinal](questions/semifinal) | 现行题面目录、Q01–Q10及练习生成源、Schema、求解器、独立复算记录 |
| 决赛题目配置 | [questions/final](questions/final) | FT-01–FT-03、PK-R01–PK-R09与12个Case的映射 |
| 正式GT和规则 | [private](private) | 51部署的正式QA/决赛GT、评分配置；生成时草案另保留在历史生成包中 |
| 官方选手Demo | [participant/demo](participant/demo) | 50当前官方CLI/Web、Baseline、LLM Agent、可视化和提交逻辑 |
| 组委会工具 | [organizer](organizer) | 多模态标注、提交证据页、排名、队伍身份分发及场次操作 |
| 部署参考 | [deploy](deploy) | systemd源文件与无凭据配置示例 |
| 数据索引 | [datasets](datasets) | Public Manifest和可用元数据；原始Metrics/Logs/Traces不在Git中 |
| 来源和校验 | [provenance](provenance) | 原始路径、源/归档SHA-256、脱敏记录和文件清单 |

工程概览见[架构说明](docs/ARCHITECTURE.md)，部署边界见[运维说明](docs/OPERATIONS.md)，评分口径见[当前评测说明](docs/SCORING.md)。

## 版本与权威来源

- `participant/demo/`使用50的官方Demo，未包含任何队伍的自研算法或运行历史。
- Master保留了多个adapter和安全门版本；版本名、历史报告和源码原样归档，见[故障脚本索引](docs/FAULT_INJECTION.md)。旧DRAFT不自动成为可执行入口。
- 正式半决赛GT在`private/ground-truth/semifinal/`，决赛GT在`private/ground-truth/final/`。`questions/semifinal/generation/private/`是原始生成资料，可能保留当时的DRAFT状态，不能据此覆盖现行正式GT。
- 旧报告里的场次、人数、权限、分数或提交额度仅代表当时状态。当前归档的控制代码、部署计划和评分实现优先；不存在对现行数据库的隐式恢复操作。

## 本地只读校验

```bash
python3 -m pip install -r tools/requirements-validation.txt
python3 tools/validate_archive.py
```

该命令只核验哈希、语法、JSON、题号与GT数量，不连接集群、不读取远端数据库、不执行注入。应用测试需要各子项目依赖和测试环境，不能把静态校验当成真实链路或故障复验。

## 归档范围

不包含服务器密码、队伍Token、LLM API Key、SSH私钥、原始评分数据库、选手答案和遥测Parquet。部署时从本地受控配置重新提供凭据。被脱敏的文件在provenance中明确标注；历史冻结源码若有脱敏，其归档哈希与原冻结哈希不同，不能直接通过旧注入授权。

本仓库是可追溯的内部归档，不是换一台机器就能直接执行的靶场镜像。部分脚本绑定原始路径、集群身份和冻结哈希；重新执行需要重新配置、资格检查、授权及独立恢复机制。
