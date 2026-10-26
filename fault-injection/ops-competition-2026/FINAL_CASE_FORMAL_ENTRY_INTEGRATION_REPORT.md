# SF-013～SF-017 正式入口集成与无害验收

**结论：`PRODUCTION_BLOCKED`。** 本轮真实业务故障 0 次、正式 Attempt 0 个、生产主机重启 0 次。新版本已把五题的控制入口统一到 Master Controller、独立 Guard、Worker Timer/实时授权 CLI 或 Master 状态机负载；五题无害控制链均通过。该结果只证明执行入口与撤销路径的工程可运行性，不能代替真实适配器资格或批准生产。

版本目录：`/root/ops-competition-2026/final-case-formal-entry-v1-20261019/`。完整代码、DRAFT 合同、测试和证据 SHA 清单：`results/evidence-manifest.json`，SHA-256 **`3d1aedb54e9adbf9c60f9a84f704dd5f2c8e517fc138a170473ef5e5ac9aaf9e`**。机器汇总：`results/integration-assessment.json`，SHA-256 **`9485b558c0bf9a638635b0c8e0c3429527a09c15a739de1cd424e418bc9513f4`**。Master、Worker2、Worker3 的新 `scripts/` 经 rsync `--checksum --dry-run` 比对无差异。共享实时授权仍使用上轮 `final-case-unified-execution-v1-20261019`，没有重写签名协议或旧证据。

## 实际入口

`control_contract.py` 对五题固定 `run_id`、Action 集合、Worker/机制/时长、系统服务名、源文件 SHA 和唯一执行命令。正式候选必须额外具有逐 Action 资格回执；启动时需要独立人工批准文件、完整 Global Safety 快照及五机空间快照。没有这些文件的正式 Manifest 在入口前拒绝。本轮仅创建 `SF-0xx-ATTEMPT-MARKERTEST-*` 演练身份，目录独立于正式 `/data/.../runs/`。

Worker 新 Timer 的命令只指向 `worker_fault_entry.py fire`；它加载 Manifest、核对本机和运行身份，随后调用已验证的 `worker_entry.execute` 获取一次性 Master 实时授权。正式效果只能经 `qualified_effect.py` 调用原有受限 `qualified_adapter.run(..., before_effect=...)`，要求版本匹配的资格回执、目标注册、独立 Watchdog 证明及效果前 grant/撤销检查。旧 `sf007/sf012` 的 4 秒本地租约 `fault_worker.py` 不在新题 Timer 命令白名单。旧文件和历史 Case 原样保留；不能宣称 root 用户无法直接运行历史脚本，但新五题的 Manifest/启动器会拒绝把它们注册为执行入口。

Master HTTP 没有 Worker Timer。`http_workload.py` 加载当前真实状态机业务生成器，在普通 GET `/` 的预注册窗口逐请求调用 `http_gate.py`；授权失败或撤销后记录 `DENIED_NORMAL_REQUEST`，请求继续以**无故障头**发送。Image/Cart 等对照请求不添加延迟头。HTTP 窗口在 60 秒结束后独立产生 Action 终态，业务负载可继续到 1800 秒；SF-017 的 Worker3 Service Action 因而有自己的后续许可与终态。任何子动作失败时 Controller 撤销本 Attempt 已登记的其他动作；Guard 在 Controller 非正常退出时独立执行同一范围的撤销。

| Case | 新正式代码路径 | 最终版本无害联合测试 | 旧入口限制 | 真实适配器资格 |
|---|---|---|---|---|
| SF-013 | Master Controller/Guard → Worker2 Timer → 统一授权 → PodMemory Adapter 门禁 | **PASS**：Marker、Controller/Guard 终态及 Timer 清理 | 新 Manifest 拒绝旧 `fault_worker.py` | **PENDING**：变更后 32 MiB/300 s 资格与恢复需重新验收 |
| SF-014 | Master Controller/Guard → Worker3 Timer → 统一授权 → ServicePause Adapter 门禁 | **PASS**：Worker3 Marker、双重清理 | 新 Manifest 拒绝 SF-007 本地租约入口 | **PENDING**：两副本 30 s 暂停及 Watchdog |
| SF-015 | Master Controller/Guard → 真实状态机普通业务请求入口 → HTTP gate | **PASS**：`LOAD_PASS`，故障头 **0** | 不调用 SF-011 旧 HTTP lease | **PENDING**：真实 250 ms 故障请求头及采集质量未执行 |
| SF-016 | SF-013 同一 Worker2 入口，但独立 Attempt/Action/Manifest | **PASS**：独立 Marker、Timer 清理 | 新 Manifest 拒绝 SF-012 本地租约入口 | **PENDING**：该题独立身份与恢复资格 |
| SF-017 | Master HTTP + Worker3 Service 两个独立 Action | **PASS**：普通业务 `LOAD_PASS`/零故障头，随后 Worker3 Marker；两 Action 分别撤销 | 两个子动作均锁定新入口 | **PENDING**：双机制真实资格与联合采集 |

最终正向演练的 Run ID 依次为 `SF-013-ATTEMPT-MARKERTEST-B7F70037`、`SF-014-ATTEMPT-MARKERTEST-3094C202`、`SF-015-ATTEMPT-MARKERTEST-625ADC85`、`SF-016-ATTEMPT-MARKERTEST-CBF906A1`、`SF-017-ATTEMPT-MARKERTEST-8C27F993`。原始 Master Controller/Guard、Worker Marker 与撤销记录保存在上述版本的 `rehearsal/`、`runtime/` 及 Worker 同名 `runtime/`；本地逐题摘要及哈希均在证据清单中。SF-013/SF-014 曾并行运行，均独立完成授权与清理，未发生跨 Attempt 撤销。

## 负向与边界验证

- 最终版本 Master 单测：新入口 **8/8 PASS**；共享授权协议回归 **13/13 PASS**；Python compile PASS。合成覆盖错误主机/机制/旧命令、缺少源码 SHA、无正式资格、并发与提前撤销、一次性消费/过期、跨 Attempt 取消、Guard 退出、模拟远端通道失败、HTTP 对照不加头及撤销后不再加头、低于容量预算即拒绝。
- SF-017 实际无害负向演练 `SF-017-ATTEMPT-MARKERTEST-525A3B4E`：在 Worker3 Timer 待触发时停止 Master Controller；独立 Guard 对 HTTP 与 Service 分别撤销并停止 Worker Timer。等过原定触发时刻后，**无 Worker3 Marker、无 HTTP 故障头**；原始记录 `results/sf-017-abort-harmless-control.json`。
- SF-015 的首轮无害演练因包装器提前创建输出目录、与原状态机生成器的独占创建规则冲突而 fail closed；SF-016 首轮演练曾因审计器过早停止 Guard 而证据不完整。失败演练原始运行目录保留，之后均用新 `MARKERTEST` ID 重测；未覆盖任何正式 Attempt。
- Master 签发前/签发后撤销及 Worker 本地撤销沿用上轮共享协议测试。Master 失联后已签出的 grant 最长仍有 **1.2 秒**残余有效期，不能声称绝对零竞态。网络中断在新 Controller 中以受控通道失败合成测试验证 fail closed；本轮**没有**切断生产网络做真实断联联测。

## 存储与生产门禁

统一采用 **DRAFT Worker 系统盘归档分配**：SF-013→Worker1 `/`、SF-014→Worker2 `/`、SF-015→Worker3 `/`、SF-016→Worker1 `/`、SF-017→Worker2 `/`。`storage_policy.py` 对 Master 数据盘、归档目标和各 Worker 数据盘逐题保留十进制 **5,000,000,000 B** 底线、历史单题 **7,350,324,113 B** 归档/导出预留及 Worker 数据盘每题 **2,000,000,000 B** 背景敏感性预留。`safety_monitor.py` 可只读采集五机实际挂载/余量、Node/Collector 基础状态、OpenSearch、Prometheus、Timer 和网络规则，每 10 秒生成心跳；正式 Controller 在等待及签发前拒绝过期、缺失或预算失败的心跳。一次新拓扑下的真实无故障采样返回 `DYNAMIC_STORAGE_AND_BASIC_HEALTH_PASS`，不是完整 Global Safety 许可。

末次只读可用空间（bytes）：Master `/` **45,219,573,760**、`/data/stage5-v3` **51,311,550,464**；Worker1 `/` **36,665,905,152**、`/data` **17,348,173,824**；Worker2 `/` **53,857,964,032**、`/data` **17,454,743,552**；Worker3 `/` **35,285,090,304**、`/data` **10,987,409,408**；VM1 `/` **87,392,141,312**。所有盘当前高于底线，**但五题累计仍不满足**：按上述每题 2 GB Worker 数据盘敏感性假设，Worker3 `/data` 到 SF-015 后约剩 **4,987,409,408 B**，已低于底线 **12,590,592 B**；五题结束约剩 **987,409,408 B**，缺 **4,012,590,592 B**，且未含突发余量。动态门禁会在不足时拒绝下一题；正式归档方案仍需扩容或经批准的数据迁移与恢复读回，不能仅因单题当前 PASS 就批准五题连续生产。

本轮末次环境：4/4 Kubernetes Node Ready，OpenSearch green，Prometheus healthy，Collector 最近 10 分钟 Metrics/Logs/Spans receiver-refused 增量均为 0；五机无活动竞赛 Service/Timer 或 `netem`/`tbf` 残留。未改 Collector、Kubernetes 业务 Deployment、历史 SF-003～SF-012 和 SF-013 失败记录。

**生产前仍缺：** (1) 本次新源码与五题各自真实适配器/Watchdog 的资格证明；(2) 1800 秒正式 capture/observer、完整 Global Safety、监控数据质量的联合验证，当前心跳仅覆盖基础健康和动态空间；(3) 可信隔离 VM 或经另行批准的主机重启生命周期测试；(4) 唯一归档方案的容量补足与异机恢复读回；(5) 组委会单独签发人工生产许可。未提供这些证明时 `start_formal.py` 会拒绝正式运行。**保持 `PRODUCTION_BLOCKED`，不启动任何真实故障或正式 Attempt。**
