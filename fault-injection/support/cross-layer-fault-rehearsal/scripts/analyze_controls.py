"""Evaluate predeclared normal-control edges from fresh trace windows."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import pandas as pd
import plotly.express as px
import yaml


STAGE4F = Path('/root/stage4f-cross-layer-validation/scripts')
PHASES = ('before', 'during', 'after')


def traces_and_pods(attempt):
    sys.path.insert(0, str(STAGE4F))
    from convert_live import load_capture, kubernetes_metadata, trace_rows

    rows, status_rows, pods, windows = [], [], None, {}
    for phase in PHASES:
        capture = load_capture(attempt / 'capture' / phase, phase)
        metadata = kubernetes_metadata(capture, 'rh-controls')
        if pods is None:
            pods = metadata['pods']
        spans, edges = trace_rows(capture, set(metadata['services']))
        for span in spans:
            if span.get('parent_service') and span['tags'].get('span.kind') == 'server':
                tags = span['tags']
                code = tags.get('http.response.status_code')
                status_rows.append({'phase': phase,
                    'edge': span['parent_service'] + '->' + span['service'],
                    'error': str(tags.get('otel.status_code', '')).upper() == 'ERROR' or
                             (str(code).isdigit() and int(code) >= 500)})
        for edge, values in edges.items():
            for stamp, latency in values:
                rows.append({'phase': phase, 'edge': edge,
                             'time': pd.Timestamp(stamp, unit='ns', tz='UTC'),
                             'latency_ms': latency})
        windows[phase] = capture['window']
    return pd.DataFrame(rows), pd.DataFrame(status_rows), pods, windows


def analyze(attempt, candidate_plan, output):
    if output.exists():
        raise ValueError('Control analysis output already exists')
    plan = yaml.safe_load(candidate_plan.read_text())
    traces, statuses, pods, windows = traces_and_pods(attempt)
    results, chart_rows = [], []
    minimum = plan['selection_policy']['min_before_trace_spans']
    maximum = plan['selection_policy']['normality_p95_during_over_before_max']
    max_error_increase = plan['selection_policy']['normality_error_rate_increase_max']
    for candidate in plan['candidates']:
        edge = candidate['edge']
        source_service, destination_service = edge.split('->')
        source = [pod for pod in pods if pod['service'] == source_service and
                  pod['node'] == candidate['source_node']]
        destination = [pod for pod in pods if pod['service'] == destination_service and
                       pod['node'] == candidate['destination_node']]
        source_replicas = [pod for pod in pods if pod['service'] == source_service]
        subset = traces[traces['edge'] == edge]
        counts = {phase: int((subset['phase'] == phase).sum()) for phase in PHASES}
        p95 = {phase: (float(subset[subset['phase'] == phase]['latency_ms'].quantile(.95))
                       if counts[phase] else None) for phase in PHASES}
        status = statuses[statuses['edge'] == edge] if not statuses.empty else pd.DataFrame()
        error_rates = {phase: (float(status[status['phase'] == phase]['error'].mean())
                               if not status.empty and (status['phase'] == phase).any() else None)
                       for phase in PHASES}
        ratio = p95['during'] / p95['before'] if p95['before'] and p95['during'] else None
        reasons = []
        if len(source) != 1 or len(destination) != 1:
            reasons.append('AMBIGUOUS_POD_PAIR')
        if candidate['source_node'] != candidate['destination_node']:
            reasons.append('NOT_LOCAL_TO_INDEPENDENT_NODE')
        if len(source_replicas) > 1:
            reasons.append('SERVICE_TRACE_MIXES_SOURCE_REPLICAS')
        if counts['before'] < minimum or counts['during'] < minimum:
            reasons.append('INSUFFICIENT_TRACE_SAMPLES')
        if ratio is None or ratio > maximum:
            reasons.append('CONTROL_NOT_STABLE')
        if (error_rates['before'] is None or error_rates['during'] is None or
                error_rates['during'] - error_rates['before'] > max_error_increase):
            reasons.append('ERROR_SIGNAL_MISSING_OR_INCREASED')
        results.append({'edge': edge, 'role': candidate['role'],
                        'source_pod_uid': source[0]['uid'] if len(source) == 1 else None,
                        'destination_pod_uid': destination[0]['uid'] if len(destination) == 1 else None,
                        'counts': counts, 'p95_ms': p95,
                        'error_rates': error_rates,
                        'during_over_before_p95': ratio,
                        'qualified': not reasons, 'reasons': reasons})
        if not subset.empty:
            part = subset.copy()
            part['bin'] = part['time'].dt.floor('15s')
            binned = part.groupby(['phase', 'bin']).agg(
                p95_ms=('latency_ms', lambda x: x.quantile(.95)),
                calls=('latency_ms', 'count')).reset_index()
            for row in binned.to_dict('records'):
                chart_rows.append({**row, 'edge': edge})
    output.mkdir(parents=True)
    if chart_rows:
        chart = pd.DataFrame(chart_rows)
        fig = px.line(chart, x='bin', y='p95_ms', color='edge', line_group='phase',
                      title='Predeclared control edge p95 by 15-second bin')
        fig.add_vrect(x0=pd.Timestamp(windows['during']['start_utc']),
                      x1=pd.Timestamp(windows['during']['end_utc']),
                      fillcolor='#f2c6b8', opacity=.25, line_width=0)
        fig.update_layout(template='plotly_white', height=560)
        (output / 'control-edge-p95.html').write_text(fig.to_html(include_plotlyjs=True))
    summary = {'status': 'RH_CONTROL_ANALYSIS_COMPLETE',
               'analyzed_at_utc': datetime.now(timezone.utc).isoformat(),
               'experiment_id': attempt.name, 'windows': windows,
               'candidate_plan': str(candidate_plan),
               'eligible_controls': [row['edge'] for row in results if row['qualified']],
               'candidates': results}
    (output / 'summary.json').write_text(json.dumps(summary, indent=2, default=str) + '\n')
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--attempt', required=True, type=Path)
    parser.add_argument('--candidate-plan', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = analyze(args.attempt, args.candidate_plan, args.output)
    print(json.dumps({'status': result['status'],
                      'eligible_controls': result['eligible_controls']}))


if __name__ == '__main__':
    main()
