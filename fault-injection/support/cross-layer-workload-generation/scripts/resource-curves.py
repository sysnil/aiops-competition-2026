#!/usr/bin/env python3
"""Archive actual Prometheus cAdvisor resource curves for one workload run."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import urllib.parse
import urllib.request

import pandas as pd
import plotly.express as px


PROM = 'http://10.97.14.138:9090'
QUERIES = {
    'pod_cpu_cores': 'sum by(pod,container,instance)(rate(container_cpu_usage_seconds_total{namespace="otel-demo",container=~"ad|frontend|recommendation|payment"}[1m]))',
    'pod_memory_bytes': 'sum by(pod,container,instance)(container_memory_working_set_bytes{namespace="otel-demo",container=~"ad|frontend|recommendation|payment"})',
    'pod_cfs_throttled_periods_per_second': 'sum by(pod,container,instance)(rate(container_cpu_cfs_throttled_periods_total{namespace="otel-demo",container=~"ad|frontend|recommendation|payment"}[1m]))',
    'pod_cfs_periods_per_second': 'sum by(pod,container,instance)(rate(container_cpu_cfs_periods_total{namespace="otel-demo",container=~"ad|frontend|recommendation|payment"}[1m]))',
    'node_business_container_cpu_cores': 'sum by(instance)(rate(container_cpu_usage_seconds_total{namespace="otel-demo",container!="",cpu="total"}[1m]))',
    'node_business_container_memory_bytes': 'sum by(instance)(container_memory_working_set_bytes{namespace="otel-demo",container!=""})',
    'pod_network_transmit_bytes_per_second': 'sum by(pod,instance)(rate(container_network_transmit_bytes_total{namespace="otel-demo",pod=~"ad-.*|frontend-.*|recommendation-.*|payment-.*"}[1m]))',
}


def query_range(expression, start, end):
    params = urllib.parse.urlencode({'query': expression, 'start': start.timestamp(),
                                      'end': end.timestamp(), 'step': 15})
    with urllib.request.urlopen(PROM+'/api/v1/query_range?'+params, timeout=60) as response:
        result=json.load(response)
    if result.get('status')!='success' or result['data']['resultType']!='matrix':
        raise RuntimeError('Prometheus query_range failed')
    return result['data']['result']


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    manifest=json.loads((args.run/'result-manifest.json').read_text())
    start=datetime.fromisoformat(manifest['started_utc'])
    end=datetime.fromisoformat(manifest['ended_utc'])
    results={}
    for name,expression in QUERIES.items():
        series=query_range(expression,start,end)
        results[name]={'expression':expression,'series':series}
        rows=[]
        for item in series:
            labels=item['metric']
            entity=labels.get('pod') or labels.get('instance') or 'unknown'
            for stamp,value in item['values']:
                try:
                    numeric=float(value)
                except ValueError:
                    continue
                rows.append({'time':pd.Timestamp(stamp,unit='s',tz='UTC'),
                             'entity':entity,'value':numeric})
        if rows:
            frame=pd.DataFrame(rows)
            fig=px.line(frame,x='time',y='value',color='entity',
                        title=name.replace('_',' ').title())
            fig.update_layout(template='plotly_white',height=530)
            fig.write_html(args.output/(name+'.html'),include_plotlyjs=True)
    archive={'source':'Prometheus query_range','window_start':start.isoformat(),
             'window_end':end.isoformat(),'step_seconds':15,'queries':results}
    (args.output/'resource-metrics.json').write_text(json.dumps(archive)+'\n')
    print(json.dumps({'status':'RESOURCE_CURVES_ARCHIVED',
                      'series_counts':{name:len(row['series']) for name,row in results.items()}}))


if __name__=='__main__':
    main()
