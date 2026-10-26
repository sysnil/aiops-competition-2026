"""GT-free temporal scouting for the organizer demo, using only Public API."""
from __future__ import annotations

import json
import math
import re
import statistics
from collections import Counter, defaultdict
from datetime import timedelta
from urllib.parse import urlsplit

from detectors.common import iso, parse_time

VERSION = 'PUBLIC_TEMPORAL_SCOUT_2.0'


def obj(value):
    if isinstance(value, str):
        try: value=json.loads(value)
        except ValueError: return {}
    return value if isinstance(value, dict) else {}


def quantile(values, q=.95):
    values=sorted(values)
    x=(len(values)-1)*q; a=int(x); b=min(a+1,len(values)-1)
    return values[a]+(values[b]-values[a])*(x-a)


def clean_groups(rows, value_key='value'):
    grouped=defaultdict(dict)
    for row in rows:
        try: value=float(row[value_key]); stamp=parse_time(row.get('timestamp') or row['start_time'])
        except (ValueError, TypeError, KeyError): continue
        if not math.isfinite(value): continue
        labels=obj(row.get('labels'))
        key=(row.get('metric_name'),row.get('pod'),row.get('service'),row.get('node'),json.dumps(labels,sort_keys=True))
        grouped[key][stamp]=row
    return [sorted(samples.values(),key=lambda r:parse_time(r['timestamp'])) for samples in grouped.values()]


def segments(samples, value, *, minimum_samples, absolute, ratio):
    if len(samples)<12: return []
    samples=sorted(samples,key=lambda r:parse_time(r.get('timestamp') or r['start_time']))
    values=[value(r) for r in samples]
    baseline=statistics.median(values)
    mad=statistics.median(abs(v-baseline) for v in values)
    threshold=baseline+max(absolute,abs(baseline)*ratio,8*1.4826*mad)
    times=[parse_time(r.get('timestamp') or r['start_time']) for r in samples]
    gaps=[(b-a).total_seconds() for a,b in zip(times,times[1:]) if b>a]
    cadence=statistics.median(gaps) if gaps else 60
    runs=[]; current=[]
    for i,v in enumerate(values):
        if v>threshold:
            if current and (times[i]-times[current[-1]]).total_seconds()>cadence*2.1:
                runs.append(current); current=[]
            current.append(i)
        elif current:
            runs.append(current);current=[]
    if current:runs.append(current)
    output=[]
    for indexes in runs:
        first,last=indexes[0],indexes[-1]
        # Both flanks are observed, never infer recovery from absent data.
        if len(indexes)<minimum_samples or first<3 or last>=len(samples)-3:continue
        if any(values[i]>threshold for i in range(max(0,first-3),first)):continue
        if any(values[i]>threshold for i in range(last+1,min(len(samples),last+4))):continue
        before=samples[max(0,first-2):first]
        during=[samples[i] for i in indexes]
        after=samples[last+1:last+3]
        output.append({'start_time':iso(times[first]),'end_time':iso(times[last+1]),
                       'baseline':baseline,'peak':max(value(r) for r in during),'threshold':threshold,
                       'evidence':before[-1:]+sorted(during,key=value,reverse=True)[:3]+after[:1],
                       'sample_count':len(indexes),'cadence_seconds':cadence})
    return output


def metric_candidates(rows):
    result=[]
    for samples in clean_groups(rows):
        row=samples[0]; name=str(row.get('metric_name') or '').lower()
        if not row.get('pod') or 'memory' not in name or 'available' in name:continue
        for change in segments(samples,lambda r:float(r['value']),minimum_samples=3,absolute=2*1024**2,ratio=.10):
            result.append({'modality':'metrics','entity_type':'Pod','entity_id':row['pod'],
                           'metric_name':row['metric_name'],'anomaly_type':'gauge_outlier',
                           'published_anomaly_type':'MEMORY_USAGE_INCREASE',
                           'start_time':change['start_time'],'end_time':change['end_time'],
                           'score':min(30,8+(change['peak']-change['baseline'])/max(change['threshold']-change['baseline'],1)),
                           'summary':f"同一实例内存持续升高，{change['sample_count']}个连续样本，基线{change['baseline']:.0f} B，峰值{change['peak']:.0f} B；前后均有正常观测",
                           'evidence_records':change['evidence'],'scout_confidence':3,
                           'coverage':'complete_metric_family_with_observed_flanks'})
    return result


def is_error(row):
    a=obj(row.get('attributes'))
    if str(row.get('status') or '').upper() in ('ERROR','STATUS_CODE_ERROR'):return True
    try:
        if int(a.get('http.status_code',0))>=500:return True
        if str(a.get('rpc.grpc.status_code','0')) not in ('','0','OK'):return True
    except (TypeError,ValueError):pass
    return a.get('error') is True


def trace_candidates(rows, *, complete=False):
    grouped=defaultdict(list)
    for row in rows:
        if not row.get('service') or not row.get('start_time'):continue
        a=obj(row.get('attributes'))
        path=urlsplit(str(a.get('http.url') or '')).path
        key=(row['service'],row.get('operation_name'),str(a.get('span.kind','')).lower(),path)
        grouped[key].append(row)
    candidates=[]
    for (service,operation,kind,path),values in grouped.items():
        buckets=defaultdict(list)
        for row in values:
            try:
                duration=float(row['duration_ms'])
                if not math.isfinite(duration) or duration<0:continue
                bucket=int(parse_time(row['start_time']).timestamp())//60
                buckets[bucket].append(row)
            except (ValueError,TypeError,KeyError):continue
        summaries=[]
        for bucket,items in sorted(buckets.items()):
            if len(items)<5:continue
            # Proxy/client and server operation populations are never mixed.
            summaries.append({'timestamp':iso(parse_time(items[0]['start_time']).replace(second=0,microsecond=0)),
                              'p95':quantile([float(r['duration_ms']) for r in items]),'rows':items})
        if complete:
            for change in segments(summaries,lambda r:r['p95'],minimum_samples=1,absolute=10,ratio=.8):
                evidence=[]
                for bucket in change['evidence']:
                    evidence.append(max(bucket['rows'],key=lambda r:float(r['duration_ms'])))
                candidates.append({'modality':'traces','entity_type':'Service','entity_id':service,
                    'anomaly_type':'trace_latency_shift','published_anomaly_type':'TRACE_LATENCY_INCREASE',
                    'start_time':change['start_time'],'end_time':change['end_time'],
                    'score':min(30,9+change['peak']/max(change['baseline'],1)),
                    'summary':f"同一操作{operation} 路径{path or '-'}的逐分钟p95由基线{change['baseline']:.2f} ms升至{change['peak']:.2f} ms，前后恢复；全窗口分页完整",
                    'evidence_records':evidence[:5],'source_service':None,'target_service':service,
                    'scout_confidence':3,'coverage':'complete_service_operation_with_observed_flanks'})
        # Explicit long ERROR spans remain genuine observations even when a
        # broad discovery page is truncated; do not infer sampled error rates.
        abnormal=[r for r in values if is_error(r) and float(r.get('duration_ms') or 0)>=5000]
        normal=[r for r in values if not is_error(r) and float(r.get('duration_ms') or 0)<1000]
        if len(abnormal)>=2 and len(normal)>=5:
            clusters=defaultdict(list)
            for r in abnormal:clusters[int(parse_time(r['start_time']).timestamp())//60].append(r)
            for _,items in clusters.items():
                if len(items)<2:continue
                start=min(parse_time(r['start_time']) for r in items)
                end=max(parse_time(r['start_time'])+timedelta(milliseconds=float(r['duration_ms'])) for r in items)
                candidates.append({'modality':'traces','entity_type':'Service','entity_id':service,
                    'anomaly_type':'extreme_error_trace_latency','published_anomaly_type':'TRACE_LATENCY_INCREASE',
                    'start_time':iso(start),'end_time':iso(end),'score':15,
                    'summary':f"同一操作{operation}出现{len(items)}条明确ERROR且耗时至少5秒的Span，另有{len(normal)}条低于1秒的正常同操作Span；不以采样量冒充全量错误率",
                    'evidence_records':normal[:1]+sorted(items,key=lambda r:float(r['duration_ms']),reverse=True)[:3]+normal[-1:],
                    'source_service':None,'target_service':service,'scout_confidence':2,'coverage':'explicit_error_duration_with_same_operation_controls'})
    return candidates


def deduplicate(candidates, maximum=6):
    chosen=[]
    for c in sorted(candidates,key=lambda r:(r.get('scout_confidence',0),r['score']),reverse=True):
        duplicate=False
        for old in chosen:
            if (c['entity_type'],c['entity_id'],c['published_anomaly_type'])!=(old['entity_type'],old['entity_id'],old['published_anomaly_type']):continue
            if max(parse_time(c['start_time']),parse_time(old['start_time']))<=min(parse_time(c['end_time']),parse_time(old['end_time']))+timedelta(seconds=60):
                duplicate=True;break
        if not duplicate:chosen.append(c)
        if len(chosen)>=maximum:break
    return chosen


def scout(agent, metadata, topology):
    start,end=metadata['start_time'],metadata['end_time']
    remaining=agent.api.request_budget-agent.api.request_count
    limit=max(0,remaining-6)
    spent=0; queries=[]; metrics=[]; logs=[]; traces=[]
    def fetch(modality,filters,max_pages):
        nonlocal spent
        rows=[]; complete=False
        for page in range(1,max_pages+1):
            if spent>=limit or agent.api.request_budget-agent.api.request_count<=6:break
            response=agent.api.get(agent.api.competition_path(f"cases/{agent.options.case_id}/{modality}"),
                                   {**filters,'page':page,'page_size':500})
            spent+=1
            items=response.payload.get('items',[])
            for raw in items:
                row=dict(raw);row['_request_id']=response.request_id;rows.append(row)
            if not response.payload.get('has_more',len(items)==500):complete=True;break
        queries.append({'modality':modality,'filters':filters,'rows':len(rows),'complete':complete})
        return rows,complete
    discovery,_=fetch('metrics',{'start':start,'end':end},1)
    families=sorted({str(r['metric_name']) for r in discovery if r.get('pod') and 'memory' in str(r.get('metric_name','')).lower()})
    candidates=[]
    for name in families[:2]:
        rows,complete=fetch('metrics',{'start':start,'end':end,'metric_name':name},14)
        metrics+=rows
        if complete:candidates+=metric_candidates(rows)
    # Uniform windows cover the whole public timeline for ERROR discovery.
    left=parse_time(start);stop=parse_time(end)
    while left<stop and spent<limit-8:
        right=min(left+timedelta(minutes=5),stop)
        rows,_=fetch('traces',{'start':iso(left),'end':iso(right),'status':'ERROR'},1)
        traces+=rows;left=right
    initial,_=fetch('traces',{'start':start,'end':end},1)
    traces+=initial
    operations=[]
    for row in initial:
        attrs=obj(row.get('attributes'))
        if str(attrs.get('span.kind','')).lower()=='server' and (attrs.get('component')=='proxy' or re.search('proxy|gateway|ingress',str(row.get('service','')),re.I)):
            pair=(row['service'],row['operation_name'])
            if pair not in operations:operations.append(pair)
    # Discover ingress roles from public span attributes, never Case IDs,
    # known fault targets, labels, injection times or a private candidate list.
    if not operations:
        for service in metadata.get('public_services',[]):
            if re.search('proxy|gateway|ingress',str(service),re.I):
                rows,_=fetch('traces',{'start':start,'end':end,'service':service},1)
                traces+=rows
                for row in rows:
                    if str(obj(row.get('attributes')).get('span.kind','')).lower()=='server':
                        pair=(service,row['operation_name'])
                        if pair not in operations:operations.append(pair)
                if operations:break
    for service,operation in operations[:1]:
        rows,complete=fetch('traces',{'start':start,'end':end,'service':service,'operation_name':operation},max(1,limit-spent-4))
        traces+=rows
        candidates+=trace_candidates(rows,complete=complete)
    # Obtain normal same-operation controls for exceptionally long errors.
    error_groups=Counter((r.get('service'),r.get('operation_name')) for r in traces if is_error(r) and float(r.get('duration_ms') or 0)>=5000)
    for (service,operation),count in error_groups.most_common(2):
        if count<2 or spent>=limit-1:continue
        rows,_=fetch('traces',{'start':start,'end':end,'service':service,'operation_name':operation},1)
        traces+=rows
    if spent<limit:
        rows,_=fetch('logs',{'start':start,'end':end,'keyword':'error'},min(2,limit-spent))
        logs+=rows
    unique={}
    for row in traces:unique[(row.get('trace_id'),row.get('span_id'))]=row
    traces=list(unique.values())
    candidates+=trace_candidates(traces,complete=False)
    candidates=deduplicate(candidates)
    report={'version':VERSION,'public_only':True,'request_count':spent,'queries':queries,
            'metric_rows':len(metrics),'log_rows':len(logs),'trace_rows':len(traces),
            'candidates':[{k:v for k,v in c.items() if k!='evidence_records'} for c in candidates]}
    (agent.options.run_dir/'public_scout.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    agent.debug['public_scout']=report
    agent.state('PUBLIC_TEMPORAL_SCOUT',{'version':VERSION,'requests':spent,'candidates':len(candidates)})
    return metrics,logs,traces,candidates
