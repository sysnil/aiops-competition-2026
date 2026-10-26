#!/usr/bin/env python3
"""Summarize bounded path probes; timeout is missing, never zero throughput."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import statistics

import pandas as pd
import plotly.express as px


def median(values):
    return statistics.median(values) if values else None


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--attempt',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    schedule=json.loads((args.attempt/'schedule.json').read_text())
    rows=[]; summary={}
    for phase in ('before','during','after'):
        path=args.attempt/f'probe-{phase}'/'observations.jsonl'
        if not path.exists():
            summary[phase]={'status':'MISSING_PHASE'}
            continue
        phase_rows=[json.loads(line) for line in path.read_text().splitlines()]
        indices=[row['index'] for row in phase_rows]
        if indices!=list(range(len(phase_rows))):
            raise ValueError('Probe sample indices are incomplete or duplicated')
        valid=[row for row in phase_rows if row['iperf3']['status']=='PASS']
        receiver=[row['iperf3']['throughput_bps'] for row in valid]
        loss=[row['iperf3']['loss_fraction'] for row in valid]
        rtts=[row['icmp']['rtt_median_ms'] for row in phase_rows
              if row['icmp']['rtt_median_ms'] is not None]
        summary[phase]={'planned_samples':len(phase_rows),
            'iperf_valid_samples':len(valid),
            'iperf_timeout_samples':sum(row['iperf3']['status']=='TIMEOUT' for row in phase_rows),
            'schedule_missed_samples':sum(row['iperf3']['status']=='SCHEDULE_MISS' for row in phase_rows),
            'iperf_command_failure_samples':sum(row['iperf3']['status']=='COMMAND_FAILURE' for row in phase_rows),
            'control_connection_observed_samples':sum(bool(row['iperf3']['control_connections_during'])
                                                      for row in phase_rows),
            'throughput_bps_median':median(receiver),
            'loss_fraction_median':median(loss),
            'icmp_rtt_ms_median':median(rtts),
            'icmp_complete_samples':sum(row['icmp']['status']=='PASS' for row in phase_rows),
            'icmp_partial_samples':sum(row['icmp']['status']=='PARTIAL' for row in phase_rows),
            'icmp_timeout_samples':sum(row['icmp']['status']=='TIMEOUT' for row in phase_rows)}
        for row in phase_rows:
            at=datetime.fromisoformat(row['observed_utc'])
            rows.extend([{'time':at,'phase':phase,'measure':'throughput Mbps',
                          'value':row['iperf3']['throughput_bps']/1e6 if row['iperf3']['throughput_bps'] is not None else None},
                         {'time':at,'phase':phase,'measure':'loss percent',
                          'value':row['iperf3']['loss_fraction']*100 if row['iperf3']['loss_fraction'] is not None else None},
                         {'time':at,'phase':phase,'measure':'ICMP RTT ms',
                          'value':row['icmp']['rtt_median_ms']}])
    result={'experiment_id':schedule['experiment_id'],'path':schedule['path'],
            'source':'bounded iperf3 UDP + independent ICMP; invalid sample is null',
            'phases':summary,'timeout_imputation':None,
            'probe_traffic_is_diagnostic_not_business':True}
    (args.output/'network-probe-summary.json').write_text(json.dumps(result,indent=2)+'\n')
    frame=pd.DataFrame(rows)
    if not frame.empty:
        fig=px.line(frame,x='time',y='value',color='phase',facet_row='measure',
                    title='NetworkPath probe strength and missing samples')
        fig.update_layout(template='plotly_white',height=850)
        fig.write_html(args.output/'network-probe-curves.html',include_plotlyjs=True)
    print(json.dumps({'experiment_id':result['experiment_id'],
                      'phases':summary}))


if __name__=='__main__':
    main()
