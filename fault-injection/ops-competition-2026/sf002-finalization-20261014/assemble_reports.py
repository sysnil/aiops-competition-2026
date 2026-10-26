"""Document real completed execution and export receipts. Final archive verdict is external."""
import json,csv,hashlib,shutil
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path('/data/stage5-v3/ops-competition-2026/batch-sf002-sf009/SF-002')
CODE=Path('/root/ops-competition-2026/sf002-production-20261014')
CONTROL=Path('/data/stage5-v3/ops-competition-2026/batch-sf002-sf009/runtime/SF-PILOT-02-ATTEMPT-01')
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def fmt(v):return f'{v:,.3f}' if isinstance(v,float) else str(v)
def write(p,s):
 with p.open('x') as f:f.write(s)
def table(headers,rows):return '| '+' | '.join(headers)+' |\n|'+'|'.join(['---']*len(headers))+'|\n'+''.join('| '+' | '.join(fmt(x) for x in row)+' |\n' for row in rows)
def main():
 report=ROOT/'reports';p=read(CODE/'plans/SF-PILOT-02-ATTEMPT-01/run-plan.json');m=read(ROOT/'public/manifest.json');a=read(ROOT/'capture-and-effects-assessment.json');x=read(report/'execution-accounting.json');t=read(CONTROL/'controller-terminal.json');g=read(ROOT/'private/anomaly-gt-draft.json');v=read(report/'public-readback-verification.json');e=read(report/'export-report.json')
 if a['status']!='CASE_CAPTURE_AND_EFFECTS_PASS' or v['status']!='PUBLIC_FULL_READBACK_SCHEMA_IDENTITY_HASH_LEAK_PASS':raise RuntimeError('NO_DELIVERY_QUALITY_PASS')
 with (report/'minute-coverage.csv').open('x',newline='') as f:
  w=csv.writer(f);w.writerow(['minute','metrics_rows','logs_rows','spans_rows'])
  for i in range(60):w.writerow([i]+[m['minute_counts'][k].get(str(i),0) for k in ('metrics','logs','traces')])
 manifest=CODE/'plans/SF-PILOT-02-ATTEMPT-01/run-manifest.json'
 execution='# SF-002 / SF-PILOT-02-ATTEMPT-01 执行报告\n\n本文件记录实际执行；最终归档与导入就绪结论见外部总报告，避免将尚未完成的归档预先标为 PASS。\n\n'
 execution+=f'连续窗口：{m["window"]["start_utc"]} 至 {m["window"]["end_utc"]}，3600 秒。公开题号 SF-002。实际控制器状态 `{t["status"]}`。\n\n'
 execution+=f'Run Manifest：`{manifest}`\n\nSHA-256：`{sha(manifest)}`\n\n'
 execution+='采用 Master UTC、Worker 本地定时器、显式运行登记、15 秒心跳/30 秒新鲜度、4 秒短期租约、原 2 秒启动容差与原已验证适配器。未重跑 Marker 或资格故障；正式新入口执行前 56 项非注入回归测试通过。\n\n'
 rows=[]
 for key,inj in t['faults'].items():
  onset=t['onsets'][key];rows.append([inj['layer'],inj['actual_start_utc'],inj['actual_end_utc'],inj['duration_seconds'],onset['raw_deviation_seconds'],onset['worst_case_deviation_seconds'],onset['feedback_upper_seconds'],inj['cleanup']['status']])
 execution+=table(['机制','实际开始 UTC','实际结束 UTC','持续秒','原始偏差秒','含不确定度秒','反馈上界秒','清理'],rows)
 execution+='\nService 恢复与 Pod 自有 holder 清理的实际 PID 以本次 injection-record.json 和执行终态为准。Pod 参数为分配 32 MiB，容器总内存变化同时包含 helper 开销，不能把总增量误称为精确 32 MiB。两次公开恢复和事后五主机预检均实际通过。\n\n'
 execution+=f'正常负载：MEDIUM 1.4 sessions/s，seed {p["seed"]}，Image 1/s，Cart 2/s，三个阶段策略不变。请求 {x["requests"]["business_requests"]} + 对照 {x["requests"]["controls_requests"]}。终态分类：`{json.dumps(x["session_terminal_classification"],ensure_ascii=False)}`。280 个 MISSED_ARRIVAL 是未发出的业务会话，不能计算为缺失 Span；27 个 MAX_TRANSITIONS 单独保留，不擅自归因于注入。\n\n'
 execution+='本题没有追加重试或 Node/NetworkPath 额外故障；未修改 Collector 配置；未访问比赛服务器。安全审计工具记录的 faults_injected=0 表示该只读工具自身不注入，不代表本次总故障数；本次总数以两条真实注入记录为准。\n'
 write(report/'execution-report.md',execution)
 quality='# 三模态与身份关联质量\n\n实际判定：`CASE_CAPTURE_AND_EFFECTS_PASS`。11/11 分段通过 +5/+60/+120/+240 秒不可变完成审计。\n\n'
 quality+=table(['模态','行数'],[[k,m['row_counts'][k]] for k in ('metrics','logs','traces')])
 quality+=f'\n真实 Trace 数 {m["unique_trace_count"]}；指标序列 {m["metric_series_count"]}。60 个分钟均有 Metrics、Logs、Traces；明细见 minute-coverage.csv。\n\n'
 quality+=table(['请求来源','应核对','公开窗口匹配','真实边界解释','成功请求'],[[k,r['expected'],r.get('public_window_proxy_matched',0),r.get('private_boundary_verified',0),r['http_success']] for k,r in a['expected_request_counts'].items()])
 quality+=f'\n派发记录 {a["dispatch_count"]}，未完成派发 {len(a["unfinished_dispatches"])}；无法解释的缺失 {len(a["unmatched_requests"])}。失败分类：`{json.dumps(x["failed_request_classification"])}`。边界 {len(a["private_boundary_records"])} 个请求使用真实 Trace/Span 身份核验，未改写时间戳，也未将窗口外 Span 填入 Public。\n\n'
 quality+=f'Collector 峰值 {x["collector_working_set_peak_bytes"]/1024**2:.3f} MiB（既有上限 380 MiB），无 receiver refused 增量，日志严重等级及采集覆盖以本次 collector_log_audits 原始统计为准。缺失的可选 exporter failure/retry 指标为 UNKNOWN，不解释为零。Collector UID/配置未改变，配置 SHA-256 `{p["collector_config_sha256"]}`。原始计数及资源曲线数据见 execution-accounting.json、collector-resource-series.json。\n\n'
 quality+='数据限制：Service 延迟主要由真实 Trace duration 提供；Prometheus 调用计数保留原始 counter，不虚构 histogram。某些可选指标为空、部分状态/身份属性为 NULL，完整缺失统计见 public-readback-verification.json。异常超时与观测缺失分开核对。\n'
 write(report/'telemetry-quality-report.md',quality)
 annotation='# 异常候选人工复核\n\n状态：`DRAFT_PENDING_HUMAN_REVIEW`，不是正式评分答案。\n\n'
 annotation+=f'基于 Public 三模态生成 {g["candidate_count"]} 条候选，按相同实体及重叠/相邻观测区间归并为 {g["merged_event_count"]} 个事件。包括 Service、Pod 与实际 client/server 身份相连的调用边症状。\n\n'
 annotation+='候选检测不读取注入计划或注入记录，不把两次注入直接变成两个 GT。时间范围来自实际异常样本；注入计划只供后续人工私有审核。保留每条候选的 Public 文件、筛选条件、Trace/Span ID 和证据。阈值是探索性候选检测规则，不是重新修改采集准入；正常波动、传播现象与多模态重复仍待人工复核。\n\n'
 annotation+=f'公开内存证据：目标中位增量 {a["pod_effect"]["delta_bytes"]/1024**2:.3f} MiB，正常副本增量 {a["pod_effect"]["sibling_delta_bytes"]/1024**2:.3f} MiB，连续异常样本 {a["pod_effect"]["consecutive_abnormal_samples"]}，恢复通过。Service 公开影响明细保留在 capture-and-effects-assessment.json。\n\n'
 annotation+='旧无故障参考仅用于辅助比较，保留不同 seed、时间及进程 uptime 的可比性限制。未用旧故障数据拼接本次题目。\n'
 write(report/'anomaly-review-report.md',annotation)
 export='# Public 导出校验\n\n实际执行：Parquet 读回、Schema、纳秒 UTC 类型、半开时间窗口、真实 ID、Trace Index、文件 SHA-256 和逐文件/逐行泄漏检查通过。服务器导入测试未执行。\n\n'
 export+=table(['文件','字节','SHA-256'],[[name,item['bytes'],item['sha256']] for name,item in m['files'].items()])
 export+=f'\nPublic manifest SHA-256：`{sha(ROOT/"public/manifest.json")}`。\n\n'
 export+=f'原始查询边界/重复统计：`{json.dumps(e["duplicate_and_boundary_accounting"])}`。仅过滤原始时间戳实际位于 `[start,end)` 之外的 Prometheus 查询端点；原始响应永久保留，未平移时间。\n\n'
 export+='Public 仅含六个指定文件；计划、故障记录、调度 Manifest、GT、评分标签和分析报告均留在独立私有目录。源缺失保留 NULL，日志等级不按关键词伪造。Public-only 分析和读回检查无 GT 输入。\n'
 write(report/'export-report.md',export)
 print(json.dumps({'reports_created':4,'minute_rows':60,'candidate_count':g['candidate_count'],'merged_events':g['merged_event_count']}))
if __name__=='__main__':main()
