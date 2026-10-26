#!/usr/bin/env python3
"""Read-only historical telemetry export; explicitly not a formal live capture."""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import urllib.parse
import urllib.request


SOURCE=Path('/root/cross-layer-workload-generation/scripts/trace-coverage.py')
spec=importlib.util.spec_from_file_location('stage2_trace_reader',SOURCE)
reader=importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)
PROM='http://10.97.14.138:9090'
OS='http://10.111.114.255:9200'
METRICS={
    'pod_cpu':'rate(container_cpu_usage_seconds_total{namespace="otel-demo",container=~"frontend|ad|payment|recommendation|checkout|shipping|product-catalog"}[1m])',
    'pod_memory':'container_memory_working_set_bytes{namespace="otel-demo",container=~"frontend|ad|payment|recommendation|checkout|shipping|product-catalog"}',
    'pod_cfs_periods':'rate(container_cpu_cfs_periods_total{namespace="otel-demo",container=~"frontend|ad|payment|recommendation"}[1m])',
    'pod_cfs_throttled':'rate(container_cpu_cfs_throttled_periods_total{namespace="otel-demo",container=~"frontend|ad|payment|recommendation"}[1m])',
    'frontend_http_count':'http_server_duration_milliseconds_count{service_name="frontend"}',
    'frontend_http_bucket':'http_server_duration_milliseconds_bucket{service_name="frontend"}',
    'pod_network_tx':'rate(container_network_transmit_bytes_total{namespace="otel-demo",pod!=""}[1m])'}


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def request_json(url,body=None):
    req=urllib.request.Request(url,data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type':'application/json'} if body is not None else {})
    with urllib.request.urlopen(req,timeout=90) as response:return json.load(response)


def logs(start,end):
    body={'size':2000,'sort':['_doc'],
          'query':{'range':{'observedTimestamp':{'gte':start.isoformat(),
                                                'lt':end.isoformat()}}}}
    page=request_json(OS+'/otel-logs-*/_search?scroll=1m',body)
    scroll=page.get('_scroll_id')
    output=[]
    try:
        while True:
            if page.get('_shards',{}).get('failed'):raise RuntimeError('OpenSearch log shard failure')
            hits=page['hits']['hits']
            if not hits:break
            output.extend({'index':row['_index'],'id':row['_id'],'source':row['_source']}
                          for row in hits)
            if len(output)>50000:raise RuntimeError('Log cap reached, no partial capture')
            page=request_json(OS+'/_search/scroll',{'scroll':'1m','scroll_id':scroll})
            scroll=page.get('_scroll_id',scroll)
    finally:
        if scroll:
            try:request_json(OS+'/_search/scroll',{'scroll_id':[scroll]})
            except Exception:pass
    return output


def prom(expression,start,end):
    params=urllib.parse.urlencode({'query':expression,'start':start.timestamp(),
                                   'end':end.timestamp(),'step':15})
    result=request_json(PROM+'/api/v1/query_range?'+params)
    if result.get('status')!='success' or result['data']['resultType']!='matrix':
        raise RuntimeError('Prometheus historical range query failed')
    return result


def relevant_uids(preflight):
    uids=set()
    for row in preflight.get('targets',[])+preflight.get('controls',[]):
        uids.add(row['uid'])
    for row in preflight.get('verified_traversing_edges',[]):
        uids.update((row['source_pod_uid'],row['destination_pod_uid']))
    for row in preflight.get('qualified_local_controls',[]):
        uids.update((row['source_uid'],row['destination_uid']))
    return uids


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--attempt',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():raise ValueError('Refusing overwrite')
    schedule=json.loads((args.attempt/'schedule.json').read_text())
    preflight=json.loads((args.attempt/'preflight.json').read_text())
    start=datetime.fromisoformat(schedule['scheduled_start_utc'])
    windows={'before':(start,start+timedelta(seconds=120)),
             'during':(start+timedelta(seconds=120),start+timedelta(seconds=180)),
             'after':(start+timedelta(seconds=180),start+timedelta(seconds=300))}
    if datetime.now(timezone.utc)<windows['after'][1]:raise ValueError('After window incomplete')
    all_pods=json.loads(subprocess.check_output(['kubectl','-n','otel-demo','get','pods','-o','json'],
                                                text=True,timeout=30))['items']
    by_uid={pod['metadata']['uid']:pod for pod in all_pods}
    identities={}
    for uid in sorted(relevant_uids(preflight)):
        pod=by_uid.get(uid)
        identities[uid]={'present_after':bool(pod),
            'pod_name':pod['metadata']['name'] if pod else None,
            'node':pod['spec']['nodeName'] if pod else None,
            'created_before_window':bool(pod and datetime.fromisoformat(
                pod['metadata']['creationTimestamp'].replace('Z','+00:00'))<=start)}
    if not identities or not all(row['present_after'] and row['created_before_window']
                                 for row in identities.values()):
        raise ValueError('Pod UID continuity cannot be verified retrospectively')
    args.output.mkdir(parents=True)
    summary={'status':'RETROSPECTIVE_TELEMETRY_COMPLETE',
        'warning':'Not a Stage4F live capture; Kubernetes identity is proven by immutable UID continuity, not a phase-time snapshot',
        'trace_source':'JAEGER_OPENSEARCH_STORAGE','trace_reader_sha256':sha(SOURCE),
        'script_sha256':sha(Path(__file__)),'captured_at_utc':datetime.now(timezone.utc).isoformat(),
        'pod_uid_continuity':identities,'phases':{}}
    for phase,(begin,end) in windows.items():
        directory=args.output/phase
        directory.mkdir()
        metrics={name:prom(query,begin,end) for name,query in METRICS.items()}
        metrics_path=directory/'prometheus.json'
        metrics_path.write_text(json.dumps(metrics)+'\n')
        log_rows=logs(begin,end)
        logs_path=directory/'opensearch-logs.jsonl'
        with logs_path.open('x') as stream:
            for row in log_rows:stream.write(json.dumps(row,separators=(',',':'))+'\n')
        query={'range':{'startTimeMillis':{'gte':int(begin.timestamp()*1000),
                                           'lt':int(end.timestamp()*1000)}}}
        spans,total=reader.scan(query)
        spans_path=directory/'jaeger-storage-spans.jsonl'
        with spans_path.open('x') as stream:
            for span in spans:stream.write(json.dumps(span,separators=(',',':'))+'\n')
        summary['phases'][phase]={'window':[begin.isoformat(),end.isoformat()],
            'prometheus_queries':list(METRICS),'prometheus_sha256':sha(metrics_path),
            'log_documents':len(log_rows),'logs_sha256':sha(logs_path),
            'span_documents':len(spans),'span_total_reported':total,
            'spans_sha256':sha(spans_path)}
    (args.output/'capture-manifest.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps({'status':summary['status'],'phases':{
        phase:{'spans':row['span_documents'],'logs':row['log_documents']}
        for phase,row in summary['phases'].items()}}))


if __name__=='__main__':main()
