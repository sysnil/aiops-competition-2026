#!/usr/bin/env python3
"""Exercise organizer-only Public views using the visualizer's read-only API."""

import argparse
from datetime import timedelta
import json
from pathlib import Path
import sys

sys.path.insert(0, '/opt/aiops-competition-visualization')
from data import (counts, edge_summary, facets, load_case, logs,
                  metric_series, trace_list, trace_spans, verify_files)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError('Organizer validation output already exists')
    expected = {'SF-002': (3600, 84982, 573991, 818797, 118968),
                'SF-003': (1800, 41160, 298746, 433676, 62528),
                'SF-010': (1800, 41581, 295980, 432558, 62868)}
    results = {}
    for case_id, (duration, metrics_count, logs_count, span_count, trace_count) in expected.items():
        case = load_case(case_id)
        if case is None:
            raise RuntimeError('Organizer case absent: ' + case_id)
        observed = counts(case, case['start'], case['end'])
        if (case['end'] - case['start']).total_seconds() != duration or observed != {
                'metrics': metrics_count, 'logs': logs_count,
                'traces': span_count, 'trace_index': trace_count}:
            raise RuntimeError('Case duration or normalized row counts mismatch: ' + case_id)
        checksums = verify_files(case)
        if not all(row['match'] and row['size_match'] for row in checksums.values()):
            raise RuntimeError('Organizer Parquet hash mismatch: ' + case_id)
        inventory = facets(case)
        if not inventory['metrics'] or not inventory['services'] or not inventory['edges']:
            raise RuntimeError('Metric or topology facets missing: ' + case_id)
        metric = ('container_cpu_usage_seconds_total'
                  if 'container_cpu_usage_seconds_total' in inventory['metrics']
                  else inventory['metrics'][0])
        curve = metric_series(case, case['start'], case['end'], metric,
                              None, None, None)
        if curve.empty:
            raise RuntimeError('Full-window Metrics curve empty: ' + case_id)
        log_rows = logs(case, case['start'], case['end'], None, None, '', None)
        filtered_logs = logs(case, case['start'], case['end'],
                             'frontend-proxy', None, '', None)
        if log_rows.empty or filtered_logs.empty:
            raise RuntimeError('Raw or entity-filtered Logs empty: ' + case_id)
        traces = trace_list(case, case['start'], case['end'], None, None, None)
        if traces.empty:
            raise RuntimeError('Trace list empty: ' + case_id)
        trace_id = str(traces.iloc[0]['trace_id'])
        waterfall = trace_spans(case, trace_id)
        if waterfall.empty or len(waterfall) != int(traces.iloc[0]['span_count']):
            raise RuntimeError('Waterfall differs from Trace Index: ' + case_id)
        edges = edge_summary(case, case['start'], case['end'], None, None)
        snapshots = case['manifest']['public_topology'].get('snapshots', [])
        if edges.empty or not snapshots:
            raise RuntimeError('Dependency graph or placement snapshot missing: ' + case_id)
        middle = case['start'] + timedelta(seconds=duration // 2)
        aligned = counts(case, middle, middle + timedelta(minutes=5))
        if any(aligned[name] <= 0 for name in ('metrics', 'logs', 'traces')):
            raise RuntimeError('Five-minute cross-modal alignment gap: ' + case_id)
        results[case_id] = {'status': 'PASS', 'duration_seconds': duration,
            'row_counts': observed, 'sha256_checks': checksums,
            'metric_curve': metric, 'metric_curve_rows': len(curve),
            'raw_log_rows_shown': len(log_rows),
            'filtered_log_rows_shown': len(filtered_logs),
            'waterfall_trace_id': trace_id, 'waterfall_span_count': len(waterfall),
            'dependency_edges_shown': len(edges), 'topology_snapshots': len(snapshots),
            'aligned_five_minute_counts': aligned}
    output = {'status': 'ORGANIZER_PUBLIC_VIEWS_PASS', 'cases': results,
              'private_gt_read': False, 'team_api_opened': False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(output, stream, indent=2, default=str)
        stream.write('\n')
    print(json.dumps({'status': output['status'], 'cases': {
        name: {key: row[key] for key in ('duration_seconds', 'row_counts',
                                         'waterfall_span_count', 'topology_snapshots')}
        for name, row in results.items()}}))


if __name__ == '__main__':
    main()
