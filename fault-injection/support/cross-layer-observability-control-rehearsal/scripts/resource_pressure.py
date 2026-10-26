#!/usr/bin/env python3
"""Compare pre-control and combined-load cAdvisor curves on same Pod placement."""

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import statistics
import urllib.parse
import urllib.request

import pandas as pd
import plotly.express as px


PROM='http://10.97.14.138:9090'
QUERIES={
    'business_container_cpu_cores':
      'sum by(instance)(rate(container_cpu_usage_seconds_total{namespace="otel-demo",container!="",cpu="total"}[1m]))',
    'business_container_memory_bytes':
      'sum by(instance)(container_memory_working_set_bytes{namespace="otel-demo",container!=""})',
    'business_container_network_tx_bytes_per_second':
      'sum by(instance)(rate(container_network_transmit_bytes_total{namespace="otel-demo",pod!=""}[1m]))'}


def query(expression,start,end):
    params=urllib.parse.urlencode({'query':expression,'start':start.timestamp(),
                                   'end':end.timestamp(),'step':15})
    with urllib.request.urlopen(PROM+'/api/v1/query_range?'+params,timeout=60) as response:
        value=json.load(response)
    if value.get('status')!='success' or value['data']['resultType']!='matrix':
        raise RuntimeError('Prometheus query_range failed')
    return value['data']['result']


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    schedule=json.loads((args.run/'schedule.json').read_text())
    begin=datetime.fromisoformat(schedule['scheduled_start_utc'])
    end=datetime.fromisoformat(schedule['scheduled_end_utc'])
    windows={'pre_control':(begin-timedelta(seconds=120),begin),
             'combined_no_fault':(begin,end)}
    summary={};rows=[]
    for name,expression in QUERIES.items():
        series=query(expression,windows['pre_control'][0],end)
        grouped=defaultdict(lambda:defaultdict(list))
        for item in series:
            node=item['metric'].get('instance','unknown')
            for stamp,value in item['values']:
                time=datetime.fromtimestamp(float(stamp),timezone.utc)
                phase='pre_control' if time<begin else 'combined_no_fault'
                try:numeric=float(value)
                except ValueError:continue
                grouped[node][phase].append(numeric)
                rows.append({'time':time,'node':node,'metric':name,'value':numeric,
                             'phase':phase})
        summary[name]={}
        for node,phases in grouped.items():
            pre=statistics.median(phases.get('pre_control',[])) if phases.get('pre_control') else None
            combined=statistics.median(phases.get('combined_no_fault',[])) if phases.get('combined_no_fault') else None
            summary[name][node]={'pre_median':pre,'combined_median':combined,
                'delta':combined-pre if pre is not None and combined is not None else None,
                'pre_samples':len(phases.get('pre_control',[])),
                'combined_samples':len(phases.get('combined_no_fault',[]))}
    cpu=summary['business_container_cpu_cores']
    result={'source':'Prometheus/cAdvisor','windows':{key:[a.isoformat(),b.isoformat()]
            for key,(a,b) in windows.items()},'metrics':summary,
            'conservative_combined_cpu_delta_below_0_25_cores_per_worker':all(
                row['delta'] is not None and row['delta']<.25 for node,row in cpu.items()
                if node.startswith('k8s-worker')),
            'interpretation':'Combined state-machine plus control-load delta is an upper bound on control-only overhead, not a causal isolation'}
    (args.output/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    frame=pd.DataFrame(rows)
    if not frame.empty:
        fig=px.line(frame[frame['metric']=='business_container_cpu_cores'],x='time',y='value',
                    color='node',title='Business container CPU before and during combined no-fault load')
        fig.add_vline(x=begin,line_dash='dash',line_color='#bc573e')
        fig.update_layout(template='plotly_white',height=520)
        fig.write_html(args.output/'node-cpu-curve.html',include_plotlyjs=True)
    print(json.dumps({'status':'RESOURCE_PRESSURE_ANALYZED',
        'conservative_cpu_delta_pass':result['conservative_combined_cpu_delta_below_0_25_cores_per_worker'],
        'cpu_delta':{node:row['delta'] for node,row in cpu.items()}}))


if __name__=='__main__':main()
