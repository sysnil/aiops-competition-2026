#!/usr/bin/env python3
"""Summarize complete probe windows while retaining null timeout samples."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import statistics

import plotly.express as px
import pandas as pd


def median(values):
    return statistics.median(values) if values else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refusing overwrite')
    args.output.mkdir(parents=True)
    summary = {}
    chart = []
    for phase, expected in (('before', 24), ('during', 12), ('after', 24)):
        path = args.run / ('probe-' + phase) / 'observations.jsonl'
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        if len(rows) != expected or [r['index'] for r in rows] != list(range(expected)):
            raise ValueError('Incomplete or duplicated probe sample indices: ' + phase)
        valid = [r for r in rows if r['iperf3']['status'] == 'SUCCESS']
        summary[phase] = {
            'planned': expected, 'valid_iperf': len(valid),
            'timeout': sum(r['iperf3']['status'] == 'TIMEOUT' for r in rows),
            'schedule_miss': sum(r['iperf3']['status'] == 'SCHEDULE_MISS' for r in rows),
            'control_connection_failure': sum(r['iperf3']['status'] ==
                'CONTROL_CONNECTION_FAILURE' for r in rows),
            'control_connection_observed': sum(bool(r['iperf3'].get('control_connections_during')) for r in rows),
            'median_throughput_mbps': median([r['iperf3']['throughput_bps'] / 1e6 for r in valid]),
            'median_udp_loss_fraction': median([r['iperf3']['loss_fraction'] for r in valid]),
            'median_icmp_rtt_ms': median([r['icmp']['rtt_median_ms'] for r in rows
                                          if r['icmp']['rtt_median_ms'] is not None]),
            'icmp_complete': sum(r['icmp']['status'] == 'SUCCESS' for r in rows),
            'icmp_partial': sum(r['icmp']['status'] == 'PARTIAL' for r in rows),
            'icmp_timeout': sum(r['icmp']['status'] == 'TIMEOUT' for r in rows)}
        for row in rows:
            at = datetime.fromisoformat(row['observed_utc'])
            chart.extend([{'time': at, 'phase': phase, 'measure': 'throughput Mbps',
                           'value': row['iperf3']['throughput_bps'] / 1e6
                           if row['iperf3']['throughput_bps'] is not None else None},
                          {'time': at, 'phase': phase, 'measure': 'UDP loss %',
                           'value': row['iperf3']['loss_fraction'] * 100
                           if row['iperf3']['loss_fraction'] is not None else None},
                          {'time': at, 'phase': phase, 'measure': 'ICMP RTT ms',
                           'value': row['icmp']['rtt_median_ms']}])
    result = {'path': 'NETWORK_PATH/k8s-worker3/k8s-worker2', 'phases': summary,
              'timeouts_are_missing_not_zero': True,
              'diagnostic_probes_separate_from_ordinary_business': True}
    (args.output / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
    fig = px.line(pd.DataFrame(chart), x='time', y='value', color='phase',
                  facet_row='measure', title='W3 to W2 full-window probe evidence')
    fig.update_layout(template='plotly_white', height=850)
    fig.write_html(args.output / 'probe-curves.html', include_plotlyjs=True)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
