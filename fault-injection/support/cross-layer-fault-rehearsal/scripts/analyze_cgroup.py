"""Compare one-second target/control cgroup counter rates across phases."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


PHASES = ('before', 'during', 'after')
ROLES = ('target', 'control')
FIELDS = [('cpu_cores', 'CPU cores'),
          ('throttled_period_fraction', 'Throttled CFS period fraction'),
          ('throttled_seconds_per_second', 'Throttled CPU seconds/s'),
          ('memory_mib', 'Memory working set (MiB)')]


def read_phase(attempt, phase, role):
    path = attempt / 'cgroup' / (phase + '-' + role) / 'observations.jsonl'
    if phase == 'before' and not path.is_file():
        path = attempt / 'cgroup' / ('clean-' + role) / 'observations.jsonl'
    rows = [json.loads(line) for line in path.read_text().splitlines() if line]
    if [row['index'] for row in rows] != list(range(len(rows))):
        raise ValueError('Missing or duplicate cgroup sample indices: ' + str(path))
    return rows


def frame(attempt):
    rows = []
    for phase in PHASES:
        for role in ROLES:
            for item in read_phase(attempt, phase, role):
                stat = item['cpu_stat']
                rows.append({'phase': phase, 'role': role, 'pod': item['pod'],
                             'uid': item['pod_uid'], 'index': item['index'],
                             'time': pd.Timestamp(item['observed_utc']),
                             'cpu_max': item['cpu_max'],
                             'usage_usec': stat['usage_usec'],
                             'nr_periods': stat['nr_periods'],
                             'nr_throttled': stat['nr_throttled'],
                             'throttled_usec': stat['throttled_usec'],
                             'memory_mib': item['memory_current_bytes'] / 1024**2})
    df = pd.DataFrame(rows).sort_values(['phase', 'role', 'time'])
    by = df.groupby(['phase', 'role'], observed=True)
    dt = by['time'].diff().dt.total_seconds()
    df['cpu_cores'] = (by['usage_usec'].diff() / 1e6 / dt).where(dt > 0)
    periods = by['nr_periods'].diff()
    throttled = by['nr_throttled'].diff()
    df['throttled_period_fraction'] = (throttled / periods).where(periods > 0)
    df['throttled_seconds_per_second'] = (by['throttled_usec'].diff() /
                                          1e6 / dt).where(dt > 0)
    return df


def analyze(attempt, output):
    if output.exists():
        raise ValueError('Cgroup analysis output already exists')
    df = frame(attempt)
    output.mkdir(parents=True)
    windows = {phase: {'start': group['time'].min().isoformat(),
                       'end': group['time'].max().isoformat()}
               for phase, group in df.groupby('phase')}
    summary = {'status': 'RH_CGROUP_ANALYSIS_COMPLETE',
               'experiment_id': attempt.name,
               'analyzed_at_utc': datetime.now(timezone.utc).isoformat(),
               'windows': windows, 'by_pod': []}
    for (phase, role, pod), group in df.groupby(['phase', 'role', 'pod']):
        summary['by_pod'].append({'phase': phase, 'role': role, 'pod': pod,
            'uid': group['uid'].iloc[0], 'samples': len(group),
            'cpu_max_values': sorted(group['cpu_max'].unique().tolist()),
            **{field + '_median': (float(group[field].median()) if
                pd.notna(group[field].median()) else None) for field, _ in FIELDS}})
    fig = make_subplots(rows=len(FIELDS), cols=1, shared_xaxes=True,
                        subplot_titles=[label for _, label in FIELDS],
                        vertical_spacing=.06)
    for index, (field, _) in enumerate(FIELDS, 1):
        for role, color in [('target', '#be5a42'), ('control', '#36748d')]:
            group = df[df['role'] == role]
            for phase in PHASES:
                part = group[group['phase'] == phase]
                fig.add_trace(go.Scatter(x=part['time'], y=part[field], mode='lines',
                              name=role, legendgroup=role,
                              showlegend=index == 1 and phase == 'before',
                              line=dict(color=color), connectgaps=False),
                              row=index, col=1)
    fig.add_vrect(x0=pd.Timestamp(windows['during']['start']),
                  x1=pd.Timestamp(windows['during']['end']),
                  fillcolor='#f2c6b8', opacity=.25, line_width=0, row='all', col=1)
    fig.update_layout(template='plotly_white', height=1180,
                      title='One-second cgroup target/control comparison: ' + attempt.name)
    (output / 'cgroup-comparison.html').write_text(fig.to_html(include_plotlyjs=True))
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--attempt', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = analyze(args.attempt, args.output)
    print(json.dumps({'status': result['status'], 'experiment_id': result['experiment_id']}))


if __name__ == '__main__':
    main()
