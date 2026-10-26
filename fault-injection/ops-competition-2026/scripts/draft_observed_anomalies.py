#!/usr/bin/env python3
"""Draft anomaly events from public Parquet only, never from an injection schedule."""
import argparse
from collections import defaultdict
from datetime import datetime,timezone
import json
from pathlib import Path
import statistics
from contest_exchange_schema import iso_ns,canonical
from competition_archive import sha

def utc(ns):return datetime.fromtimestamp(ns/1e9,timezone.utc).isoformat()
def p95(values):
    values=sorted(values);return values[int((len(values)-1)*.95)] if values else None
def error(a):
    if a.get('error') in (True,'true',1) or str(a.get('otel.status_code','')).upper()=='ERROR':return True
    for k in ('http.status_code','http.response.status_code'):
        try:
            if int(a.get(k,0))>=400:return True
        except (ValueError,TypeError):pass
    try:return int(a.get('rpc.grpc.status_code',0))!=0
    except (ValueError,TypeError):return False
def rows(file,columns):
    import pyarrow.parquet as pq
    import pyarrow as pa
    for batch in pq.ParquetFile(file).iter_batches(batch_size=5000,columns=columns):
        names=batch.schema.names
        converted={k:(batch.column(i).cast(pa.int64()).to_pylist() if pa.types.is_timestamp(batch.column(i).type) else batch.column(i).to_pylist()) for i,k in enumerate(names)}
        for i in range(batch.num_rows):yield {k:v[i] for k,v in converted.items()}

def main():
    p=argparse.ArgumentParser();p.add_argument('--public',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    if a.output.exists():raise RuntimeError('No overwrite of annotation draft')
    a.output.mkdir(parents=True,mode=0o700)
    manifest=json.loads((a.public/'manifest.json').read_text());start=iso_ns(manifest['window']['start_utc'])
    for name,item in manifest['files'].items():
        if sha(a.public/name)!=item['sha256']:raise RuntimeError('Public source hash changed')
    minute=lambda ns:int((ns-start)//(60*10**9));base_ns=start+600*10**9
    memory=defaultdict(list);log_counts=defaultdict(lambda:defaultdict(list));trace=defaultdict(lambda:defaultdict(list))
    for r in rows(a.public/'metrics.parquet',['timestamp','metric_name','value','labels']):
        if r['metric_name']!='container_memory_working_set_bytes':continue
        labels=json.loads(r['labels'])
        if labels.get('container') in (None,'','POD'):continue
        memory[(labels.get('namespace'),labels.get('pod'),labels.get('id'))].append((r['timestamp'],r['value'],labels))
    for r in rows(a.public/'logs.parquet',['timestamp','service','severity','body','trace_id','span_id']):
        if str(r['severity']).upper() not in ('ERROR','FATAL','CRITICAL'):continue
        log_counts[r['service']][minute(r['timestamp'])].append(r)
    for r in rows(a.public/'traces.parquet',['start_time','service','operation_name','duration_ms','attributes','trace_id','span_id']):
        attrs=json.loads(r['attributes']);kind=attrs.get('span.kind','UNKNOWN')
        key=(r['service'],r['operation_name'],kind)
        r['is_error']=error(attrs);trace[key][minute(r['start_time'])].append(r)
    events=[]
    def append(entity_type,entity_id,kind,points,evidence,confidence='MEDIUM',extra=None):
        if not points:return
        points=sorted(points);groups=[]
        for ts in points:
            if not groups or ts-groups[-1][-1]>90*10**9:groups.append([ts])
            else:groups[-1].append(ts)
        for group in groups:
            lo,hi=group[0],group[-1]
            events.append({'anomaly_id':f'ANOM-{len(events)+1:04d}','entity_type':entity_type,'entity_id':entity_id,
                'anomaly_type':kind,'start_time':utc(lo),'end_time':utc(hi),
                'boundary_semantics':'first and last observed abnormal samples; not injection boundaries',
                'modalities':[evidence['modality']],'evidence':{**evidence,'observed_time_filter':{'from':utc(lo),'through':utc(hi)}},
                'confidence':confidence,'review_status':'DRAFT_PENDING_HUMAN_REVIEW','details':extra or {}})
    topology=json.loads((a.public/'topology.json').read_text());uids=defaultdict(set)
    for snapshot in topology['snapshots']:
        for pod in snapshot['pods']:uids[(pod['namespace'],pod['name'])].add(pod['uid'])
    for (ns,pod,cgroup),samples in memory.items():
        baseline=[v for t,v,_ in samples if t<base_ns]
        if len(baseline)<10:continue
        center=statistics.median(baseline);mad=statistics.median(abs(v-center) for v in baseline);threshold=max(16*1024**2,5*mad)
        abnormal=[(t,v) for t,v,_ in sorted(samples) if t>=base_ns and v>=center+threshold]
        # Require consecutive public samples; isolated points remain unlabelled.
        times=[t for i,(t,v) in enumerate(abnormal) if (i and t-abnormal[i-1][0]<=30*10**9) or (i+1<len(abnormal) and abnormal[i+1][0]-t<=30*10**9)]
        identity=next(iter(uids[(ns,pod)])) if len(uids[(ns,pod)])==1 else f'{ns}/{pod}'
        append('POD',identity,'MEMORY_WORKING_SET_INCREASE',times,
            {'modality':'Metrics','file':'metrics.parquet','metric':'container_memory_working_set_bytes',
             'labels_filter':{'namespace':ns,'pod':pod,'id':cgroup},'baseline_median_bytes':center,'baseline_mad_bytes':mad,
             'increase_threshold_bytes':threshold,'abnormal_samples':len(times)},extra={'pod_name':pod})
    for (service,operation,kind),bins in trace.items():
        baseline=[r for i,items in bins.items() if i<10 for r in items]
        if len(baseline)<10:continue
        base=p95([r['duration_ms'] for r in baseline]);base_rate=sum(r['is_error'] for r in baseline)/len(baseline)
        for typ in ('TRACE_LATENCY_INCREASE','TRACE_ERROR_BURST'):
            points=[];examples=[];details=[]
            for i,items in sorted(bins.items()):
                if i<10:continue
                latency=p95([r['duration_ms'] for r in items]);errors=[r for r in items if r['is_error']]
                bad=(len(items)>=10 and latency>=max(base*2,base+10)) if typ=='TRACE_LATENCY_INCREASE' else (len(errors)>=3 and len(errors)/len(items)>base_rate+.05)
                if not bad:continue
                selected=errors if typ=='TRACE_ERROR_BURST' else [r for r in items if r['duration_ms']>=max(base*2,base+10)]
                points.extend(r['start_time'] for r in selected)
                examples.extend({'trace_id':r['trace_id'],'span_id':r['span_id'],'duration_ms':r['duration_ms']} for r in selected[:3])
                details.append({'minute':i,'spans':len(items),'errors':len(errors),'p95_ms':latency})
            append('SERVICE',service,typ,points,{'modality':'Traces','file':'traces.parquet',
                'filter':{'service':service,'operation_name':operation,'span.kind':kind},
                'baseline_p95_ms':base,'baseline_error_fraction':base_rate,'examples':examples[:20],'minute_statistics':details})
    for service,bins in log_counts.items():
        base=statistics.mean(len(bins.get(i,[])) for i in range(10));points=[];examples=[]
        for i,items in bins.items():
            if i>=10 and len(items)>=max(3,base*3+3):
                points.extend(r['timestamp'] for r in items)
                examples.extend({k:r[k] for k in ('trace_id','span_id','body')} for r in items[:2])
        append('SERVICE',service,'EXPLICIT_ERROR_LOG_INCREASE',points,{'modality':'Logs','file':'logs.parquet',
            'filter':{'service':service,'severity':['ERROR','FATAL','CRITICAL']},'baseline_per_minute':base,'examples':examples[:10]})
    result={'status':'DRAFT_PENDING_HUMAN_REVIEW','source_public_manifest_sha256':sha(a.public/'manifest.json'),
        'injection_plan_or_gt_read':False,'normal_reference':'first ten minutes of the registered observation hour',
        'independent_baseline_statistical_validation':False,'candidate_count':len(events),'events':events,
        'event_overlap_policy':'modality/operation events may overlap; human merge required before scoring',
        'absence_of_anomaly_candidate':'not proof of absence of anomaly','formal_scoring_gt':False}
    for name in ('anomaly-candidates.json','anomaly-gt-draft.json'):
        with (a.output/name).open('x') as f:json.dump(result,f,indent=2,ensure_ascii=False);f.write('\n')
    (a.output/'review-notes.md').write_text('All labels are DRAFT_PENDING_HUMAN_REVIEW. Detection read only public observations, never the injection schedule. The first ten minutes are an in-case reference, not an independent statistical baseline. Review overlapping service/operation/modality events, confirm time boundaries and merge duplicate symptoms before constructing a scoring GT. Missing/error spans and unparseable log levels must remain explicit unknowns.\n')
    print(json.dumps({'status':result['status'],'candidate_count':len(events),'injection_plan_or_gt_read':False}))

if __name__=='__main__':main()
