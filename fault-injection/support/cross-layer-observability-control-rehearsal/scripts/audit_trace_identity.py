#!/usr/bin/env python3
"""Compare raw Jaeger OpenSearch span resource UIDs to current Kubernetes Pods."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import subprocess


SOURCE=Path('/root/cross-layer-workload-generation/scripts/trace-coverage.py')
spec=importlib.util.spec_from_file_location('stage2_trace_reader',SOURCE)
reader=importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)


def kube_pods():
    raw=subprocess.check_output(['kubectl','-n','otel-demo','get','pods','-o','json'],
                                text=True,timeout=30)
    return json.loads(raw)['items']


def attrs(span):
    return {row.get('key'):row.get('value') for row in span.get('process',{}).get('tags',[])}


def is_business_server(span):
    if (span.get('tag') or {}).get('span@kind')!='server':
        return False
    name=span.get('operationName','').lower()
    return not any(word in name for word in ('/metrics','/health','/ready','feature_flag'))


def quantile(values,p):
    ordered=sorted(values)
    return ordered[min(len(ordered)-1,int((len(ordered)-1)*p))] if ordered else None


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--start-utc',required=True)
    parser.add_argument('--end-utc',required=True)
    parser.add_argument('--services',default='frontend,ad,payment,recommendation')
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    start=datetime.fromisoformat(args.start_utc.replace('Z','+00:00'))
    end=datetime.fromisoformat(args.end_utc.replace('Z','+00:00'))
    if not start<end or end>datetime.now(timezone.utc):
        raise ValueError('Audit requires a completed UTC window')
    services=[name.strip() for name in args.services.split(',') if name.strip()]
    pods=kube_pods()
    by_uid={item['metadata']['uid']:item for item in pods}
    query={'bool':{'filter':[
        {'range':{'startTimeMillis':{'gte':int(start.timestamp()*1000),
                                      'lt':int(end.timestamp()*1000)}}},
        {'terms':{'process.serviceName':services}}]}}
    spans,total=reader.scan(query)
    grouped=defaultdict(list)
    for span in spans:
        if is_business_server(span):
            grouped[span.get('process',{}).get('serviceName')].append(span)
    result={'source':'JAEGER_OPENSEARCH_STORAGE/raw_span_documents',
        'window_start':start.isoformat(),'window_end':end.isoformat(),
        'services':{},'queried_span_count':len(spans),
        'business_server_span_count':sum(len(rows) for rows in grouped.values()),
        'opensearch_total':total,'uid_matched_business_server_spans':0,
        'uid_present_business_server_spans':0,'uid_unmatched_business_server_spans':0}
    examples=[]
    for service in services:
        rows=grouped.get(service,[])
        per_pod=defaultdict(list)
        missing=[];invalid=[]
        for span in rows:
            tags=attrs(span)
            uid=tags.get('k8s.pod.uid')
            name=tags.get('k8s.pod.name')
            if not uid or not name:
                missing.append(span['spanID'])
                continue
            result['uid_present_business_server_spans']+=1
            pod=by_uid.get(uid)
            if (not pod or pod['metadata']['name']!=name or
                    pod['metadata']['labels'].get('opentelemetry.io/name')!=service):
                invalid.append({'traceID':span['traceID'],'spanID':span['spanID'],
                                'uid':uid,'name':name})
                result['uid_unmatched_business_server_spans']+=1
                continue
            result['uid_matched_business_server_spans']+=1
            per_pod[uid].append(span)
            if not any(item['service']==service and item['pod_uid']==uid for item in examples):
                examples.append({'service':service,'pod_name':name,'pod_uid':uid,
                    'traceID':span['traceID'],'spanID':span['spanID'],
                    'operationName':span['operationName'],
                    'startTimeMillis':span['startTimeMillis'],
                    'duration_microseconds':span.get('duration'),
                    'process_tags':span.get('process',{}).get('tags',[])})
        pod_data={}
        for uid,items in per_pod.items():
            durations=[max(0,span.get('duration',0))/1000 for span in items]
            errors=0;status_known=0
            for span in items:
                st={row.get('key'):row.get('value') for row in span.get('tags',[])}
                http=st.get('http.status_code',st.get('http.response.status_code'))
                grpc=st.get('rpc.grpc.status_code')
                if http is not None:
                    status_known+=1
                    try: errors+=int(http)>=400
                    except (TypeError,ValueError): pass
                elif grpc is not None:
                    status_known+=1
                    try: errors+=int(grpc)!=0
                    except (TypeError,ValueError): pass
            pod_data[uid]={'pod_name':by_uid[uid]['metadata']['name'],
                'node':by_uid[uid]['spec']['nodeName'],'business_server_spans':len(items),
                'p50_ms_successful_spans_only':quantile(durations,.5),
                'p95_ms_successful_spans_only':quantile(durations,.95),
                'status_known_spans':status_known,'span_error_count':errors,
                'timeout_attribution':'UNAVAILABLE_WHEN_NO_SERVER_SPAN'}
        result['services'][service]={'business_server_spans':len(rows),
            'uid_coverage_fraction':(len(rows)-len(missing))/len(rows) if rows else None,
            'uid_match_fraction':sum(len(items) for items in per_pod.values())/len(rows) if rows else None,
            'missing_uid_spans':len(missing),'invalid_uid_spans':len(invalid),
            'invalid_examples':invalid[:10],'pod_stats':pod_data}
    total_business=result['business_server_span_count']
    result['uid_coverage_fraction']=result['uid_present_business_server_spans']/total_business if total_business else None
    result['uid_accuracy_fraction']=(result['uid_matched_business_server_spans'] /
        result['uid_present_business_server_spans'] if result['uid_present_business_server_spans'] else None)
    (args.output/'audit.json').write_text(json.dumps(result,indent=2)+'\n')
    (args.output/'raw-span-examples.json').write_text(json.dumps(examples,indent=2)+'\n')
    print(json.dumps({'business_server_spans':total_business,
        'uid_coverage_fraction':result['uid_coverage_fraction'],
        'uid_accuracy_fraction':result['uid_accuracy_fraction'],
        'per_service':{service:{'spans':row['business_server_spans'],
            'pods':len(row['pod_stats']),'match':row['uid_match_fraction']}
            for service,row in result['services'].items()}}))


if __name__=='__main__':main()
