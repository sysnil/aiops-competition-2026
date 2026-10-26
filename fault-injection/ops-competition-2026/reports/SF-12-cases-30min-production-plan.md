# SF-001～SF-012 题库生产规划（方案 B，DRAFT）

本规划只做只读资源与机制审查。SF-001、SF-002 是已采集的 60 分钟练习题；SF-003～SF-012 是拟新采集的 30 分钟正式题。目前 **0/10 正式题具备执行条件，0/10 已采集**。本轮没有注入故障、导入 SF-002 或修改原题 Manifest。

机器可读总账保存在 Master：`/root/ops-competition-2026/plans/SF-12-cases-30min-production-plan.json`；本机副本与本报告同目录。资源快照及机制审计原件保存在本机 `planning/SF-12-30min-20261015/evidence/` 和 Master 的同名规划目录。数值是 2026-10-15 的当前状态和基于两题的估计，不是未来十题的已验证容量许可。

## 十二题总账

| 题号 | 用途/采集状态 | 原定故障组合 | 30 分钟注入时间 | 当前生产资格 |
|---|---|---|---|---|
| SF-001 | 练习；60 分钟已采集，Public 已发布 | ServicePause + PodMemoryStress | 原始一小时数据保持不变 | 公开查询可用；练习提交与正式 GT 待核对 |
| SF-002 | 练习；60 分钟已采集，Public 尚未导入 51 | ServicePause + PodMemoryStress | 原始一小时数据保持不变 | 源包完整；导入、API 和练习提交待办 |
| SF-003 | 正式；未采集 | PodCPUStress + HTTP ReqDelay + PodKill | 未锁定；3 个新机制的持续/恢复合同缺失 | `SAFETY_BLOCKED` |
| SF-004 | 正式；未采集 | NetworkDelay + HTTP RespDelay + PodMemoryStress + ServicePause | 未锁定；两项已验证的 300 秒故障本身已超过现行 30 分钟时间合同 | `TIME_AND_MECHANISM_BLOCKED` |
| SF-005 | 正式；未采集 | HTTP RespReplaceCode + ContainerKill + NetworkLoss | 未锁定；三个机制没有可用的正式联合执行合同 | `MECHANISM_UNAVAILABLE` |
| SF-006 | 正式；未采集 | PodCPUStress + HTTP ReqAbort + NetworkBandwidth + HTTP RespAbort + ServicePause | 未锁定；5 次非重叠故障与恢复时长未获验证 | `MECHANISM_UNAVAILABLE` |
| SF-007 | 正式；未采集 | NodeCPUStress + HTTP ReqDelay + PodKill + NetworkDelay | 未锁定；4 项工程安全合同缺失 | `SAFETY_BLOCKED` |
| SF-008 | 正式；未采集 | NetworkLoss + HTTP RespReplaceCode + PodCPUStress + HTTP RespAbort + NetworkBandwidth + PodMemoryStress | 未锁定；6 次非重叠故障和控制链路保护未获验证 | `MECHANISM_UNAVAILABLE` |
| SF-009 | 正式；未采集 | NodeCPUStress + ContainerKill + HTTP RespDelay + HTTP ReqAbort + ServicePause | 未锁定；5 项中多项适配器缺失 | `MECHANISM_UNAVAILABLE` |
| SF-010 | 正式；**新增建议，待人工审查**；未采集 | ServicePause + HTTP ReqDelay | 未锁定；第 12 分钟先运行 ServicePause 时，第二事件最早约第 23 分 30 秒；它必须另有可验证的短时执行与恢复合同 | `SAFETY_AND_TIME_BLOCKED` |
| SF-011 | 正式；**新增建议，待人工审查**；未采集 | PodMemoryStress + NetworkDelay | 同上；网络目标/正常对照须按最新拓扑重新证明 | `SAFETY_AND_TIME_BLOCKED` |
| SF-012 | 正式；**新增建议，待人工审查**；未采集 | NodeCPUStress + HTTP RespReplaceCode | 未锁定；两个机制均缺正式安全适配器 | `SAFETY_AND_TIME_BLOCKED` |

SF-003～SF-009 的故障类型和数量来自原候选计划，保持原样。SF-010～SF-012 是补齐 Service、Pod、Node、NetworkPath 和 HTTP 调用边现象的设计建议；不构成注入许可。每个正式案例仍须提供 Public Metrics、Logs、Traces 和独立 Private 异常草案，识别任务只评分可回查的异常现象、实体与时间区间。

## 30 分钟时间合同

当前正式执行器使用 5 分钟采集分段，并要求首段的不可变 +240 秒 Trace 审计和质量检查在首个故障前完成。已验证的首次故障在第 12 分钟（720 秒）。现有 ServicePause/PodMemoryStress 各持续 300 秒；前一故障的公开恢复为 240 秒，检查 45 秒，下一次预检 105 秒；末次故障后恢复 180 秒、检查 45 秒。

两次此类故障的最早不重叠完成时间为 `720 + 300 + 240 + 45 + 105 + 300 + 180 + 45 = 1935 秒`，比 1800 秒多 **135 秒**。即使只从 10 分钟基线计算而暂不考虑首段审计，也需 1815 秒。把首故障提前至第 9 分钟得到的 1755 秒仅是算术假设，和现行首段完整审计门禁冲突，**不能执行**。若三次故障都使用同样 300 秒、恢复及预检合同，至少需 2625 秒；四、五、六次分别至少需 3315、4005、4695 秒。这些是同合同下的下界，不把未知新机制强行设为 300 秒。

所有正式题的 `start_seconds` 在 Manifest 中保持 `null`。待每种新机制的强度、持续、独立 Watchdog、公开证据、恢复及预检时间获验证后，按 `next_start >= previous_start + duration + recovery + 45 + 105` 顺序计算；最后还需 `last_start + duration + recovery + 45 <= 1800`，并满足首段质量门禁、正常基线和最终业务恢复。现阶段没有一份可以安全冻结的 30 分钟注入时刻表。特别是 SF-004 的两个 300 秒故障，在现行合同下无法容纳；不得缩短它们或让故障重叠。

下一版 30 分钟执行器需要先用无故障分段验证首段审计时序，再为新机制验证各自更短但真实有效的持续与恢复合同；如果仍放不下，应提交变更题目组合或延长窗口的方案，由人工决定。不能先运行再修改规则。

## 当前机制资格

ServicePause 与 PodMemoryStress 在 SF-001、SF-002 均有真实、公开可观测和恢复证据；每道新题仍需新鲜 Pod UID、进程/cgroup、内存余量与 Watchdog 检查。其余 12 个候选机制本轮均未获当前正式执行器的生产资格：

| 机制组 | 实际障碍 |
|---|---|
| PodCPUStress | 旧 Ad 目标节点已变；旧实现按进程名清理，缺运行实例所有权和短租约绑定。 |
| PodKill、ContainerKill | 缺 UID/ContainerID 前置条件、替换实例的独立恢复；当前观察器把预期身份变化视为失败。 |
| NodeCPUStress | 旧强度与当前共享 Collector/存储节点安全边界不匹配，缺持续 CPU/PSI 停止合同。 |
| NetworkDelay/Loss/Bandwidth | 旧路由与 Pod 放置不能复用；需新鲜 Pod UID、Calico 路由、真实业务包与独立对照证明，过滤器不得覆盖 Collector 或管理通道，qdisc 必须由本次运行独立清理。 |
| HTTP ReqDelay | 存在 header-delay 原语，但尚无针对真实 HTTP 路由的比赛级作用范围、最大延迟、对照排除和租约恢复。 |
| HTTP RespDelay/ReqAbort/RespAbort/RespReplaceCode | 尚无对应正式适配器；不能把请求延迟改名当作响应故障，也不能把 gRPC 当作 HTTP。 |

新增机制首次正式启用可同时承担效果验证，但必须**先**通过作用范围、强度、管理/遥测隔离、独立恢复、当前目标身份与时间合同的工程安全准入。磁盘门槛变更不能代替这些检查。

## 存储测算

以下为只读快照，单位为十进制 GB；绝对余量下限为 20 GB。四个节点均为 4/4 Ready，otel-demo 39/39 Pod Ready；本轮未见运行中的比赛故障服务。

| 节点 | 系统盘剩余 | 数据盘剩余 | 归档角色 |
|---|---:|---:|---|
| Master | 45.967 | 259.052 | 活跃采集和临时导出 |
| Worker1 | 70.049 | 54.883 | 业务节点，无本轮双副本归档 |
| Worker2 | 69.009 | **62.299** | 原始数据与 Public/Private 第一副本、导出 |
| Worker3 | 62.013 | 89.173 | 原始数据第二副本 |

SF-001/SF-002 的最终原始目录分别为 11.375/11.404 GB；其中 Trace 与各审计版本约 10.19/10.22 GB，Logs 0.736/0.745 GB，Metrics 0.0042/0.0044 GB。运行控制目录各约 0.218 GB，Public 各约 0.078 GB。故 30 分钟的**线性估计**为每题每个原始归档副本加控制文件约 **5.811 GB**，Public 约 0.039 GB，Private 约 0.0024 GB；十题单个归档节点净增约 **58.111 GB**。考虑新题 3～6 次故障和 Trace 突发，另列 25% 敏感性预算约 **7.264 GB/题、72.638 GB/十题/归档节点**。这些是估算，不是经无故障 30 分钟实测的硬上限。

单题开始前还必须检查实际峰值。目前仅一小时执行器有已验证的保守增量：Master 数据盘 34 GiB、Worker2 31 GiB、Worker3 29 GiB，另有 Master 系统盘约 64 MiB 与 Worker 根盘保护。这些保护在 30 分钟执行器完成容量实测前继续适用。以线性净增估计，当前空间和原峰值门禁允许 Master 10 题、Worker2 **最多 2 题**、Worker3 **最多 7 题**串行开始；第三题之前 Worker2 将不能再证明归档峰值后的剩余空间达 20 GB。即使只按最终净占用，Worker2 完成十题也会低于 20 GB。

若继续使用原峰值门禁，为使第十题仍可启动，Worker2 需额外至少 **43.3 GB**（线性估计）或 **56.4 GB**（25% 情景），Worker3 至少 **14.3/27.4 GB**。建议预留 Worker2 **不少于 60 GB**、Worker3 **不少于 30 GB** 的新增受保护数据容量，并在扩容后重新核算文件系统、临时文件和五节点安全；推荐值本身不代替实际执行前预检。Master 当前容量可支持上述估算，但仍须逐题检查。经授权的历史归档迁移也可以释放空间，前提是新位置完成独立 SHA-256、双副本及恢复读取，再变更索引；本轮不迁移或删除任何旧数据。

## 练习题交付与评测兼容

SF-001 的 Public 已在 51 服务器发布；SF-002 的 Public 在 Master，六文件、Manifest、SHA-256 和泄漏扫描已通过，但**尚未传输或导入 51**。建议单独流水线：仅传 SF-002 六个 Public 文件到受限 staging，目标端独立校验；把 `validate_exchange_v2.py`、`prepare_exchange_v2.py` 和 SF-001 专用 API 回归测试参数化为 case ID 与原始窗口，先 validate-only，再发布为新版本，最后验证三模态查询、Trace 详情、分页和 60 分钟时间范围。不得修改 SF-002 原始 Manifest、裁剪数据或传 Private 文件。

当前评分器只把 `DEMO-001` 识别为练习模式；SF-001 的 Public 虽已发布，提交时仍可能按正式题计数和处理。当前答案 Schema 要求 `suspected_fault_type`，评分项也给故障类型 40 分；这与已确认的“多模态异常检测、不要求判断注入类型或根因”不一致。SF-001 私有 GT 是 58 候选/13 归并事件的待审草案，SF-002 GT 也是待审草案；现有正式评分器仅接受 `APPROVED`，匹配器最多 12 个事件，提交上限 10 个异常。不能把草案直接改名或标批准来测试正式评分。

在练习题发布前，应定义练习题专用的 case 集、提交次数与反馈策略，调整异常检测评分 Schema，并经人工审核形成与评分器匹配的练习答案；随后用实际 Starter Agent 与 `submit.py` 执行 SF-001/002 的查询、提交、轮询、反馈全链路回归。本次在 51 服务器应用目录和 Master 参考目录未找到可核验的 `submit.py` 或 Starter Agent 文件，因此该回归目前记为 **未执行**。需从其实际发布仓库/参赛镜像取受控版本，不凭空宣称兼容。

正式题须保持独立的 Public/Private 隔离；评分 GT 应由真实 Public 异常证据形成，记录异常现象、实体、观测时间和可回查引用，按事前审核规则确定事件粒度，再锁版本。故障注入记录仅供可信审核，不直接生成答案；正式 GT 不交给参赛账号。

## 推荐顺序与退出条件

资源和机制准备顺序：先完成 30 分钟采集器/首段审计无故障验证及容量实测，同时修复 HTTP 请求作用范围和 Pod CPU/重建身份问题；随后做 Node、Network 安全和独立恢复证明，并完成目前缺失的 HTTP 响应/中止适配器。SF-002 Public 导入与练习提交验证单独进行，不依赖正式题故障进度。

正式生产顺序预注册为 **SF-003 → SF-004 → … → SF-012**，每题只有在自身组合、时间合同、当前拓扑、数据盘峰值、Collector、Watchdog、Public/Private 校验及双副本归档全部通过后才启动。前题如果机制不可用，保留 `NOT_STARTED/BLOCKED`，可继续检查后续题的资格；不得因算法或选手答案决定题目替换、补采或顺序。当前状态是十题全部 `NOT_STARTED`，无可立即执行的正式题。

进入首道 30 分钟正式采集之前，尚需人工审核 SF-010～012 组合、30 分钟窗口与每种机制的预注册参数，解决时间门禁冲突，完成资源扩容/经授权迁移，以及练习题评分合同修订。规划文件本身不是冻结协议或注入授权。
