"""Measure ad demand during a bounded no-fault frontend request sweep."""

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import urllib.parse
import urllib.request

import pandas as pd
import plotly.express as px


PROM = 'http://10.97.14.138:9090'
OPENSEARCH = 'http://10.111.114.255:9200'


def get_json(url, body=None):
    request = urllib.request.Request(url, data=body,
        headers={'Content-Type':'application/json'} if body else {})
    with urllib.request.urlopen(request,timeout=30) as response:
        return json.load(response)


def run(attempt, output):
    if output.exists():
        raise ValueError('Load analysis output already exists')
    manifest = json.loads((attempt/'manifest.json').read_text())
    start = datetime.fromisoformat(manifest['start_utc'].replace('Z','+00:00'))
    end = datetime.fromisoformat(manifest['end_utc'])
    windows = {'before':(start-timedelta(seconds=60),start),
               'during':(start,end),
               'after':(end,end+timedelta(seconds=60))}
    query = 'container_cpu_usage_seconds_total{namespace="otel-demo",container="ad",pod=~"ad-.*"}'
    params = urllib.parse.urlencode({'query':query,
        'start':windows['before'][0].timestamp(),
        'end':windows['after'][1].timestamp(),'step':15})
    prom = get_json(PROM+'/api/v1/query_range?'+params)
    if prom.get('status')!='success' or prom['data']['resultType']!='matrix':
        raise ValueError('Ad CPU Prometheus query failed')
    rows = []
    for series in prom['data']['result']:
        pod = series['metric'].get('pod')
        if not pod:
            continue
        for stamp,raw in series['values']:
            at = datetime.fromtimestamp(float(stamp),timezone.utc)
            phase = next((name for name,(begin,finish) in windows.items()
                          if begin<=at<finish),None)
            if phase:
                rows.append({'pod':pod,'phase':phase,'time':pd.Timestamp(at),
                             'cpu_seconds_total':float(raw)})
    frame = pd.DataFrame(rows).sort_values(['pod','time'])
    if frame.empty:
        raise ValueError('No ad container CPU series in bounded load window')
    by = frame.groupby('pod')
    dt = by['time'].diff().dt.total_seconds()
    delta = by['cpu_seconds_total'].diff()
    frame['cpu_cores'] = (delta/dt).where((delta>=0)&(dt>0))
    spans = {}
    for phase,(begin,finish) in windows.items():
        body = json.dumps({'query':{'bool':{'filter':[
            {'range':{'startTimeMillis':{'gte':int(begin.timestamp()*1000),
                                          'lt':int(finish.timestamp()*1000)}}},
            {'term':{'process.serviceName':'ad'}}]}}}).encode()
        result = get_json(OPENSEARCH+'/jaeger-jaeger-span-*/_count',body)
        if result.get('_shards',{}).get('failed') != 0:
            raise ValueError('Ad span count query incomplete')
        spans[phase] = result['count']
    output.mkdir(parents=True)
    fig = px.line(frame,x='time',y='cpu_cores',color='pod',line_group='phase',
                  title='Ad Pod CPU demand under bounded frontend request load')
    fig.add_vrect(x0=start,x1=end,fillcolor='#b8d7e1',opacity=.25,line_width=0)
    fig.update_layout(template='plotly_white',height=500)
    (output/'ad-cpu-load-sweep.html').write_text(fig.to_html(include_plotlyjs=True))
    summary = {'status':'RH_LOAD_ANALYSIS_COMPLETE',
               'load_status':manifest['status'],
               'requests_completed':manifest['completed_requests'],
               'request_errors':manifest['non_200_or_timeout'],
               'ad_span_counts':spans,
               'ad_cpu_median_cores':{phase:{pod:float(group['cpu_cores'].median())
                   for pod,group in frame[frame['phase']==phase].groupby('pod')}
                   for phase in windows},
               'windows':{phase:{'start':begin.isoformat(),'end':finish.isoformat()}
                          for phase,(begin,finish) in windows.items()},
               'fault_injected':False}
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    return summary


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--attempt',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    result=run(args.attempt,args.output)
    print(json.dumps({'status':result['status'],'ad_spans':result['ad_span_counts']}))


if __name__=='__main__':
    main()
