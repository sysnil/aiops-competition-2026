# 决赛题库与选手机交接（2026-10-21）

状态：`FINAL_POOL_REGISTERED_CLOSED`。全部队伍当前只有练习权限；半决赛和决赛均未开放，正式决赛提交数为 0。

## 选手机

- 192.168.51.52～68 共 17 台均可访问，代码哈希一致，活动运行数和 Web/CLI 历史数均为 0。
- 从 52 同步到 53～68 的差异文件：`starter_todos.py`、`agent/llm_core.py`、`detectors/metrics.py`、`webapp/static/app.js`、`webapp/static/styles.css`；并同步两份更新后的测试。每台机器的 Team Token 和 LLM Key 文件在同步前后哈希一致。
- 17 台选手环境各自的完整单元测试通过 34 项。发布代码及逐台备份/校验记录在 51 的 `/var/lib/aiops-contest/player-release-20261020/`。
- 52 原有 8 条 Web 和 5 个 CLI 测试运行已从选手机移走；231 个原始文件、约 576 MB，已逐文件校验并归档在 51 的 `/var/lib/aiops-grading/internal-run-archives/TEAM-01-pre-handoff-20261020/`（root-only）。51 历史预览提交和审计记录未删除。

## 权限与分组

| 分组 | 队伍 |
| --- | --- |
| AM，9 队 | TEAM-01～TEAM-09 |
| PM，7 队 | TEAM-10～TEAM-16 |
| 暂不分组 | TEAM-17 |

分组按连续编号登记，若组委会已有不同抽签名单，须在开赛前更新。17 个 Token/IP 对应关系和凭据内容未变。AM/PM Session 均为 `NOT_OPEN`，两项临时预览授权已关闭，17/17 台实际调用均得到 Practice 200、Semifinal 403、Final 404。

## 决赛数据与 GT

SF-013～SF-017 的六文件 `CONTEST_EXCHANGE_V2` 包由 Master 与 51 暂存逐 Manifest SHA-256 核对，文件哈希、行数、30 分钟窗口、Trace Index 和 Public/Private 扫描全部通过；经现有导入器转换后发布为 51 上的 `public-v1`，二次校验全部 PASS。新 Case 权限与旧 Case 一致，Public API/组委会可视化账号均可读取真实 Metrics、Logs、Traces；组委会可视化题号扩展到 SF-017。

| Case | Master 原始 Public Manifest SHA-256 | 正式 GT 事件数 |
| --- | --- | ---: |
| SF-013 | `3328c73eaf8533d343b6452cb4cd14d3708b2adacc9757b9f593941a8895ab7c` | 1 |
| SF-014 | `7ce9501fe6dd0751566466cd0220aec64c5d89a06f4ee9bd03c35b4fc7665cfb` | 5 |
| SF-015 | `2e36f67f8e95e0a70fdcb10612cc89c4c5a00255549f583489dadee5af0a04b2` | 5 |
| SF-016 | `66797252cef3c83f06477bf8c59aec694b732c7bd30b23b55af36335522546a0` | 2 |
| SF-017 | `1f772f68bf55bedb3239d4fc6d67a485f81ba109b504c300b6b0dd3d7bf39dd0` | 5 |

Master Private 草案以本次用户“人工看过确认”为审批来源，选定事件按现有 11 类公开枚举转换：`MEMORY_WORKING_SET_INCREASE`→`MEMORY_USAGE_INCREASE`，Service 的 Trace 错误/延迟复合事件→`REQUEST_ERROR_INCREASE`（接受 `TRACE_LATENCY_INCREASE`）。未选候选保留在私有来源，不伪称逐项人工 REJECT。原七题的事件定义在 v2 中除版本标识外逐项一致，旧 v1 原件未改。

- 十二题正式 GT v2：`/etc/aiops-private-grader/final-gt-v2-20261020/`，Manifest SHA-256 `9049aa9461c61a08bde91a7c5e0c4cb8b9b848c62e92636dcbb567abc295c807`；共 35 个事件。
- 正式评分规则权重、容差和扣分未变；新规则副本仅重新绑定 v2 Manifest，SHA-256 `2f09909bc319caa0e1672067ed7766a930cf7cf189b3d7b9fe995670c0ef0d7d`。
- Private Grader 运行账号可加载十二题及规则；Public API 账号无法读取 GT。Private Grader 对新旧各一题的内部空答案联调均返回 `COMPLETED`、0 分，不产生选手提交。

## 任务分配

| 阶段 | 任务与 Case |
| --- | --- |
| 决赛上半场，共三题 | FT-01→SF-006、FT-02→SF-011、FT-03→SF-007 |
| 同题 PK，八轮 | R01→SF-004、R02→SF-008、R03→SF-009、R04→SF-012、R05→SF-013、R06→SF-016、R07→SF-014、R08→SF-017 |
| 未发布备用 | SF-015 |

前三题保留原映射，避免改变既有 FT-01 内部预览收据的含义，同时覆盖 Pod 内存、Service Trace 与混合观测。四个新 PK Case 只写入原本空置的 R05～R08；SF-015 未绑定任务。11 个任务与八个轮次在 SQLite 中均为 `NOT_OPEN`，无正式提交。

正式 PK 胜负规则现为本轮异常分析分数优先，同分比较有效提交的服务器接收时间，不使用评委分。独立临时数据库测试覆盖高分胜、同分速度决胜、预览成绩隔离和评委入口禁用；51 全套 API/评分测试通过。

## 开赛前操作

当前继续保持关闭。`manage_final_stage1.py` 的正式状态变更保护尚未解除；正式开赛需另行核对实际参赛名单/抽签、授予相应 Final 模式权限，并执行受控的任务开放与 PK 轮次推进。尤其需要确认 TEAM-17 是否为备用队，以及 9+7 的具体名单是否与本报告连续编号方案一致。不得通过修改本报告或 GT 文件直接开赛。
