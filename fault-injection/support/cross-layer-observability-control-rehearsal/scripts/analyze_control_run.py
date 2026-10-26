#!/usr/bin/env python3
"""Count ordinary control requests and exact-ID trace edges in fixed UTC bins."""

import argparse
from collections import defaultdict
from datetime import datetime, timedelta
import importlib.util
import json
from pathlib import Path
import statistics

import pandas as pd
import plotly.express as px


SOURCE=Path('/root/cross-layer-workload-generation/scripts/trace-coverage.py')
spec=importlib.util.spec_from_file_location('stage2_trace_reader',SOURCE)
reader=importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)
EDGES={'image_asset':'frontend-proxy->image-provider',
       'product_detail':'frontend->product-catalog',
       'cart_view':'frontend->cart'}
STREAMS={'image_asset':1.0,'product_detail':2.0,'cart_view':2.0}


def quantile(values,p):
    ordered=sorted(values)
    return ordered[min(len(ordered)-1,int((len(ordered)-1)*p))] if ordered else None


def tags(span):
    return {item.get('key'):item.get('value') for item in
            span.get('process',{}).get('tags',[])}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--minimum',type=int,default=30)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    schedule=json.loads((args.run/'schedule.json').read_text())
    start=datetime.fromisoformat(schedule['scheduled_start_utc'])
    bins={}
    ids=set()
    by_stream={}
    for name,rate in STREAMS.items():
        rows=[json.loads(line) for line in
              (args.run/'controls'/(name+'.jsonl')).read_text().splitlines()]
        by_stream[name]=rows
        ids.update(row['trace_id'] for row in rows if row.get('trace_id'))
    query={'bool':{'filter':[
        {'range':{'startTimeMillis':{'gte':int(start.timestamp()*1000),
                                      'lt':int((start+timedelta(seconds=360)).timestamp()*1000)}}},
        {'terms':{'traceID':sorted(ids)}}]}}
    spans,total=reader.scan(query)
    by_trace=defaultdict(list)
    for span in spans:by_trace[span['traceID']].append(span)
    plot_rows=[]
    for name,rate in STREAMS.items():
        rows=by_stream[name]
        for minute in range(5):
            selected=[row for row in rows if minute<=row['index']/rate/60<minute+1]
            successful=[row for row in selected if row['success']]
            observed_ids={row['trace_id'] for row in selected if row.get('trace_id')}
            observed_spans=[span for trace_id in observed_ids for span in by_trace.get(trace_id,[])]
            edge_count=reader.describe(observed_spans)['edges'].get(EDGES[name],0)
            pod_requests=defaultdict(set)
            if name in ('product_detail','cart_view'):
                for span in observed_spans:
                    if (span.get('process',{}).get('serviceName')=='frontend' and
                            (span.get('tag') or {}).get('span@kind')=='server'):
                        uid=tags(span).get('k8s.pod.uid')
                        if uid:pod_requests[uid].add(span['traceID'])
            bucket={'stream':name,'minute_index':minute,
                'phase':'before' if minute<2 else 'during_no_fault' if minute==2 else 'after',
                'scheduled_start_utc':(start+timedelta(seconds=60*minute)).isoformat(),
                'planned':len(selected),'executed':sum(row.get('trace_id') is not None for row in selected),
                'successful':len(successful),'errors':sum(row.get('trace_id') and not row['success'] for row in selected),
                'missed_arrivals':sum(row.get('trace_id') is None for row in selected),
                'p50_client_ms':quantile([row['latency_ms'] for row in successful],.5),
                'p95_client_ms':quantile([row['latency_ms'] for row in successful],.95),
                'trace_ids_matched':sum(trace_id in by_trace for trace_id in observed_ids),
                'verified_call_edge_spans':edge_count,
                'frontend_pod_request_trace_ids':{uid:len(values) for uid,values in pod_requests.items()},
                'request_gate_pass':len(successful)>=args.minimum,
                'trace_gate_pass':edge_count>=args.minimum}
            bins[(name,minute)]=bucket
            plot_rows.append({k:bucket[k] for k in
                ('stream','minute_index','phase','successful','verified_call_edge_spans','p95_client_ms')})
    before={name:[bins[(name,minute)]['p95_client_ms'] for minute in (0,1)]
            for name in STREAMS}
    stability={}
    for name in STREAMS:
        ref=statistics.median(before[name]) if all(x is not None for x in before[name]) else None
        during=bins[(name,2)]['p95_client_ms']
        stability[name]={'reference_before_p95_ms':ref,'during_p95_ms':during,
            'during_before_ratio':during/ref if ref and during is not None else None,
            'within_1_25':ref is not None and during is not None and during/ref<=1.25,
            'all_five_request_bins_pass':all(bins[(name,i)]['request_gate_pass'] for i in range(5)),
            'all_five_trace_bins_pass':all(bins[(name,i)]['trace_gate_pass'] for i in range(5))}
    result={'source':'fixed ordinary HTTP controls + exact-ID Jaeger OpenSearch spans',
            'trace_ids_issued':len(ids),'trace_ids_seen':len(by_trace),
            'span_count':len(spans),'opensearch_total':total,
            'minimum_per_60s':args.minimum,'bins':list(bins.values()),'stability':stability,
            'server_duration_zero_is_not_used_for_latency':True}
    (args.output/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    frame=pd.DataFrame(plot_rows)
    fig=px.bar(frame,x='minute_index',y=['successful','verified_call_edge_spans'],
               facet_row='stream',barmode='group',title='Real business-control requests and joined call edges')
    fig.add_hline(y=args.minimum,line_dash='dash',line_color='#bc573e')
    fig.update_layout(template='plotly_white',height=830)
    fig.write_html(args.output/'control-coverage.html',include_plotlyjs=True)
    fig=px.line(frame,x='minute_index',y='p95_client_ms',color='stream',markers=True,
                title='Control client p95 on unchanged 60-second schedule')
    fig.update_layout(template='plotly_white',height=480)
    fig.write_html(args.output/'control-client-latency.html',include_plotlyjs=True)
    print(json.dumps({'trace_ids':len(ids),'matched':len(by_trace),
        'minimum_success_requests_per_stream':{name:min(bins[(name,i)]['successful'] for i in range(5))
                                                for name in STREAMS},
        'stability':stability}))


if __name__=='__main__':main()
