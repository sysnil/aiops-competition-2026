#!/usr/bin/env python3
"""Attribute network target edges and normal controls using raw trace parents."""

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path

import pandas as pd
import plotly.express as px


SOURCE=Path('/root/cross-layer-workload-generation/scripts/trace-coverage.py')
spec=importlib.util.spec_from_file_location('stage2_trace_reader',SOURCE)
reader=importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)


def quantile(values,p):
    ordered=sorted(values)
    return ordered[min(len(ordered)-1,int((len(ordered)-1)*p))] if ordered else None


def tags(span):
    return {item.get('key'):item.get('value') for item in
            span.get('process',{}).get('tags',[])}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--attempt',type=Path,required=True)
    parser.add_argument('--control-analysis',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    schedule=json.loads((args.attempt/'schedule.json').read_text())
    pre=json.loads((args.attempt/'preflight.json').read_text())
    controls=json.loads(args.control_analysis.read_text())
    start=datetime.fromisoformat(schedule['scheduled_start_utc'])
    end=start+timedelta(seconds=300)
    ids=set()
    for line in (args.attempt/'load_pair/state-machine/sessions.jsonl').read_text().splitlines():
        ids.update(step['trace_id'] for step in json.loads(line)['steps'])
    for stream in ('image_asset','product_detail','cart_view'):
        for line in (args.attempt/'load_pair/controls'/(stream+'.jsonl')).read_text().splitlines():
            value=json.loads(line).get('trace_id')
            if value:ids.add(value)
    query={'bool':{'filter':[
        {'range':{'startTimeMillis':{'gte':int(start.timestamp()*1000),
                                      'lt':int((end+timedelta(seconds=30)).timestamp()*1000)}}},
        {'terms':{'traceID':sorted(ids)}}]}}
    spans,total=reader.scan(query)
    by_trace=defaultdict(dict)
    for span in spans:by_trace[span['traceID']][span['spanID']]=span
    targets=defaultdict(lambda:{'client_ms':[],'server_ms':[],
                                'trace_ids':set(),'source_uids':defaultdict(set)})
    allowed={(row['business_edge'],row['source_pod_uid'],row['destination_pod_uid'])
             for row in pre['verified_traversing_edges']}
    for child in spans:
        if (child.get('tag') or {}).get('span@kind')!='server':continue
        dest_service=child.get('process',{}).get('serviceName')
        for ref in child.get('references') or []:
            if ref.get('refType')!='CHILD_OF' or ref.get('traceID')!=child['traceID']:
                continue
            parent=by_trace[child['traceID']].get(ref['spanID'])
            if not parent:continue
            source_service=parent.get('process',{}).get('serviceName')
            edge=source_service+'->'+dest_service if source_service else None
            if edge not in {row[0] for row in allowed}:continue
            possible=[row for row in pre['verified_traversing_edges'] if row['business_edge']==edge]
            source_uid=tags(parent).get('k8s.pod.uid')
            dest_uid=tags(child).get('k8s.pod.uid')
            if source_uid is None and len({row['source_pod_uid'] for row in possible})==1:
                source_uid=possible[0]['source_pod_uid']
            if dest_uid is None and len({row['destination_pod_uid'] for row in possible})==1:
                dest_uid=possible[0]['destination_pod_uid']
            if (edge,source_uid,dest_uid) not in allowed:continue
            at=datetime.fromtimestamp(parent['startTimeMillis']/1000,timezone.utc)
            minute=int((at-start).total_seconds()//60)
            if not 0<=minute<5:continue
            key=(edge,minute)
            row=targets[key]
            row['client_ms'].append(max(0,parent.get('duration',0))/1000)
            row['server_ms'].append(max(0,child.get('duration',0))/1000)
            row['trace_ids'].add(child['traceID'])
            row['source_uids'][source_uid].add(child['traceID'])
    target_rows=[]
    for (edge,minute),values in sorted(targets.items()):
        target_rows.append({'edge':edge,'minute':minute,
            'phase':'before' if minute<2 else 'during' if minute==2 else 'after',
            'trace_calls':len(values['trace_ids']),
            'p95_client_span_ms':quantile(values['client_ms'],.95),
            'p95_server_span_ms':quantile(values['server_ms'],.95),
            'source_pod_trace_calls':{uid:len(seen) for uid,seen in values['source_uids'].items()}})
    normal_controls={}
    for stream in ('image_asset','cart_view'):
        status=controls['stability'][stream]
        bins=[row for row in controls['bins'] if row['stream']==stream]
        normal_controls[stream]={'all_five_request_bins_pass':status['all_five_request_bins_pass'],
            'all_five_trace_bins_pass':status['all_five_trace_bins_pass'],
            'stable_client_p95':status['within_1_25'],
            'errors_during':next(row['errors'] for row in bins if row['minute_index']==2),
            'during_successful_requests':next(row['successful'] for row in bins if row['minute_index']==2),
            'during_edge_spans':next(row['verified_call_edge_spans'] for row in bins if row['minute_index']==2)}
        normal_controls[stream]['qualified']=(
            all(normal_controls[stream][k] for k in
                ('all_five_request_bins_pass','all_five_trace_bins_pass','stable_client_p95'))
            and normal_controls[stream]['errors_during']==0)
    result={'source':'ordinary generated trace IDs joined to raw Jaeger parent-child spans',
        'path':pre['path'],'source_node':pre['source_node'],
        'destination_node':pre['destination_node'],
        'trace_ids_issued':len(ids),'trace_ids_seen':len(by_trace),
        'span_count':len(spans),'opensearch_total':total,
        'target_edges':target_rows,'normal_controls':normal_controls,
        'route_membership_source':'pre-fault UID, Calico route and payload packet proof',
        'server_duration_zero_is_not_used_for_control_latency':True}
    (args.output/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    if target_rows:
        frame=pd.DataFrame(target_rows)
        fig=px.line(frame,x='minute',y='p95_client_span_ms',color='edge',markers=True,
                    title='Affected business-edge client span p95 by minute')
        fig.add_vrect(x0=1.5,x1=2.5,fillcolor='#e8ac6d',opacity=.22,line_width=0)
        fig.update_layout(template='plotly_white',height=520)
        fig.write_html(args.output/'affected-edge-client-p95.html',include_plotlyjs=True)
    print(json.dumps({'path':pre['path'],'target_during':[
        {'edge':row['edge'],'calls':row['trace_calls'],'p95_ms':row['p95_client_span_ms']}
        for row in target_rows if row['minute']==2],
        'normal_controls':normal_controls}))


if __name__=='__main__':main()
