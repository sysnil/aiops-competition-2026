# SF-PILOT-01 首题端到端验收报告（Phase 0 阻断）

**结论：`PILOT_MECHANISM_NOT_QUALIFIED`。本轮未生成真实半决赛题目。**
本报告是内部审核记录，不是公开 SF-001 的发布包。2026-10-13 本轮按用户规定
在资格前置阶段停止，仅交付待审核设计、资格缺口与只读证据。没有把历史研究
失败实验、无故障 Clean 或单次 DEV 准入当作两次合格真实故障。

| 验收项 | 状态 | 依据 |
| --- | --- | --- |
| CASE_DATA_STATUS | BLOCKED | 两个不同业务实体的合格 Service/Pod 故障组合不存在，8 分钟未验证 |
| REAL_DATA_IMPORT_STATUS | NOT_RUN | 无真实 Public 包；未运行 validate-only 或发布 |
| E2E_TEST_STATUS | NOT_RUN | 无 SF-001 实例；未运行 SDK/Agent 查询 |
| CONCURRENCY_STATUS | NOT_RUN | 无导入后的真实数据；未启动 17 客户端测试 |

## 权威资格核验

从 Master 读取的最新 Final Fast-Ready decision 仍为
`ENGINEERING_PARTIAL`，`FORMAL_QUALIFICATION_ALLOWED=false`。
Service 为 `SERVICE_CONDITIONAL`，Pod 为
`POD_CONDITIONAL_WORKLOAD_COVERAGE`。Mechanism Qualification Phase 1 的结论为
`PHASE1_NO_READY_MECHANISM`，两类候选的两个故障 seed 均 NOT_RUN，故障计数为 0。
13 份权威源文件已保存原始字节并校验 SHA，详见私有证据索引。

拟定 Service 候选是 Recommendation service-wide stall；现有 Pod 内存候选
也来自 Recommendation，不能满足不同业务服务的要求。另一个服务的 Pod 不能
仅因 Ready、可见指标或已有适配器就获得资格。当前没有 480 秒的强度、持续性、
独立 watchdog 与故障恢复合格证据，亦没有完整 60 分钟组合安全资格。

最低补齐事项为：① Recommendation 服务暂停的当前拓扑/负载下两次有界直接
根因与恢复验证；② 选定并验证另一业务服务的单 Pod memory pressure，包含
目标/sibling 普通业务覆盖及 OOM/节点安全边界；③ 对两机制验证实际拟用时长。
若采用较短时长，需事前审批并重新私有预注册；DRAFT 提供了总时长仍为 60 分钟
的 5+5 分钟故障排程作为讨论选项，**不表示该组合当前已合格或已获执行授权**。

## 当前最小环境核验

| 地址 | 角色 | SSH TCP | 授权访问结果 |
| --- | --- | --- | --- |
| 192.168.53.52 | master | REACHABLE | AUTHENTICATED_READ_ONLY |
| 192.168.53.53 | w1 | REACHABLE | AUTHENTICATED_READ_ONLY |
| 192.168.53.54 | w2 | REACHABLE | AUTHENTICATED_READ_ONLY |
| 192.168.53.55 | w3 | REACHABLE | AUTHENTICATED_READ_ONLY |
| 192.168.53.50 | router | REACHABLE | AUTHENTICATED_READ_ONLY |
| 192.168.51.51 | server | REACHABLE | AUTHENTICATION_BLOCKED |
| 192.168.51.50 | client | REACHABLE | AUTHENTICATION_BLOCKED |

五节点靶场实际为四个 Kubernetes 节点加一个网络模拟节点。本次 API 只读快照
确认四个 Kubernetes 节点 Ready，Recommendation 两 Pod Ready、UID 与已有
身份记录一致。未执行新 workload、watchdog 恢复演练或完整 Global Safety Gate。
历史 Global Safety PASS 为 2026-10-13T08:47:36.614289+00:00，审查时已过约
33.5 分钟，且 `faults_allowed=false`，不能用作当前注入许可。

Master `/data/stage5-v3` 当前仍为 `/dev/sdb1` ext4，UUID
`a35051a2-3d09-4b83-bbc0-3bb055e8a4f3`，读写挂载。审核快照中数据盘可用
307,720,200,192 字节，根盘可用 46,194,819,072 字节。竞赛大型原始数据的
拟用路径是 `/data/stage5-v3/ops-competition-2026/SF-PILOT-01/`，本轮未创建
实验数据树；未移动研究数据或任何现有观测组件数据目录，也未宣称一小时预算通过。

比赛服务器和参赛样板机端口可达，但给定 root 凭据被拒绝。因此无法确认
`/opt/aiops-contest-server/scripts/import_case.py`、部署 Schema/Manifest/Token/
路由规范，以及 `/opt/aiops-competition/.venv` 和 SDK 的实际状态。
这只是认证受阻，不能推断 API 服务不可用。未尝试其他账户、绕过认证、开放端口
或发放 Token。该访问问题记录为后续联调条件，不据此继续做大规模无关诊断。

## 产物与隔离

- 内部设计：`private/SF-PILOT-01/design-draft.yaml`。
- 10 项机制审核和最小缺口：`private/SF-PILOT-01/qualification-review.md`。
- 机器决策：`private/SF-PILOT-01/phase0-decision.json`。
- 权威源快照、主机可达性和当前身份：`private/SF-PILOT-01/reviews/20261013-phase0/`。
- 本报告：`reports/SF-PILOT-01-e2e-report.md`。

没有生成 Public Parquet、公开 manifest、GT 草案、异常证据索引、导入记录、
SDK 查询结果或 Agent 异常 JSON；缺少真实数据时生成这些内容会混淆计划与实测。
现有 SHA 清单仅证明审核材料完整，**不是 Parquet 数据 SHA 清单**。所有文档均为
组织方内部材料，包含私有候选机制与时间表，不进入比赛公开目录或 API。

## 八项最终结论

| 问题 | 实际结论 |
| --- | --- |
| SF-PILOT-01 真实生成成功？ | 否，Phase 0 BLOCKED |
| 两个不同实体故障均有效执行？ | 未执行，0 次 |
| 足够可验证异常？ | NOT_EVALUATED，无本轮故障观测 |
| Public 三模态完整无泄漏？ | NOT_EVALUATED，Public 包未创建 |
| 真实数据导入成功？ | NOT_RUN |
| 参赛机 SDK/Agent 异常发现？ | NOT_RUN |
| 17 客户端并发通过？ | NOT_RUN |
| 推荐批量生产？ | 不推荐；先完成机制资格、时长与联调访问审核 |

本轮未更改研究 Clean Gate、历史协议/数据/GT/预测，未访问 TEST GT。
任务在生成这份待审核设计与缺口清单后停止，等待人工审核。没有自动执行新增
资格故障、短时改版时间表、真实题目采集或发布。
