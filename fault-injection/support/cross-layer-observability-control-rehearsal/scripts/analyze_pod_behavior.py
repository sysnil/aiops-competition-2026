#!/usr/bin/env python3
"""Separate ordinary per-Pod Trace/Metric evidence from unattributed client failures."""

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import urllib.parse
import urllib.request

import pandas as pd
import plotly.express as px


SOURCE=Path('/root/cross-layer-workload-generation/scripts/trace-coverage.py')
spec=importlib.util.spec_from_file_location('stage2_trace_reader',SOURCE)
reader=importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)
PROM='http://10.97.14.138:9090'
OS='http://10.111.114.255:9200'


def quantile(values,p):
    ordered=sorted(values)
    return ordered[min(len(ordered)-1,int(p*(len(ordered)-1)))] if ordered else None


def prom_range(expression,start,end):
    params=urllib.parse.urlencode({'query':expression,'start':start.timestamp(),
                                   'end':end.timestamp(),'step':15})
    with urllib.request.urlopen(PROM+'/api/v1/query_range?'+params,timeout=60) as response:
        data=json.load(response)
    if data.get('status')!='success' or data['data']['resultType']!='matrix':
        raise RuntimeError('Prometheus query_range failed')
    return data['data']['result']


def phase(at,start):
    index=int((at-start).total_seconds()//60)
    return index if 0<=index<5 else None


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
    target=pre['targets'][0]
    sibling=pre['controls'][0]
    trace_ids=set();client_rows=[]
    for line in (args.attempt/'load_pair/state-machine/sessions.jsonl').read_text().splitlines():
        session=json.loads(line)
        for step in session['steps']:
            trace_ids.add(step['trace_id'])
            client_rows.append({'operation':step['operation'],'time_utc':step['time_utc'],
                'success':step['success'],'error_type':step['error_type'],
                'latency_ms':step['latency_ms'],'source':'state_machine'})
        if session['failure']=='MISSED_ARRIVAL':
            at=start+timedelta(seconds=session['index']/1.5)
            client_rows.append({'operation':'session','time_utc':at.isoformat(),
                'success':False,'error_type':'MISSED_ARRIVAL','latency_ms':None,
                'source':'state_machine'})
    rates={'image_asset':1.0,'product_detail':2.0,'cart_view':2.0}
    for name,rate in rates.items():
        for line in (args.attempt/'load_pair/controls'/(name+'.jsonl')).read_text().splitlines():
            row=json.loads(line)
            if row.get('trace_id'):trace_ids.add(row['trace_id'])
            at=start+timedelta(seconds=row['index']/rate)
            client_rows.append({'operation':name,'time_utc':at.isoformat(),
                'success':row['success'],'error_type':row.get('error_type'),
                'latency_ms':row.get('latency_ms'),'source':'fixed_business_control'})
    query={'bool':{'filter':[
        {'range':{'startTimeMillis':{'gte':int(start.timestamp()*1000),
                                      'lt':int((end+timedelta(seconds=30)).timestamp()*1000)}}},
        {'terms':{'traceID':sorted(trace_ids)}}]}}
    spans,total=reader.scan(query)
    grouped=defaultdict(lambda:defaultdict(list))
    unique=defaultdict(lambda:defaultdict(set))
    trace_rows=[]
    for span in spans:
        if (span.get('process',{}).get('serviceName')!='frontend' or
                (span.get('tag') or {}).get('span@kind')!='server'):
            continue
        tags={item.get('key'):item.get('value') for item in
              span.get('process',{}).get('tags',[])}
        uid=tags.get('k8s.pod.uid')
        if uid not in (target['uid'],sibling['uid']):continue
        at=datetime.fromtimestamp(span['startTimeMillis']/1000,timezone.utc)
        minute=phase(at,start)
        if minute is None:continue
        duration=max(0,span.get('duration',0))/1000
        grouped[uid][minute].append(duration)
        unique[uid][minute].add(span['traceID'])
        trace_rows.append({'time':at,'pod':target['name'] if uid==target['uid'] else sibling['name'],
                           'uid':uid,'duration_ms':duration,'minute':minute})
    pod_counts={uid:[{'minute':minute,'request_trace_ids':len(unique[uid][minute]),
                      'server_spans':len(grouped[uid][minute]),
                      'p95_successful_server_span_ms':quantile(grouped[uid][minute],.95)}
                     for minute in range(5)] for uid in (target['uid'],sibling['uid'])}
    client_by_minute=[]
    for minute in range(5):
        rows=[row for row in client_rows if phase(datetime.fromisoformat(row['time_utc']),start)==minute]
        failures=[row for row in rows if not row['success']]
        client_by_minute.append({'minute':minute,'scheduled_or_executed':len(rows),
            'success':len(rows)-len(failures),'failed':len(failures),
            'timeout':sum(row['error_type']=='TimeoutError' for row in failures),
            'missed_arrival':sum(row['error_type']=='MISSED_ARRIVAL' for row in failures),
            'pod_attribution_for_missing_server_span':'UNAVAILABLE'})
    uids='|'.join((target['uid'],sibling['uid']))
    expression=('http_server_duration_milliseconds_count{service_name="frontend",'
                'service_instance_id=~"'+uids+'"}')
    http_metrics=prom_range(expression,start-timedelta(seconds=30),
                            end+timedelta(seconds=15))
    cpu_expr=('rate(container_cpu_usage_seconds_total{namespace="otel-demo",'
              'container="frontend",pod=~"'+target['name']+'|'+sibling['name']+'"}[1m])')
    cpu=prom_range(cpu_expr,start,end)
    cpu_rows=[]
    for item in cpu:
        name=item['metric'].get('pod')
        for stamp,value in item['values']:
            try:numeric=float(value)
            except ValueError:continue
            cpu_rows.append({'time':datetime.fromtimestamp(float(stamp),timezone.utc),
                             'pod':name,'cpu_cores':numeric})
    metric_summary={}
    for item in http_metrics:
        metric=item['metric']
        uid=metric.get('service_instance_id')
        if uid not in (target['uid'],sibling['uid']):continue
        status=metric.get('http_status_code','UNKNOWN')
        series=[(float(stamp),float(value)) for stamp,value in item['values']
                if value not in ('NaN','+Inf','-Inf')]
        for minute in range(5):
            lo=(start+timedelta(seconds=minute*60)).timestamp()
            hi=lo+60
            before=[value for stamp,value in series if stamp<=lo and lo-stamp<=20]
            after=[value for stamp,value in series if stamp<=hi and hi-stamp<=20]
            if not before or not after:continue
            delta=max(0,after[-1]-before[-1])
            key=(uid,minute)
            row=metric_summary.setdefault(key,{'all_status_approx':0,'error_approx':0})
            row['all_status_approx']+=delta
            try:
                if int(status)>=400:row['error_approx']+=delta
            except ValueError:pass
    result={'source':'ordinary state-machine/control request IDs joined to raw Jaeger spans, Prometheus, and Pod UIDs',
        'target':target,'control':sibling,'trace_ids_issued':len(trace_ids),
        'trace_ids_seen':len({span['traceID'] for span in spans}),
        'pod_trace_counts':pod_counts,'client_outcomes':client_by_minute,
        'prometheus_http_counts_scrape_aligned':[
            {'uid':uid,'minute':minute,**values} for (uid,minute),values in metric_summary.items()],
        'prometheus_cpu_query':cpu_expr,'prometheus_cpu_series':cpu,
        'unattributed_timeout_policy':'Never assign a missing server span to a Pod'}
    (args.output/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    chart_rows=[{'minute':row['minute'],'pod':target['name'] if uid==target['uid'] else sibling['name'],
                 'business_request_trace_ids':row['request_trace_ids']}
                for uid,records in pod_counts.items() for row in records]
    fig=px.bar(pd.DataFrame(chart_rows),x='minute',y='business_request_trace_ids',
               color='pod',barmode='group',title='Ordinary business Trace IDs by verified frontend Pod UID')
    fig.add_vrect(x0=2-.5,x1=2+.5,fillcolor='#e8ac6d',opacity=.22,line_width=0)
    fig.update_layout(template='plotly_white',height=500)
    fig.write_html(args.output/'pod-request-coverage.html',include_plotlyjs=True)
    if cpu_rows:
        fig=px.line(pd.DataFrame(cpu_rows),x='time',y='cpu_cores',color='pod',
                    title='Frontend Pod CPU across bounded pause')
        fig.add_vrect(x0=start+timedelta(seconds=120),
                      x1=start+timedelta(seconds=180),fillcolor='#e8ac6d',opacity=.22,line_width=0)
        fig.update_layout(template='plotly_white',height=500)
        fig.write_html(args.output/'pod-cpu-curve.html',include_plotlyjs=True)
    print(json.dumps({'target_uid':target['uid'],'control_uid':sibling['uid'],
        'trace_ids_seen':result['trace_ids_seen'],'target_counts':[
            row['request_trace_ids'] for row in pod_counts[target['uid']]],
        'control_counts':[row['request_trace_ids'] for row in pod_counts[sibling['uid']]],
        'client_failed':[row['failed'] for row in client_by_minute]}))


if __name__=='__main__':main()
