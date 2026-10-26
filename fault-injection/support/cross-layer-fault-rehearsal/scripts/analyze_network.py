"""Summarize bounded NetworkPath probe completeness and real measured values."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


PHASES = ('before', 'during', 'after')


def read_phase(attempt, phase):
    path = attempt / 'probes' / phase / 'observations.jsonl'
    rows = [json.loads(line) for line in path.read_text().splitlines() if line]
    manifest = json.loads((path.parent / 'manifest.json').read_text())
    expected = manifest['samples']
    actual = [row['index'] for row in rows]
    if len(actual) != len(set(actual)) or any(i < 0 or i >= expected for i in actual):
        raise ValueError('Duplicate or invalid network probe sample index')
    return rows, sorted(set(range(expected)) - set(actual))


def analyze(attempt, output):
    if output.exists():
        raise ValueError('Network analysis output already exists')
    preflight = json.loads((attempt / 'preflight-final.json').read_text())
    observations, summary = [], []
    for phase in PHASES:
        rows, missing = read_phase(attempt, phase)
        iperf = Counter(row['iperf3']['status'] for row in rows)
        icmp = Counter(row['icmp']['status'] for row in rows)
        summary.append({'phase': phase, 'expected_samples': len(rows) + len(missing),
                        'observed_samples': len(rows), 'missing_indices': missing,
                        'iperf3_status_counts': dict(iperf),
                        'iperf3_success_rate': iperf['PASS'] / (len(rows) + len(missing)),
                        'icmp_status_counts': dict(icmp),
                        'icmp_full_success_rate': icmp['PASS'] / (len(rows) + len(missing)),
                        'iperf3_control_connection_seen': sum(bool(
                            row['iperf3'].get('control_connections_during')) for row in rows)})
        for row in rows:
            observations.append({'phase': phase, 'index': row['index'],
                'time': pd.Timestamp(row['observed_utc']),
                'iperf_status': row['iperf3']['status'],
                'throughput_mbps': (row['iperf3']['throughput_bps'] / 1e6
                                    if row['iperf3']['throughput_bps'] is not None else None),
                'iperf_loss': row['iperf3']['loss_fraction'],
                'iperf_jitter_ms': row['iperf3']['jitter_ms'],
                'icmp_status': row['icmp']['status'],
                'icmp_rtt_ms': row['icmp']['rtt_median_ms'],
                'icmp_loss': row['icmp']['loss_fraction']})
    frame = pd.DataFrame(observations).sort_values('time')
    output.mkdir(parents=True)
    fields = [('throughput_mbps', 'iperf3 throughput (Mb/s; timeout = gap)'),
              ('iperf_loss', 'iperf3 receiver loss fraction'),
              ('icmp_rtt_ms', 'ICMP median RTT (ms)'),
              ('icmp_loss', 'ICMP loss fraction')]
    fig = make_subplots(rows=4, cols=1, shared_xaxes=True,
                        subplot_titles=[name for _, name in fields], vertical_spacing=.06)
    colors = {'before': '#36748d', 'during': '#bd6047', 'after': '#4e8865'}
    for index, (field, _) in enumerate(fields, 1):
        for phase in PHASES:
            part = frame[frame['phase'] == phase]
            fig.add_trace(go.Scatter(x=part['time'], y=part[field], mode='lines+markers',
                          name=phase, legendgroup=phase,
                          showlegend=index == 1, line=dict(color=colors[phase]),
                          connectgaps=False), row=index, col=1)
    during = frame[frame['phase'] == 'during']
    if not during.empty:
        fig.add_vrect(x0=during['time'].min(), x1=during['time'].max(),
                      fillcolor='#f2c6b8', opacity=.20, line_width=0,
                      row='all', col=1)
    fig.update_layout(template='plotly_white', height=1150,
                      title='NetworkPath probe comparison: ' + attempt.name)
    (output / 'network-probe-comparison.html').write_text(fig.to_html(include_plotlyjs=True))
    result = {'status': 'RH_NETWORK_ANALYSIS_COMPLETE',
              'experiment_id': attempt.name, 'path': preflight['path'],
              'analyzed_at_utc': datetime.now(timezone.utc).isoformat(),
              'probe_completeness': summary,
              'timeout_is_missing_not_zero': True}
    (output / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--attempt', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = analyze(args.attempt, args.output)
    print(json.dumps({'status': result['status'], 'path': result['path']}))


if __name__ == '__main__':
    main()
