#!/usr/bin/env python3
"""Read-only Pod-identified frontend logs from OpenSearch, without inferred severity."""

import argparse
from collections import defaultdict
from datetime import datetime, timedelta
import json
from pathlib import Path
import urllib.request


OS='http://10.111.114.255:9200'


def post(path,body):
    req=urllib.request.Request(OS+path,data=json.dumps(body).encode(),
        headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=60) as response:return json.load(response)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--attempt',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    schedule=json.loads((args.attempt/'schedule.json').read_text())
    start=datetime.fromisoformat(schedule['scheduled_start_utc'])
    end=start+timedelta(seconds=300)
    pre=json.loads((args.attempt/'preflight.json').read_text())
    known={item['uid']:item['name'] for item in pre['targets']+pre.get('controls',[])}
    query={'size':2000,'sort':['_doc'],'_source':[
        'observedTimestamp','resource','severity','body','traceId','spanId'],
        'query':{'bool':{'filter':[
            {'term':{'resource.service.name.keyword':'frontend'}},
            {'range':{'observedTimestamp':{'gte':start.isoformat(),
                                           'lt':end.isoformat()}}}]}}}
    page=post('/otel-logs-*/_search?scroll=1m',query)
    scroll=page.get('_scroll_id')
    sources=[]
    try:
        while True:
            if page.get('_shards',{}).get('failed'):
                raise RuntimeError('OpenSearch log shard failure')
            hits=page['hits']['hits']
            if not hits:break
            sources.extend(item['_source'] for item in hits)
            if len(sources)>50000:raise RuntimeError('Log cap reached, no partial analysis')
            page=post('/_search/scroll',{'scroll':'1m','scroll_id':scroll})
            scroll=page.get('_scroll_id',scroll)
    finally:
        if scroll:
            try:post('/_search/scroll',{'scroll_id':[scroll]})
            except Exception:pass
    stats=defaultdict(lambda:{'count':0,'error_keyword':0,'severity':defaultdict(int)})
    missing=0;invalid=0;examples=[]
    for row in sources:
        resource=row.get('resource') or {}
        uid=resource.get('k8s.pod.uid')
        if not uid:missing+=1;continue
        if uid not in known or resource.get('k8s.pod.name')!=known[uid]:
            invalid+=1;continue
        stamp=row.get('observedTimestamp')
        if not stamp:continue
        at=datetime.fromisoformat(stamp.replace('Z','+00:00'))
        minute=int((at-start).total_seconds()//60)
        if not 0<=minute<5:continue
        severity=row.get('severity')
        level=(severity.get('text') or severity.get('severityText') or 'UNKNOWN') if isinstance(severity,dict) else 'UNKNOWN'
        stat=stats[(uid,minute)]
        stat['count']+=1
        stat['severity'][level]+=1
        body=str(row.get('body') or '')
        if 'Error:' in body:stat['error_keyword']+=1
        if len(examples)<10:
            examples.append({'uid':uid,'pod':known[uid],'at':stamp,
                'severity':level,'body_excerpt':body[:200],
                'traceId':row.get('traceId')})
    output={'source':'OpenSearch otel-logs-* raw documents',
        'window':[start.isoformat(),end.isoformat()],
        'total_frontend_logs':len(sources),'uid_missing':missing,'uid_invalid':invalid,
        'per_pod_minute':[{'uid':uid,'pod':known[uid],'minute':minute,
            'count':value['count'],'severity':dict(value['severity']),
            'body_error_keyword_count':value['error_keyword']}
            for (uid,minute),value in sorted(stats.items())],
        'examples':examples,'severity_policy':'UNKNOWN when severity field has no explicit level'}
    (args.output/'summary.json').write_text(json.dumps(output,indent=2)+'\n')
    print(json.dumps({'total':len(sources),'uid_missing':missing,'uid_invalid':invalid,
        'pods':sorted({row['pod'] for row in output['per_pod_minute']})}))


if __name__=='__main__':main()
