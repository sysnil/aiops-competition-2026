"""Canonical minute-series construction from a frozen Public bundle."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
import json
import math

import pandas as pd
import pyarrow.parquet as pq


UNITS = {
    'memory': 'MiB', 'cpu_rate': 'cores', 'server_span_count': 'spans/min',
    'error_log_count': 'logs/min', 'edge_error_count': 'error spans/min',
    'server_p95': 'ms', 'edge_p95': 'ms',
}
KINDS = {
    'memory': 'Metrics', 'cpu_rate': 'Metrics',
    'server_span_count': 'Traces', 'error_log_count': 'Logs',
    'edge_error_count': 'Traces', 'server_p95': 'Traces', 'edge_p95': 'Traces',
}
FILES = {'memory': 'metrics.parquet', 'cpu_rate': 'metrics.parquet',
         'server_span_count': 'traces.parquet', 'error_log_count': 'logs.parquet',
         'edge_error_count': 'traces.parquet', 'server_p95': 'traces.parquet',
         'edge_p95': 'traces.parquet'}


def _timestamps(values):
    return pd.to_datetime(values, utc=True)


def _minute(timestamp, start, count):
    index = int((timestamp - start).total_seconds() // 60)
    return index if 0 <= index < count else None


def _frame(root, columns, filename):
    return pq.read_table(root / filename, columns=columns).to_pandas()


def build_series(root, manifest, spec):
    start = pd.Timestamp(manifest['window']['start_utc'])
    end = pd.Timestamp(manifest['window']['end_utc'])
    span = (end - start).total_seconds()
    if span not in (1800, 3600):
        raise ValueError('Unexpected Public observation duration')
    count = int(span // 60)
    kind, entities = spec['kind'], spec['entities']
    values = {entity: defaultdict(list) for entity in entities}
    evidence = []

    if kind in ('memory', 'cpu_rate'):
        metric = ('container_memory_working_set_bytes' if kind == 'memory'
                  else 'container_cpu_usage_seconds_total')
        rows = _frame(root, ['timestamp', 'metric_name', 'value', 'pod', 'labels'],
                      'metrics.parquet')
        rows = rows[(rows.metric_name == metric) & rows.pod.isin(entities)].copy()
        rows['timestamp'] = _timestamps(rows.timestamp)
        rows = rows[(rows.timestamp >= start) & (rows.timestamp < end)]
        rows = rows[rows.labels.map(lambda raw: json.loads(raw or '{}').get('container') == 'frontend')]
        for entity, group in rows.groupby('pod'):
            group = group.sort_values('timestamp')
            if kind == 'cpu_rate':
                elapsed = group.timestamp.diff().dt.total_seconds()
                delta = group.value.diff()
                rates = delta / elapsed
                group = group.assign(sample_value=rates.where((elapsed > 0) & (delta >= 0)))
            else:
                group = group.assign(sample_value=group.value / 2**20)
            for row in group.itertuples():
                minute = _minute(row.timestamp, start, count)
                if minute is not None and pd.notna(row.sample_value):
                    values[entity][minute].append(float(row.sample_value))
                    if len(evidence) < 6:
                        evidence.append({'timestamp_utc': row.timestamp.isoformat(),
                                         'metric_name': metric, 'pod': entity,
                                         'raw_value': float(row.value)})

    elif kind == 'error_log_count':
        rows = _frame(root, ['timestamp', 'service', 'severity', 'body', 'trace_id'],
                      'logs.parquet')
        rows['timestamp'] = _timestamps(rows.timestamp)
        rows = rows[(rows.service.isin(entities)) &
                    (rows.severity.astype(str).str.upper() == 'ERROR') &
                    (rows.timestamp >= start) & (rows.timestamp < end)]
        for row in rows.itertuples():
            minute = _minute(row.timestamp, start, count)
            if minute is not None:
                values[row.service][minute].append(1)
                if len(evidence) < 6:
                    evidence.append({'timestamp_utc': row.timestamp.isoformat(),
                                     'service': row.service, 'severity': row.severity,
                                     'trace_id': row.trace_id})

    else:
        rows = _frame(root, ['trace_id', 'span_id', 'service', 'parent_service',
                             'operation_name', 'start_time', 'duration_ms',
                             'status', 'attributes'], 'traces.parquet')
        rows['start_time'] = _timestamps(rows.start_time)
        rows = rows[(rows.start_time >= start) & (rows.start_time < end)]
        edge = kind.startswith('edge_')
        if edge:
            left, right = entities[0].split(' -> ', 1)
            rows = rows[(rows.parent_service == left) & (rows.service == right)]
        else:
            rows = rows[rows.service.isin(entities)]
        if spec.get('operation'):
            rows = rows[rows.operation_name == spec['operation']]
        rows = rows[rows.attributes.map(lambda raw: json.loads(raw or '{}').get('span.kind', '').lower() == 'server')]
        if kind == 'edge_error_count':
            rows = rows[rows.status.astype(str).str.upper() == 'ERROR']
        for row in rows.itertuples():
            minute = _minute(row.start_time, start, count)
            if minute is None:
                continue
            entity = entities[0] if edge else row.service
            sample = float(row.duration_ms) if kind.endswith('_p95') else 1
            if kind.endswith('_p95') and (not math.isfinite(sample) or sample < 0):
                continue
            values[entity][minute].append(sample)
            if len(evidence) < 6:
                evidence.append({'timestamp_utc': row.start_time.isoformat(),
                                 'trace_id': row.trace_id, 'span_id': row.span_id,
                                 'service': row.service,
                                 'parent_service': row.parent_service,
                                 'duration_ms': float(row.duration_ms),
                                 'status': row.status})

    points = []
    for entity in entities:
        for minute in range(count):
            samples = values[entity][minute]
            if kind in ('memory', 'cpu_rate'):
                value = sum(samples) / len(samples) if len(samples) >= 2 else None
            elif kind.endswith('_p95'):
                value = sorted(samples)[math.ceil(.95 * len(samples)) - 1] \
                    if len(samples) >= 20 else None
            else:
                value = float(len(samples))
            points.append({'series': entity,
                           'timestamp_utc': (start + timedelta(minutes=minute)).isoformat(),
                           'minute_index': minute, 'value': value,
                           'sample_count': len(samples)})
    return {'unit': UNITS[kind], 'modality': KINDS[kind], 'source_file': FILES[kind],
            'points': points, 'supporting_public_records': evidence}
