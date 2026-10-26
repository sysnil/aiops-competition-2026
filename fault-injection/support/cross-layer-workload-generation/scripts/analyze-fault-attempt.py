#!/usr/bin/env python3
"""Phase-aligned, GT-free signal analysis of an independent workload rehearsal."""

import argparse
from collections import defaultdict
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import statistics

import pandas as pd
import plotly.express as px


spec=importlib.util.spec_from_file_location('trace_coverage',Path(__file__).with_name('trace-coverage.py'))
trace=importlib.util.module_from_spec(spec)
spec.loader.exec_module(trace)


def quantile(values,p):
    ordered=sorted(values)
    return ordered[min(len(ordered)-1,int(p*(len(ordered)-1)))] if ordered else None


def span_kind(span):
    return (span.get('tag') or {}).get('span@kind')


def phase_for(at, start, fault_start, fault_end, end):
    if start<=at<fault_start: return 'before'
    if fault_start<=at<fault_end: return 'during'
    if fault_end<=at<end: return 'after'
    return None


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--attempt',required=True,type=Path)
    parser.add_argument('--local-archive',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    schedule=json.loads((args.local_archive/'schedule.json').read_text())
    load=args.attempt/'load'
    manifest=json.loads((load/'result-manifest.json').read_text())
    started=datetime.fromisoformat(schedule['load_start_utc'])
    fault_start=datetime.fromisoformat(schedule['fault_start_utc'])
    fault_end=datetime.fromisoformat(schedule['fault_end_utc'])
    ended=datetime.fromisoformat(manifest['ended_utc'])
    rows=[json.loads(line) for line in (load/'sessions.jsonl').read_text().splitlines()]
    ids=sorted({step['trace_id'] for row in rows for step in row['steps']})
    query={'bool':{'filter':[
        {'range':{'startTimeMillis':{'gte':int(started.timestamp()*1000),
                                      'lt':int(ended.timestamp()*1000)}}},
        {'terms':{'traceID':ids}}]}}
    spans,total=trace.scan(query)
    by_trace=defaultdict(dict)
    for span in spans:
        by_trace[span['traceID']][span['spanID']]=span
    service_latency=defaultdict(list)
    edge_latency=defaultdict(list)
    status_counts=defaultdict(lambda:{'success':0,'failure':0})
    timeseries=[]
    for span in spans:
        at=datetime.fromtimestamp(span['startTimeMillis']/1000,started.tzinfo)
        phase=phase_for(at,started,fault_start,fault_end,ended)
        if not phase: continue
        service=span.get('process',{}).get('serviceName')
        if not service or span_kind(span) not in ('server','consumer'):
            continue
        duration=max(0,span.get('duration',0))/1000
        service_latency[(phase,service)].append(duration)
        tags={tag.get('key'):tag.get('value') for tag in span.get('tags',[])}
        code=tags.get('http.status_code',tags.get('http.response.status_code'))
        if code is not None:
            try: success=int(code)<400
            except (ValueError,TypeError): success=True
            status_counts[(phase,service)]['success' if success else 'failure']+=1
        timeseries.append({'time':at,'service':service,'phase':phase,
                           'latency_ms':duration})
        for ref in span.get('references') or []:
            if ref.get('refType')!='CHILD_OF' or ref.get('traceID')!=span['traceID']:
                continue
            parent=by_trace[span['traceID']].get(ref['spanID'])
            if parent:
                source=parent.get('process',{}).get('serviceName')
                if source and source!=service:
                    edge_latency[(phase,source+'->'+service)].append(duration)
    service_summary={phase:{} for phase in ('before','during','after')}
    edge_summary={phase:{} for phase in ('before','during','after')}
    for (phase,service),values in service_latency.items():
        service_summary[phase][service]={'count':len(values),'p50_ms':quantile(values,.5),
                                         'p95_ms':quantile(values,.95)}
    for (phase,edge),values in edge_latency.items():
        edge_summary[phase][edge]={'count':len(values),'p50_ms':quantile(values,.5),
                                   'p95_ms':quantile(values,.95)}
    requests=defaultdict(list)
    for row in rows:
        for step in row['steps']:
            at=datetime.fromisoformat(step['time_utc'])
            phase=phase_for(at,started,fault_start,fault_end,ended)
            if phase: requests[phase].append(step)
    request_summary={phase:{'count':len(requests[phase]),
                            'success_fraction':sum(step['success'] for step in requests[phase])/len(requests[phase]) if requests[phase] else None,
                            'p95_ms':quantile([step['latency_ms'] for step in requests[phase]],.95)}
                     for phase in ('before','during','after')}
    probes={}
    probe_rows=[]
    for phase in ('before','during','after'):
        path=args.attempt/f'direct-probe-{phase}'/'observations.jsonl'
        if not path.exists(): continue
        by_pod=defaultdict(list)
        for line in path.read_text().splitlines():
            row=json.loads(line)
            by_pod[row['pod']].append(row)
            if row['elapsed_ms'] is not None:
                probe_rows.append({'time':datetime.fromisoformat(row['observed_utc']),
                    'pod':row['pod'],'latency_ms':row['elapsed_ms'],'phase':phase})
        probes[phase]={pod:{'samples':len(values),
            'http_200':sum(row['http_status']==200 for row in values),
            'p95_ms':quantile([row['elapsed_ms'] for row in values
                               if row['elapsed_ms'] is not None],.95),
            'timeouts':sum(row['status']=='TIMEOUT' for row in values)}
            for pod,values in by_pod.items()}
    result={'source':'generated request trace IDs + Jaeger OpenSearch + UID-bound diagnostic probes',
            'experiment_id':schedule['experiment_id'], 'windows':{
                'before':[started.isoformat(),fault_start.isoformat()],
                'during':[fault_start.isoformat(),fault_end.isoformat()],
                'after':[fault_end.isoformat(),ended.isoformat()]},
            'span_count':len(spans),'trace_count':len(by_trace),
            'opensearch_total':total,'request_summary':request_summary,
            'service_server_spans':service_summary,'edge_server_spans':edge_summary,
            'http_status_counts':{f'{phase}/{service}':counts
                for (phase,service),counts in status_counts.items()},
            'pod_direct_probe':probes}
    (args.output/'signal-summary.json').write_text(json.dumps(result,indent=2)+'\n')
    if timeseries:
        frame=pd.DataFrame(timeseries)
        frame=frame[frame['service'].isin(['frontend','ad','cart','checkout','shipping'])]
        frame['minute']=frame['time'].dt.floor('15s')
        grouped=frame.groupby(['minute','service'])['latency_ms'].quantile(.95).reset_index()
        fig=px.line(grouped,x='minute',y='latency_ms',color='service',
                    title='Observed server-span p95 (15-second bins)')
        fig.add_vrect(x0=fault_start,x1=fault_end,fillcolor='#e8ac6d',opacity=.22,line_width=0)
        fig.update_layout(template='plotly_white',height=530)
        fig.write_html(args.output/'service-p95-curve.html',include_plotlyjs=True)
    if probe_rows:
        fig=px.line(pd.DataFrame(probe_rows),x='time',y='latency_ms',color='pod',
                    title='UID-bound direct Pod probe latency (diagnostic traffic)')
        fig.add_vrect(x0=fault_start,x1=fault_end,fillcolor='#e8ac6d',opacity=.22,line_width=0)
        fig.update_layout(template='plotly_white',height=530)
        fig.write_html(args.output/'pod-direct-probe-curve.html',include_plotlyjs=True)
    print(json.dumps({'experiment_id':schedule['experiment_id'],
                      'request_summary':request_summary,
                      'frontend_p95':{phase:service_summary[phase].get('frontend',{}).get('p95_ms')
                                      for phase in ('before','during','after')}}))


if __name__=='__main__':
    main()
