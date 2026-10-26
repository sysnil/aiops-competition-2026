"""Compare real three-phase metrics and traces for isolated SERVICE/POD rehearsals."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import pandas as pd
import plotly.express as px
from plotly.subplots import make_subplots
import plotly.graph_objects as go


STAGE4F = Path('/root/stage4f-cross-layer-validation/scripts')
PHASES = ('before', 'during', 'after')


def read_jsonl(path):
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def load_windows(attempt):
    sys.path.insert(0, str(STAGE4F))
    from convert_live import load_capture, kubernetes_metadata, metric_rows, trace_rows

    metrics, spans, windows = [], [], {}
    for phase in PHASES:
        capture = load_capture(attempt / 'capture' / phase, phase)
        metadata = kubernetes_metadata(capture, 'isolated-rehearsal')
        services = set(metadata['services'])
        metrics.extend(metric_rows(capture, metadata['pods']))
        trace, _ = trace_rows(capture, services)
        spans.extend(trace)
        windows[phase] = capture['window']
    return pd.DataFrame(metrics), pd.DataFrame(spans), windows


def root_trace_identity(attempt, service):
    counts = Counter()
    for phase in PHASES:
        for page in (attempt / 'capture' / phase / 'trace-storage-pages').glob('page-*.json'):
            for hit in json.loads(page.read_text()).get('hits', {}).get('hits', []):
                process = hit.get('_source', {}).get('process', {})
                if process.get('serviceName') != service:
                    continue
                counts['root_service_spans'] += 1
                tags = {row['key']: row.get('value') for row in process.get('tags', [])}
                if tags.get('k8s.pod.uid'):
                    counts['pod_uid_spans'] += 1
                if tags.get('container.id'):
                    counts['container_id_spans'] += 1
                if tags.get('service.instance.id'):
                    counts['service_instance_spans'] += 1
    return dict(counts)


def resource_series(metrics, pods):
    frame = metrics[metrics['entity'].isin(pods)].copy()
    frame['time'] = pd.to_datetime(frame['time_unix_ns'], unit='ns', utc=True)
    grouped = frame.groupby(['entity', 'phase', 'time', 'family'], observed=True)['value'].sum()
    pivot = grouped.unstack('family').reset_index().sort_values(['entity', 'phase', 'time'])
    by = pivot.groupby(['entity', 'phase'], observed=True)
    dt = by['time'].diff().dt.total_seconds()
    for family, name in [('container_cpu_usage_seconds_total', 'cpu_cores'),
                         ('container_cpu_cfs_periods_total', 'cfs_periods_rate'),
                         ('container_cpu_cfs_throttled_periods_total', 'cfs_throttled_periods_rate'),
                         ('container_cpu_cfs_throttled_seconds_total', 'cfs_throttled_seconds_rate')]:
        if family in pivot:
            delta = by[family].diff()
            pivot[name] = (delta / dt).where((delta >= 0) & (dt > 0))
    pivot['throttled_period_fraction'] = (pivot['cfs_throttled_periods_rate'] /
                                           pivot['cfs_periods_rate']).where(
                                               pivot['cfs_periods_rate'] > 0)
    pivot['memory_mib'] = pivot['container_memory_working_set_bytes'] / 1024**2
    return pivot


def probe_rows(attempt):
    rows = []
    for phase in PHASES:
        for item in read_jsonl(attempt / 'probes' / phase / 'observations.jsonl'):
            rows.append({**item, 'phase': phase})
    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame['time'] = pd.to_datetime(frame['observed_utc'], utc=True)
    return frame


def service_edge_series(spans, service):
    rows = spans[(spans['service'] == service) &
                 spans['tags'].map(lambda x: x.get('span.kind') == 'server')].copy()
    if rows.empty:
        return pd.DataFrame()
    rows['time'] = pd.to_datetime(rows['time_unix_ns'], unit='ns', utc=True)
    rows['bin'] = rows['time'].dt.floor('15s')
    return rows.groupby(['phase', 'bin']).agg(
        p95_ms=('duration_ms', lambda x: x.quantile(.95)),
        count=('span_id', 'count')).reset_index()


def ratio(frame, field, pod):
    rows = frame[frame['entity'] == pod]
    before = rows[rows['phase'] == 'before'][field].dropna().median()
    during = rows[rows['phase'] == 'during'][field].dropna().median()
    return {'before_median': None if pd.isna(before) else float(before),
            'during_median': None if pd.isna(during) else float(during),
            'during_over_before': (float(during / before) if pd.notna(before) and
                                   pd.notna(during) and before > 0 else None)}


def plot(attempt, output, windows, resources, probes, service_spans):
    start = pd.Timestamp(windows['during']['start_utc'])
    end = pd.Timestamp(windows['during']['end_utc'])
    fields = [('cpu_cores', 'Pod CPU cores'),
              ('throttled_period_fraction', 'CFS throttled period fraction'),
              ('memory_mib', 'Pod working-set memory (MiB)'),
              ('cfs_throttled_seconds_rate', 'CFS throttled CPU seconds/s')]
    fig = make_subplots(rows=len(fields), cols=1, shared_xaxes=True,
                        subplot_titles=[item[1] for item in fields], vertical_spacing=.07)
    colors = px.colors.qualitative.Safe
    for index, (field, _) in enumerate(fields, 1):
        for i, (entity, group) in enumerate(resources.groupby('entity')):
            for phase in PHASES:
                part = group[group['phase'] == phase]
                fig.add_trace(go.Scatter(x=part['time'], y=part[field], mode='lines+markers',
                              name=entity.rsplit('/', 1)[-1], legendgroup=entity,
                              showlegend=index == 1 and phase == 'before',
                              line=dict(color=colors[i % len(colors)]),
                              connectgaps=False), row=index, col=1)
    fig.add_vrect(x0=start, x1=end, fillcolor='#f1c7bb', opacity=.22,
                  line_width=0, row='all', col=1)
    fig.update_layout(template='plotly_white', height=1150,
                      title='Pod resource comparison: ' + attempt.name)
    (output / 'pod-resource-comparison.html').write_text(fig.to_html(include_plotlyjs=True))
    if not probes.empty:
        success = probes[probes['status'] == 'RESPONSE']
        fig = px.scatter(success, x='time', y='elapsed_ms', color='pod',
                         title='Direct frontend Pod request latency: ' + attempt.name)
        fig.add_vrect(x0=start, x1=end, fillcolor='#f1c7bb', opacity=.22, line_width=0)
        fig.update_layout(template='plotly_white', height=500)
        (output / 'pod-request-comparison.html').write_text(fig.to_html(include_plotlyjs=True))
    if not service_spans.empty:
        fig = px.line(service_spans, x='bin', y='p95_ms', color='phase',
                      title='Service server-span p95: ' + attempt.name)
        fig.add_vrect(x0=start, x1=end, fillcolor='#f1c7bb', opacity=.22, line_width=0)
        fig.update_layout(template='plotly_white', height=500)
        (output / 'service-trace-p95.html').write_text(fig.to_html(include_plotlyjs=True))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--attempt', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Analysis output already exists')
    preflight = json.loads((args.attempt / 'preflight-final.json').read_text())
    service = preflight['targets'][0]['service']
    pods = ['POD/otel-demo/' + row['name'] for row in
            preflight['targets'] + preflight.get('controls', [])]
    metrics, spans, windows = load_windows(args.attempt)
    resources = resource_series(metrics, pods)
    probes = probe_rows(args.attempt)
    service_spans = service_edge_series(spans, service)
    args.output.mkdir(parents=True)
    plot(args.attempt, args.output, windows, resources, probes, service_spans)
    result = {'status': 'RH_SERVICE_POD_ANALYSIS_COMPLETE',
              'experiment_id': preflight['experiment_id'],
              'analyzed_at_utc': datetime.now(timezone.utc).isoformat(),
              'windows': windows, 'metric_rows': len(metrics), 'span_rows': len(spans),
              'root_trace_identity': root_trace_identity(args.attempt, service),
              'pod_metrics': {pod: {field: ratio(resources, field, pod)
                                    for field in ('cpu_cores', 'throttled_period_fraction',
                                                  'memory_mib', 'cfs_throttled_seconds_rate')}
                              for pod in pods},
              'direct_pod_probes': (probes.groupby(['phase', 'pod', 'status']).size()
                                    .reset_index(name='count').to_dict('records')
                                    if not probes.empty else []),
              'service_span_phase_summary': (service_spans.groupby('phase').agg(
                  p95_median_ms=('p95_ms', 'median'), bins=('p95_ms', 'count'))
                  .reset_index().to_dict('records') if not service_spans.empty else [])}
    (args.output / 'summary.json').write_text(json.dumps(result, indent=2, default=str) + '\n')
    print(json.dumps({'status': result['status'], 'experiment_id': result['experiment_id']}))


if __name__ == '__main__':
    main()
