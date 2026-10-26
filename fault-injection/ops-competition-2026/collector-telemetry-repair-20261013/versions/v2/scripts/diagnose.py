#!/usr/bin/env python3
"""New immutable read-only evidence; historical requests are never rewritten."""
import gzip,json,urllib.parse
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path
import yaml
from common import *

def main():
    out=DATA/'diagnosis-v3'
    if out.exists():raise RuntimeError('Never overwrite diagnosis')
    out.mkdir(parents=True,mode=0o700)
    configs={}
    for kind,name in [('configmap','otel-collector'),('deployment','otel-collector'),('service','otel-collector'),
        ('deployment','frontend-proxy'),('configmap','s5v3ap-frontend-proxy-idle-c2'),('deployment','frontend'),
        ('configmap','jaeger'),('deployment','jaeger'),('configmap','otel-cluster-collector')]:
        try:configs[kind+'-'+name]=obj(kind,name);save(out/(kind+'-'+name+'.json'),configs[kind+'-'+name])
        except Exception as e:save(out/(kind+'-'+name+'-unavailable.json'),{'error':str(e)})
    for name in ['pods','services','endpointslices']:
        save(out/(name+'.json'),obj(name,''))
    for prefix in ['otel-collector-','frontend-proxy-','jaeger-']:
        p=pod(prefix);save(out/(prefix+'pod.json'),p)
        raw=kube('logs',p['metadata']['name'],'--since=3h')
        with gzip.open(out/(prefix+'logs.gz'),'wt') as f:f.write(raw)
    try:
        (out/'proxy-effective-config.json').write_text(admin('config_dump'))
        (out/'proxy-stats.txt').write_text(admin('stats?filter=tracing%7Copentelemetry_collector_grpc'))
    except Exception as e:save(out/'proxy-admin-unavailable.json',{'error':str(e)})
    source=Path('/data/stage5-v3/ops-competition-2026/audits/collector-late-diagnostic-20261013.json')
    requests=read(source)['issued_records'];ids=sorted(requests)
    spans=http(OS+'/jaeger-jaeger-span-2026-10-13/_search',{'size':10000,'track_total_hits':True,'query':{'terms':{'traceID':ids}}})
    logs=http(OS+'/otel-logs-*/_search',{'size':10000,'track_total_hits':True,'query':{'terms':{'traceId.keyword':ids}}})
    for name,v in [('historical-span-query',spans),('historical-log-query',logs)]:
        if v['_shards']['total']<1 or v['_shards']['failed'] or v['hits']['total']['relation']!='eq' or len(v['hits']['hits'])!=v['hits']['total']['value']:raise RuntimeError('Incomplete forensic query')
        save(out/(name+'.json'),v)
    bys=defaultdict(list);byl=defaultdict(list)
    for h in spans['hits']['hits']:bys[h['_source']['traceID'].lower()].append(h['_source'])
    for h in logs['hits']['hits']:byl[h['_source']['traceId'].lower()].append(h['_source'])
    per=[]
    for tid in ids:
        ss=bys[tid];ll=byl[tid];access=[r for r in ll if r.get('resource',{}).get('service.name')=='frontend-proxy' and r.get('attributes',{}).get('event.name')=='proxy.access']
        per.append({'trace_id':tid,'issued':requests[tid],'proxy_span_count':sum(s['process']['serviceName']=='frontend-proxy' and s.get('tag',{}).get('span@kind')=='server' for s in ss),
            'stored_services':dict(Counter(s['process']['serviceName'] for s in ss)),
            'proxy_access_logs':access,'access_log_context_proves_finished_span_export':False,
            'exact_drop_location':'UNRESOLVED_WITHOUT_PER_ID_RECEIVER_EXPORTER_ACK'})
    end=datetime.now(timezone.utc).timestamp();start=end-7200
    names=['otelcol_receiver_accepted_spans_total','otelcol_receiver_refused_spans_total','otelcol_processor_memory_limiter_refused_spans_total',
        'otelcol_receiver_refused_metric_points_total','otelcol_receiver_refused_log_records_total','otelcol_process_runtime_heap_alloc_bytes',
        'otelcol_process_memory_rss_bytes','otelcol_exporter_queue_size','otelcol_exporter_queue_capacity',
        'otelcol_exporter_sent_spans_total','otelcol_exporter_send_failed_spans_total','otelcol_exporter_enqueue_failed_spans_total',
        'otelcol_exporter_sent_metric_points_total','otelcol_exporter_send_failed_metric_points_total',
        'otelcol_exporter_sent_log_records_total','otelcol_exporter_send_failed_log_records_total',
        'container_memory_working_set_bytes{namespace="otel-demo",pod=~"otel-collector-.*",container="opentelemetry-collector"}',
        'rate(container_cpu_usage_seconds_total{namespace="otel-demo",pod=~"otel-collector-.*",container="opentelemetry-collector"}[1m])']
    stats={}
    for i,q in enumerate(names):
        result=http(PROM+'/api/v1/query_range?'+urllib.parse.urlencode({'query':q,'start':start,'end':end,'step':10}))
        save(out/'prometheus'/f'{i:02d}.json',{'query':q,'response':result})
        stats[q]=[{'labels':r['metric'],'samples':len(r['values']),'first':r['values'][0],'last':r['values'][-1],
            'max':max(float(v) for _,v in r['values'])} for r in result['data']['result']]
    save(out/'opensearch-node-stats.json',http(OS+'/_nodes/stats/jvm,thread_pool,indices'))
    relay=yaml.safe_load(configs['configmap-otel-collector']['data']['relay'])
    container=configs['deployment-otel-collector']['spec']['template']['spec']['containers'][0]
    summary={'status':'READ_ONLY_DIAGNOSIS_COMPLETE','at_utc':now(),'historical_requests':len(ids),
        'proxy_access_log_matched':sum(bool(r['proxy_access_logs']) for r in per),
        'proxy_span_matched_now':sum(r['proxy_span_count']>0 for r in per),'per_request':per,
        'collector_limiter':relay['processors']['memory_limiter'],'collector_batch':relay['processors']['batch'],
        'resources':container['resources'],'gomemlimit':next(r['value'] for r in container['env'] if r['name']=='GOMEMLIMIT'),
        'pipelines':relay['service']['pipelines'],'exporters':relay['exporters'],'otlp_receivers':relay['receivers']['otlp'],
        'metric_statistics':stats,'absent_metric_policy':'UNKNOWN_NOT_ZERO',
        'no_trace_sampling_or_drop_filter_in_gateway':not any('sampl' in r or 'filter' in r for r in relay['service']['pipelines']['traces']['processors']),
        'historical_source_sha256':sha(source),'faults_injected':0,'configuration_changed':False}
    save(out/'summary.json',summary)
    save(out/'manifest.json',{'at_utc':now(),'sha256':{str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file()}})
    print(json.dumps({k:summary[k] for k in ['status','historical_requests','proxy_access_log_matched','proxy_span_matched_now','collector_limiter','resources','gomemlimit']}))

if __name__=='__main__':main()
