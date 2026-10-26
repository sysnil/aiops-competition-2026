#!/usr/bin/env python3
"""Assess a preselected real business control from generated client requests."""

import argparse
from collections import defaultdict
from datetime import datetime
import json
from pathlib import Path

import pandas as pd
import plotly.express as px


def quantile(values,p):
    ordered=sorted(values)
    return ordered[min(len(ordered)-1,int(p*(len(ordered)-1)))] if ordered else None


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--attempt',type=Path,required=True)
    parser.add_argument('--operation',required=True)
    parser.add_argument('--minimum-samples',type=int,default=30)
    parser.add_argument('--maximum-p95-ratio',type=float,default=1.25)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    schedule=json.loads((args.attempt/'schedule.json').read_text())
    start=datetime.fromisoformat(schedule['fault_start_utc'])
    end=datetime.fromisoformat(schedule['fault_end_utc'])
    rows=defaultdict(list)
    plot_rows=[]
    for line in (args.attempt/'load/sessions.jsonl').read_text().splitlines():
        for step in json.loads(line)['steps']:
            if step['operation']!=args.operation: continue
            at=datetime.fromisoformat(step['time_utc'])
            phase='before' if at<start else 'during' if at<end else 'after'
            rows[phase].append(step)
            plot_rows.append({'time':at,'phase':phase,'latency_ms':step['latency_ms'],
                              'success':step['success']})
    summary={phase:{'count':len(rows[phase]),
                    'successes':sum(item['success'] for item in rows[phase]),
                    'p95_ms':quantile([item['latency_ms'] for item in rows[phase]],.95)}
             for phase in ('before','during','after')}
    before,during=summary['before']['p95_ms'],summary['during']['p95_ms']
    result={'operation':args.operation,'phase_summary':summary,
            'minimum_samples_per_phase':args.minimum_samples,
            'maximum_during_before_p95_ratio':args.maximum_p95_ratio,
            'sample_gate_pass':all(row['count']>=args.minimum_samples for row in summary.values()),
            'latency_gate_pass':before is not None and during is not None and
                                during/before<=args.maximum_p95_ratio,
            'classification':'NORMAL_BUSINESS_CONTROL' if args.operation=='image_asset' else
                             'OPERATION_DIAGNOSTIC',
            'source':'real generated HTTP requests, independent of Jaeger zero-duration server spans'}
    result['qualified']=result['sample_gate_pass'] and result['latency_gate_pass']
    (args.output/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    if plot_rows:
        frame=pd.DataFrame(plot_rows)
        fig=px.scatter(frame,x='time',y='latency_ms',color='phase',
                       title=args.operation+' client latency and control coverage')
        fig.add_vrect(x0=start,x1=end,fillcolor='#e8ac6d',opacity=.22,line_width=0)
        fig.update_layout(template='plotly_white',height=530)
        fig.write_html(args.output/'latency-and-coverage.html',include_plotlyjs=True)
    print(json.dumps(result))


if __name__=='__main__':main()
