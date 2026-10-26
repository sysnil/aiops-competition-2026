# SF-PILOT-01 机制资格审核（内部 DRAFT）

**结论：`PILOT_MECHANISM_NOT_QUALIFIED`。** 本轮停在 Phase 0，故障注入、
60 分钟真实赛题采集及所有后续数据处理均未启动。保留公开编号 SF-001 的
设计意图，不创建可发布 Case。

权威证据是本次从 Master 只读取回并校验 SHA 的最新报告，而不是根据适配器
存在或历史 Case 准入推断资格：

- `stage5-v3-final-fast-ready/results/final-decision.json`：
  `ENGINEERING_PARTIAL`、`FORMAL_QUALIFICATION_ALLOWED=false`。
- 同目录 `mechanism-readiness.json`：SERVICE_CONDITIONAL；
  POD_CONDITIONAL_WORKLOAD_COVERAGE，均仍欠两次有界故障/恢复资格实验。
- `stage5-v3-mechanism-qualification/results/qualification-summary.json`：
  `PHASE1_NO_READY_MECHANISM`、`faults_injected=0`；Service、Pod 两种候选的
  两个 seed 都为 NOT_RUN。
- `stage5-v3-autonomous-preparation/reports/autonomous-preparation-summary.md`：
  八次计划故障实验均未执行，无同版本两次 Clean 通过。
- `stage5-v3-cross-layer-rca/design/fault-catalog-draft.yaml`：相关机制为
  CONDITIONAL，没有无条件 READY 的不同业务实体组合。

各源文件的远程路径、mtime、原始内容、SHA 见
`reviews/20261013-phase0/phase0-evidence.json`。没有读取 Stage5-v1/v2 TEST，
没有读取未来 TEST GT，也没有改写任何研究结果。

## 10 项资格前置审查

| 项目 | Service：Recommendation 全副本暂停 | Pod：另一业务服务单实例内存压力 |
| --- | --- | --- |
| 1. 注入资格证据 | 未通过；两次当前机制资格实验 NOT_RUN | 未找到合格的不同业务服务目标；既有内存候选是 Recommendation |
| 2. 稳定真实业务负载 | 近期 Clean Before 后五分钟通过，但另一 30 分钟窗弱副本最低 24/min，尚不稳定 | 目标未定；不能继承 Recommendation 的请求覆盖、资源或 sibling 证据 |
| 3. 当前目标 UID | 本次只读快照核对两 Recommendation Pod Ready，UID 与既有记录相符 | 未绑定目标；当前 Ready 的其他服务 Pod 不因此自动合格 |
| 4. 直接根因公开证据 | 普通业务 Trace/UID 可读；service-wide stall 的直接故障信号尚需实测 | 须验证所选 Pod 工作集、独立 pressure/reclaim/请求信号、UID 与 sibling 对比 |
| 5. 已验证强度 | 没有当前合格强度；不得照搬其他服务或单次 DEV | 未确定；内存余量预检不证明安全且可观测的注入强度 |
| 6. 支持 8 分钟 | 没有 480 秒资格证据 | 没有 480 秒资格证据 |
| 7. 故障期安全边界 | 须绑定服务范围、节点影响、失败归属与停止条件 | 须绑定 UID/container/cgroup、OOM/重启/整节点压力上限及停止条件 |
| 8. 独立 watchdog | 有通用 systemd/Collector 回滚能力证据；不等于此故障的 480 秒恢复演练 | 同左；还需所选 Pod 内存机制专属恢复验证 |
| 9. 故障后恢复 | 当前资格故障未执行，无本机制恢复合格证明 | 所选不同服务目标无资格/恢复证明 |
| 10. 连续一小时组合安全 | 尚未验证；25 分钟 Clean 与独立 30 分钟 Collector 窗不能拼成一小时实验 | 同左；第二故障前必须证实第一故障恢复，不可按固定钟点强行注入 |

当前 Recommendation UID 快照：

- `recommendation-c887f6f6b-cxb5j`：`8b7c229b-cea1-440f-bc88-e9c1f246ef9e`，Worker3。
- `recommendation-c887f6f6b-zchkk`：`3886c989-d7a1-44e9-8b44-64c536db1897`，Worker3。

这些是审查时的公开身份，不能作为未来执行时无需刷新的目标绑定。

## 最小资格缺口

1. **Service 候选**：在批准的前置规则和固定真实业务负载下，完成当前
   Recommendation 全副本暂停的两次独立、有界、可恢复资格验证；明确公开
   直接信号、正常对照、已验证强度和最大持续时间。现有通用 watchdog 证明
   不能代替针对暂停进程的独立恢复证明。
2. **Pod 候选**：先确定 Recommendation 之外的业务服务，验证普通业务对
   目标和 sibling 的覆盖、UID/container/cgroup 及内存安全余量，再资格验证
   该服务的单 Pod 内存机制与独立恢复。不得为了不同实体要求直接换服务后
   复用 Recommendation 的强度和时长。
3. **时长决策**：8 分钟未验证。若只取得更短机制资格，应在注入前人工批准
   新时间表并重新私有预注册。可讨论 5 分钟故障、总观察仍 60 分钟的方案，
   但当前连这两个不同实体的 5 分钟合格组合也不存在，缩短时间并不自动解锁。

资格满足后才进入 Phase A 的新鲜 Global Safety、独立基线、一小时预算和
实际 watchdog 检查。本次读到的历史 `GLOBAL_SAFETY_PASS` 时间为
2026-10-13 08:47:36 UTC，包含 `faults_allowed=false`；它早于当前审核，
不能充当新的注入许可。本次仅观察到四个 Kubernetes 节点 Ready、目标 Pod
身份及 Master 数据盘状态，没有把这些局部检查称为完整新鲜安全门禁。

另有后续联调访问缺口：192.168.51.51 与 192.168.51.50 的 SSH TCP 端口可达，
但提供的 root 凭据认证失败。未检查到实际 importer/schema/Token/SDK；这不
等于 API 服务不可用。由于机制资格已经阻断任务，没有继续做无关的大规模
服务器诊断、尝试其他账户、绕过认证或开放端口。
