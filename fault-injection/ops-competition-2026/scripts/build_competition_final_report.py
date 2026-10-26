#!/usr/bin/env python3
"""Render a factual partial-delivery report from completed evidence, never placeholders."""
import argparse
import hashlib
import json
from pathlib import Path

def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    p=argparse.ArgumentParser();p.add_argument('--summary',type=Path,required=True)
    p.add_argument('--archive',type=Path,required=True);p.add_argument('--health',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    s,archive,health=map(read,(a.summary,a.archive,a.health));cases=s['qualification_results']
    if archive['status']!='COMPETITION_DUAL_ARCHIVE_PASS':raise RuntimeError('Cannot report unverified archives')
    lines=['# SF-PILOT-01 首题故障验证与真实数据生成 — 最终报告','',
        '**最终状态：SF_PILOT_01_PARTIAL。正式一小时题目未启动，不具备导入条件。**','',
        '已完成 Recommendation Service 与 Frontend 单 Pod 的短时、300 秒独立注入。'
        '以下保留原竞赛判据的实际结果；新增端到端诊断发现正常对照的 Proxy Span 缺失，'
        '因此机制验证结果不能代表完整数据生产链路通过。没有覆盖原结果或补造数据。','',
        '历史 `faults_allowed=false` 来源已区分：`global_safety.py` 根据实际检查计算权限；科研的 '
        '`assess_precanary_safety.py` 随后为无故障研究范围无条件设置 false。竞赛没有覆盖该科研记录，'
        '每次独立执行新的五主机/资源/组件/业务检查，再结合相同适配器的 watchdog 实测生成仅对该次计划哈希有效的短期授权。'
        '初次证据过期导致的失败也保留并重新采集，没有忽略真实环境失败。','',
        '## A. 故障机制验证','',
        '| 独立验证 | 实际持续秒数 | 原判据结果 | 公开异常证据 | 撤销与恢复 |',
        '|---|---:|---|---|---|']
    for r in cases:
        d=r['decision'];e=d['public_anomaly_evidence'];inj=r['injection']
        if 'caller_p95_ms' in e:
            evidence=f"相关失败请求及错误 Span {e['failed_with_public_error_spans']} 条；调用 p95 {e['caller_p95_ms']['before']:.3f} → {e['caller_p95_ms']['during']:.3f} ms"
        else:evidence=f"目标内存中位数 +{e['during_median_increase_bytes']/2**20:.3f} MiB；sibling +{e['sibling_centered_increase_bytes']/2**20:.3f} MiB；连续异常 {e['consecutive_abnormal_samples']} 点"
        recovery=d['checks']['recovery_requests_and_resources'] and d['checks']['postfault_actual_environment']
        lines.append(f"| {r['id']} | {inj['duration_seconds']:.6f} | {d['status']} | {evidence} | {'PASS' if recovery else 'FAIL'} |")
    lines+=['','Service 为 Worker3 上两个 Recommendation 副本的整体暂停；Pod 为 Worker2 单个 Frontend 容器的固定 32 MiB 有界内存占用，Worker1 Frontend 为正常副本。未修改 cgroup 上限、Deployment 或监控配置。',
        '','实际故障起止（UTC，属于私有执行记录）：','']
    for r in cases:
        i=r['injection'];lines.append(f"- `{r['id']}`：{i['actual_start_utc']} → {i['actual_end_utc']}；清理状态 `{i['cleanup']['status']}`。")
    lines+=['','共 4 次资格注入，没有额外强度搜索。独立 systemd 恢复计时器使用 100 ms 精度；v1/v2 的 disposable 自测失败证据保留，v3 在 Worker2/Worker3 的真实进程恢复测试通过。未将 disposable 自测算作业务故障。',
        '',f"竞赛合同 SHA-256：`{s['contract_sha256']}`。",f"资格执行源锁 SHA-256：`{s['source_lock_sha256']}`；原文件逐项复核未变化。",'',
        '## B. 正式题目生成与停止原因','',
        'SF-PILOT-01/SF-001 的正式 60 分钟采集、双故障预注册和题目注入均未执行。未把多次独立资格窗口拼接成一小时，也未将资格数据改名为正式题目。',
        '', '阻断记录：`/root/ops-competition-2026/qualification/production-telemetry-readiness.json`，状态 `COMPETITION_PRODUCTION_TELEMETRY_BLOCKED`。',
        '', '1. Collector memory limiter 日志明确出现超过 soft limit 后拒收数据，拒收计数在本轮实际增长，并出现指标导出超时。',
        '2. 下表为普通 Image/Cart 对照已发请求与最终归档中 Proxy Server Span 的真实身份匹配；不把缺失当成业务故障。', '',
        '| 验证窗口 | Image 匹配/请求 | Cart 匹配/请求 | receiver refused 原始计数增量 |', '|---|---:|---:|---:|']
    for r in cases:
        im,ca=r['controls']['image_asset'],r['controls']['cart_view']
        delta=sum(x['delta'] for x in r['collector_refused_counter_deltas'])
        lines.append(f"| {r['id']} | {im['matched_proxy_trace_ids']}/{im['unique_issued_trace_ids']} | {ca['matched_proxy_trace_ids']}/{ca['unique_issued_trace_ids']} | {delta:.0f} |")
    lines+=['','Service 300 秒验证中，Image 33 条、Cart 60 条，共 93 条请求缺少 Proxy Server Span。2026-10-13 11:49:44 UTC 的只读存储复查仍缺少全部 93 条，没有用这次回溯改写冻结归档。',
        '', '拒收计数不能等同于永久丢失 Span 数量，也不能凭二者同时发生断言每条缺失都在 Collector 丢弃。Exporter failed/retry 序列缺失保留为 UNKNOWN；确证的是最终存储没有对应 Proxy Span。',
        '', '**原自动资格判据的边界：**首次、+60、+120、+240 的身份审计只验证“已经进入存储的 Span”的一致性及有界迟到，并不穷尽请求端至存储端的全部应有 Span。原资格结果不回写；本轮新增的生产阻断独立保存。',
        '', '正式题目入口已验证 fail closed：状态为 BLOCKED 时，未创建 case 目录、未登记正式计划、未发送新的故障指令。',
        '', '## C. 数据质量与原始数据量','',
        '下列均为资格验证数据，**不是正式一小时数据**。Metric 为原查询返回样本行数，含相邻 query_range 的共同端点；没有冒充去重后的 Parquet 行数。Trace 为 +120 最终版本中唯一身份计数。原始磁盘大小包含各不可变读取版本。','',
        '| 验证 | Metric 原始样本 | Logs | Trace IDs | Spans | 原始字节数 |', '|---|---:|---:|---:|---:|---:|']
    for r in cases:lines.append(f"| {r['id']} | {r['raw_metric_sample_rows_including_query_endpoints']:,} | {r['logs']:,} | {r['final_unique_traces']:,} | {r['final_unique_spans']:,} | {r['raw_bytes_including_immutable_read_versions']:,} |")
    lines+=['','各窗口原始 Metrics/Logs/Traces、Kubernetes 快照、实际负载请求、注入/清理证据均保存。没有静默删除日志、Trace 或指标，也没有把事后回溯标成实时补齐。',
        '', '正式题目 Metrics/Logs/Traces Parquet 行数、压缩大小、每分钟覆盖和导出耗时：**N/A，尚未生成**。正式题目的 CASE_DATA_PASS/REVIEW/FAIL 未授予；当前生产采集就绪状态为 BLOCKED。',
        '', '## D. 异常标注','',
        '正式一小时候选事件、已确认事件及评分 GT 数量：N/A。尚未制作或发布题目 GT，不能将两次注入直接变成两条异常答案。',
        '', '已准备的导出/标注代码均标记 DRAFT，尚未做真实一小时端到端验证。标注入口只读 Public 观测，输出 DRAFT_PENDING_HUMAN_REVIEW；本轮未运行它生成真实题目标注。',
        '', '## E. 交付与归档','',
        'Master 控制目录：`/root/ops-competition-2026/`。',
        'Master 原始数据：`/data/stage5-v3/ops-competition-2026/qualification/<验证ID>/`。',
        'Collector 诊断：`/data/stage5-v3/ops-competition-2026/audits/collector-investigation-20261013/`，完整读取日志压缩件及只读查询记录均保留。',
        '', f"双副本索引：`{archive['archive_index']}/result.json`，状态 **{archive['status']}**。", '',
        '| 归档内容 | Worker | 实际路径 | 文件数 | 字节数 | 校验 |', '|---|---|---|---:|---:|---|']
    for key,proof in archive['proofs'].items():
        lines.append(f"| {key.split('-')[0]} | {proof['host']} | `{proof['root']}` | {proof['file_count']:,} | {proof['bytes']:,} | SHA-256 + 恢复读取 PASS |")
    lines+=['','全量 source manifest 中列出每个文件的 SHA-256；两台 Worker 逐文件比对，并恢复解析真实 Metric/Log/Trace JSON 样本。Mac 未复制全量原始数据。此报告另附到两台 Worker 的 final-reports 目录。',
        '', 'Public 题目包路径：**无**。Private 正式题目标签路径：**无**。已验证的是资格证据双副本，不能当作 Public 导入包。未连接 192.168.51.51，未做导入兼容性、17 队并发或正式评分测试。',
        '', '## F. 最终安全状态与后续最小工作','',
        f"最终基础设施/五主机复查：`{health['status']}`，实际环境 blocker：`{health['current_environment_blockers']}`。该状态不覆盖上文 Collector 端到端采集缺失；未签发新的故障许可。", 
        f"Master 系统盘剩余 {s['root_free_bytes']:,} bytes；独立数据盘剩余 {s['data_free_bytes']:,} bytes（汇总时）。未降低 46,000,000,000 bytes 系统盘安全阈值。", '',
        '下一步应单独修复或解释 Collector 拒收及 issued→Proxy 缺失，并在固定负载下验证完整请求身份关联、三模态新鲜度和 +60/+120/+240 审计。不得将旧失败记录覆盖成 PASS，也不应以 Kubernetes Ready 代替端到端采集验证。',
        '', '若需要变更 Collector，应另建备份、版本和回滚验证；当前资格合同禁止本批临时修改业务/监控配置，本轮没有直接改 spike_limit_percentage 或扩大资源阈值。问题解决后再审核这组机制在新环境下的有效性，预注册并执行唯一一小时题目。',
        '', '本轮没有修改 Stage5-v1/v2 冻结资产、Stage5-v3 科研规则或历史实验，没有访问 TEST GT。',
        '', '软件检查：15 项单元测试通过；Parquet 纳秒时间戳往返的本地辅助测试通过；生产阻断入口测试通过。正式一小时执行器和比赛服务器 Schema 兼容性仍未通过真实端到端验证。',
        '', f"机器汇总 SHA-256：`{sha(a.summary)}`。", f"归档结果 SHA-256：`{sha(a.archive)}`。", '',
        '**SF_PILOT_01_PARTIAL；不输出 SF_PILOT_01_READY_FOR_IMPORT。**','']
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('x') as f:f.write('\n'.join(lines))
    print(json.dumps({'report':str(a.output),'sha256':sha(a.output),'status':'SF_PILOT_01_PARTIAL'}))

if __name__=='__main__':main()
