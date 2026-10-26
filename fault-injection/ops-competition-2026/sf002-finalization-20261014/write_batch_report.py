"""Final Chinese report assembled from actual receipts; does not admit or run cases."""
import json,hashlib,shutil
from pathlib import Path
from datetime import datetime,timezone
B=Path('/root/ops-competition-2026');D=Path('/data/stage5-v3/ops-competition-2026/batch-sf002-sf009');R=D/'SF-002';V=D/'revisions/storage20-20261014';I=Path('/data/stage5-v3/ops-competition-archive-index/SF-PILOT-02-ATTEMPT-01');C=B/'sf002-production-20261014'
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def table(headers,rows):return '| '+' | '.join(headers)+' |\n|'+'|'.join(['---']*len(headers))+'|\n'+''.join('| '+' | '.join(str(v) for v in row)+' |\n' for row in rows)
def main():
 d=read(V/'batch-decision.json');a=read(R/'capture-and-effects-assessment.json');m=read(R/'public/manifest.json');x=read(R/'reports/execution-accounting.json');gt=read(R/'private/anomaly-gt-draft.json');h=read(V/'final-health.json');s=read(V/'final-storage.json')
 if d['ready_cases']!=1 or h['current_environment_blockers']:raise RuntimeError('FINAL_RECEIPTS_NOT_READY')
 out=B/'reports/SF-002-to-SF-009-integrated-fault-validation.md'
 if out.exists():
  expected='49d9f10f604e22d356d8e3de017dcf73de4d121b384cfce9a71e7b2bce2f7b39'
  if sha(out)!=expected:raise RuntimeError('UNEXPECTED_EXISTING_REPORT')
  old=out.with_name(out.stem+'-before-storage20-20261014.md')
  with old.open('xb') as f:f.write(out.read_bytes())
 text='# SF-002 至 SF-009 融合故障验证：20 GB 门槛后续执行\n\n'
 text+=f'更新时间：{datetime.now(timezone.utc).isoformat()}。最终状态 **BATCH_8_PARTIAL**。本轮实际生成 SF-002：完整 60 分钟、2 次真实故障、均恢复、Public 六文件及双副本完整校验通过。其余 7 道未开始，保留原组合，不替换机制、不伪造执行。\n\n'
 text+='## 存储授权与执行版本\n\nMaster 与 Worker1/2/3 的绝对剩余磁盘下限按用户本轮明确授权改为 **20,000,000,000 bytes（20 GB）**。使用独立执行版本，未修改历史规则、原始数据、科研冻结资产或既有归档。CPU、内存、Collector、时间同步、2 秒启动容差、恢复及数据质量规则均未放宽。\n\n'
 text+='单题峰值增量仍保留 Master 数据盘 34 GiB、Worker2 31 GiB、Worker3 29 GiB，JSON 24 GiB、原始加控制数据 26 GiB、导出临时 2 GiB、产物 2 GiB 上限；不是只看开跑前剩余 20 GB。后续每题都必须重新计算峰值预算，本次通过不代表余下七题已获存储许可。\n\n'
 text+=f'执行代码：`{C}`；磁盘政策：`{C}/design/storage-policy.json`。Run Manifest SHA-256：`{sha(C/"plans/SF-PILOT-02-ATTEMPT-01/run-manifest.json")}`。\n\n'
 text+='## 实际测试和生产\n\n58 项批量规划/调度测试通过；SF-002 正式分支 56 项非注入回归通过。六事件 Marker 联合演练通过，最大启动误差上界 0.386294 秒，错误哈希、错误 Worker 和取消后重启均被拒绝。Marker 只证明调度流程，不替代真实故障恢复证据，也不代表 2–6 个任意新机制已获准执行。SF-002 复用已验证的两事件正式后端。\n\n'
 text+=f'实际观测窗口：`{m["window"]["start_utc"]}` 至 `{m["window"]["end_utc"]}`。负载 MEDIUM 1.4 sessions/s、Image 1/s、Cart 2/s，seed=20261014002；全程不调整。\n\n'
 events=read(R/'private/fault-validation-report.json')['events']
 text+=table(['事件','机制','实际开始 UTC','持续秒','启动误差上界秒','恢复'],[[e['fault_event_id'],e['fault_type'],e['actual_start'],round(e['actual_duration_seconds'],6),round(e['onset']['worst_case_deviation_seconds'],6),e['cleanup']['status']] for e in events])
 text+=f'\nServicePause 为两个 Recommendation 副本整体暂停；PodMemoryStress 仅作用于登记的 Frontend Pod，分配 32 MiB。实际公开内存中位增量 {a["pod_effect"]["delta_bytes"]/1024**2:.3f} MiB、sibling 增量 {a["pod_effect"]["sibling_delta_bytes"]/1024**2:.3f} MiB，连续异常 {a["pod_effect"]["consecutive_abnormal_samples"]} 点并恢复。容器总增量包含 helper 开销，不冒充精确分配值。\n\n'
 text+='## 数据质量与异常草案\n\n'
 text+=table(['来源','已派发','成功','公开 Proxy Span 匹配'],[[k,v['expected'],v['http_success'],v.get('public_window_proxy_matched',0)] for k,v in a['expected_request_counts'].items()])
 text+=f'\n全部 11 个分段通过不可变 +5/+60/+120/+240 秒审计；{a["dispatch_count"]} 次派发均有终态，未完成派发 {len(a["unfinished_dispatches"])}，无法解释的 Span 缺失 {len(a["unmatched_requests"])}。真实业务失败分类：`{json.dumps(x["failed_request_classification"])}`。未发出的 session 和 MAX_TRANSITIONS 另行保留：`{json.dumps(x["session_terminal_classification"])}`，不伪造成 HTTP 请求或删除。\n\n'
 text+=f'Collector 工作集峰值 {x["collector_working_set_peak_bytes"]/1024**2:.3f} MiB，receiver refused 增量 0，UID 和配置保持不变；可选 exporter/retry 指标缺失仍为 UNKNOWN。Collector 可检索日志快照保留原始日志及统计，不能仅以未拒收替代最终存储验证。\n\n'
 text+=table(['数据','实际条数'],[['Metrics',m['row_counts']['metrics']],['Logs',m['row_counts']['logs']],['Spans',m['row_counts']['traces']],['Traces',m['unique_trace_count']]])
 text+=f'\n公开包六文件共 {sum(p.stat().st_size for p in (R/"public").iterdir()):,} bytes。60 个分钟均有三模态数据；Parquet 全量读回、UTC 纳秒类型、[start,end) 边界、Trace/Span ID、Pod UID、Trace Index、SHA-256 和泄漏扫描通过。没有向 Public 放入计划、故障数答案、调度字段或 GT。\n\n'
 text+=f'基于 Public 实际观测产生 {gt["candidate_count"]} 条异常候选，归并 {gt["merged_event_count"]} 个事件。每条候选重新解析公开证据引用。状态 **DRAFT_PENDING_HUMAN_REVIEW**，没有把两个注入直接改写成两个 GT，未发布正式评分答案。\n\n'
 text+='## 归档与资源\n\n原始、控制、执行代码、导出工具均已双副本保护。最终 Public/Private/Reports 追加后再次校验完整源目录、公开目录、私有目录和后处理代码；原始初始清单中所有文件哈希保持不变。\n\n'
 text+=table(['节点','最终系统盘剩余 GB','最终数据盘剩余 GB'],[['Master',round(s['master']['root_free']/1e9,3),round(s['master']['data_free']/1e9,3)]]+[[k,round(v['root_free']/1e9,3),round(v['data_free']/1e9,3)] for k,v in s['workers'].items()])
 text+=f'\n最终五主机环境状态 `{h["status"]}`，blockers={len(h["current_environment_blockers"])}。通用环境工具的 Watchdog 标记与实际两机制的独立回调/清理证据分开记录；本次真实 Watchdog 和恢复证据均通过。无活动故障 Timer、暂停进程、内存 holder 或网络故障残留。\n\n'
 text+='## 八题状态与剩余阻断\n\n'+table(['题号','计划故障数','实际完成故障','状态','未解决条件'],[[v['case_id'],v['planned_faults'],v['actual_faults'],v['status'],'; '.join(v['additional_blockers']) or '无'] for v in d['case_results']])
 text+='\nSF-003～009 的阻断来自尚未具备当前比赛安全实现/恢复边界的机制，未因本次磁盘门槛变更自动放行。PodCPU 的旧目标及按进程名清理、Pod/ContainerKill 的实例替换与观察器身份契约、NodeCPU 的共享监控资源隔离、Network 的监控/管理流量排除和新鲜路径证明仍需完成；多个 HTTP 响应/中止机制没有已配置的正式适配器。完整 14 机制状态见 fault-capability-matrix.json。保留原组合，不用已支持机制替换；没有启动这些题目的故障。\n\n'
 text+='两种已执行机制本轮各 1 次，并保留 SF-001 历史结果作为单独参考；不把历史次数计入本批。资源异常未产生请求失败不等于无效，当前结论依据公开资源信号；正常对照稳定不代表证明所有非目标实体均零影响。\n\n'
 text+='先前只读盘点边界事件保留在旧版报告及原 boundary-incident.json；本轮没有访问 TEST 数据/GT、没有执行 TEST。未操作比赛服务器 192.168.51.50/51，未导入数据库或发布 API。\n\n'
 text+='## 交付路径\n\n'
 for label,path in [('Public',R/'public'),('Private',R/'private'),('单题证据报告',R/'reports'),('本轮机器可读总账',V),('归档索引',I),('Worker2/Worker3 相同归档路径',Path('/data/rca-archives/ops-competition-2026/SF-PILOT-02-ATTEMPT-01'))]:text+=f'- {label}：`{path}`\n'
 text+=f'\nPublic manifest SHA-256：`{sha(R/"public/manifest.json")}`。SF-002 可进入下一阶段导入测试；本轮未进行传输或导入，GT 仍待人工审核。\n'
 out.write_text(text);out.chmod(0o600)
 case=B/'reports/SF-002-production-report.md'
 with case.open('x') as f:f.write(text)
 print(json.dumps({'report':str(out),'sha256':sha(out),'status':d['status']}))
if __name__=='__main__':main()
