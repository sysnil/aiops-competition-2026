"""Compact, read-only variability profile for Public-only question selection."""

from __future__ import annotations

import json

import pandas as pd
import pyarrow.parquet as pq

from inspect_sources import SOURCES


def bucket(values, start):
    return ((pd.to_datetime(values, utc=True) - start).dt.total_seconds() // 60).astype(int)


def profile(case_id, root):
    manifest = json.loads((root / 'manifest.json').read_text())
    start = pd.Timestamp(manifest['window']['start_utc'])
    metrics = pq.read_table(root / 'metrics.parquet',
                            columns=['timestamp', 'metric_name', 'value', 'pod']).to_pandas()
    memory = metrics[metrics.metric_name == 'container_memory_working_set_bytes'].copy()
    memory['minute'] = bucket(memory.timestamp, start)
    memory = memory.groupby(['pod', 'minute']).value.mean().reset_index()
    memory_pods = []
    for pod, group in memory.groupby('pod'):
        if pod and any(name in pod for name in ('frontend', 'recommendation', 'ad', 'checkout')):
            memory_pods.append({'pod': pod, 'minutes': len(group),
                                'min_mib': round(group.value.min() / 2**20, 2),
                                'max_mib': round(group.value.max() / 2**20, 2),
                                'range_mib': round((group.value.max() - group.value.min()) / 2**20, 2)})
    traces = pq.read_table(root / 'traces.parquet',
                           columns=['service', 'parent_service', 'start_time',
                                    'duration_ms', 'status']).to_pandas()
    traces['minute'] = bucket(traces.start_time, start)
    traces['is_error'] = traces.status.astype(str).str.upper().isin(['ERROR', 'STATUS_CODE_ERROR'])
    service = traces.groupby('service').agg(spans=('service', 'size'),
        errors=('is_error', 'sum')).sort_values('spans', ascending=False).head(20)
    edges = traces[traces.parent_service.notna() &
                   (traces.parent_service != traces.service)].groupby(
        ['parent_service', 'service']).agg(spans=('service', 'size'),
        errors=('is_error', 'sum')).sort_values('spans', ascending=False).head(20)
    trend = []
    for name in ('frontend', 'recommendation', 'frontend-proxy', 'checkout',
                 'product-catalog', 'cart'):
        group = traces[traces.service == name].groupby('minute').agg(
            spans=('service', 'size'), errors=('is_error', 'sum'),
            p95=('duration_ms', lambda value: value.quantile(.95)))
        if not group.empty:
            trend.append({'service': name, 'minutes': len(group),
                          'count_min': int(group.spans.min()), 'count_max': int(group.spans.max()),
                          'error_max': int(group.errors.max()),
                          'p95_min_ms': round(group.p95.min(), 2),
                          'p95_max_ms': round(group.p95.max(), 2)})
    logs = pq.read_table(root / 'logs.parquet',
                         columns=['service', 'severity', 'timestamp']).to_pandas()
    logs['minute'] = bucket(logs.timestamp, start)
    bad = logs[logs.severity.astype(str).str.upper() == 'ERROR']
    log_counts = bad.groupby('service').size().sort_values(ascending=False).head(15)
    return {'case_id': case_id, 'status_values': traces.status.value_counts().head(12).to_dict(),
            'memory_pods': sorted(memory_pods, key=lambda row: row['range_mib'], reverse=True),
            'top_services': service.reset_index().to_dict('records'),
            'top_edges': edges.reset_index().to_dict('records'),
            'service_trend': trend, 'error_logs': log_counts.to_dict()}


if __name__ == '__main__':
    print(json.dumps({case_id: profile(case_id, root)
                      for case_id, root in SOURCES.items()}, indent=2))
