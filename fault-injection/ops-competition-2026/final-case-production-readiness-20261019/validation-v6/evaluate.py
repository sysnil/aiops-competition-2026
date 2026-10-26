#!/usr/bin/env python3
"""Bounded-memory, public-storage identity audit for two no-fault windows."""

import argparse
from collections import Counter
from datetime import datetime
import gzip
import hashlib
import json
from pathlib import Path
import resource
import sqlite3

from common import DATA, ROOT, now, read, save, sha


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def metric_key(labels):
    return json.dumps(labels, sort_keys=True)


def file_sha(path):
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024**2), b''):
            checksum.update(block)
    return checksum.hexdigest()


def metric_stats(path):
    stats = {}
    samples = 0
    memory_peak = 0
    max_freshness = {'logs': 0, 'traces': 0}
    first_proxy = last_proxy = None
    with path.open() as stream:
        for line in stream:
            row = json.loads(line)
            samples += 1
            values = row['working_set']['data']['result']
            if len(values) != 1:
                raise RuntimeError('Collector working-set identity ambiguous')
            memory_peak = max(memory_peak, float(values[0]['value'][1]))
            for signal in max_freshness:
                age = row['latest_storage'][signal]['age_seconds']
                max_freshness[signal] = max(max_freshness[signal], age)
                if not 0 <= age <= 60:
                    max_freshness[signal] = float('inf')
            proxy = row['envoy_stats'].get('tracing.opentelemetry.spans_dropped')
            if first_proxy is None:
                first_proxy = proxy
            last_proxy = proxy
            for series in row['collector_metrics']['data']['result']:
                key = metric_key(series['metric'])
                value = float(series['value'][1])
                current = stats.get(key)
                if current is None:
                    stats[key] = {'labels': series['metric'], 'first': value,
                                  'last': value, 'max': value, 'samples': 1,
                                  'resets': 0}
                else:
                    if series['metric']['__name__'].endswith('_total') and value < current['last']:
                        current['resets'] += 1
                    current['last'] = value
                    current['max'] = max(current['max'], value)
                    current['samples'] += 1
    return stats, samples, memory_peak, max_freshness, first_proxy, last_proxy


def evaluate(run, plan, role, output, plan_path, diagnostic=False):
    schedule = read(run / 'schedule.json')
    execution = read(run / 'execution-result.json')
    duration = schedule['duration_seconds']
    start = timestamp(schedule['start_utc'])
    if output.exists():
        raise RuntimeError('Assessment exists; no overwrite')
    if plan['runs'][role]['seed'] != schedule['seed'] or duration != 1800:
        raise RuntimeError('Run identity differs from registered plan')
    if not diagnostic and schedule['plan_sha256'] != file_sha(plan_path):
        raise RuntimeError('Run and preregistered plan hash differ')
    expected = {}
    counts = Counter()
    successes = Counter()
    control_periods = {name: {'before': [], 'during': [], 'after': []}
                       for name in ('image_asset', 'cart_view', 'home', 'recommendations')}
    control_minutes = {name: Counter() for name in control_periods}
    with (run / 'state-machine/sessions.jsonl').open() as stream:
        for line in stream:
            for row in json.loads(line)['steps']:
                counts['business'] += 1
                successes['business'] += bool(row.get('success'))
                trace_id = (row.get('trace_id') or '').lower()
                if not trace_id or trace_id in expected:
                    raise RuntimeError('Business trace identity missing or duplicated')
                expected[trace_id] = 'business'
    for name in ('image_asset', 'cart_view', 'home', 'recommendations'):
        with (run / 'controls' / (name + '.jsonl')).open() as stream:
            for line in stream:
                row = json.loads(line)
                counts[name] += 1
                successes[name] += bool(row.get('success'))
                if row.get('success'):
                    elapsed = timestamp(row['time_utc']) - start
                    if 0 <= elapsed < duration:
                        phase = ('before', 'during', 'after')[min(2, int(elapsed // 600))]
                        control_periods[name][phase].append(float(row['latency_ms']))
                        control_minutes[name][int(elapsed // 60)] += 1
                trace_id = (row.get('trace_id') or '').lower()
                if not trace_id or trace_id in expected:
                    raise RuntimeError('Control trace identity missing or duplicated')
                expected[trace_id] = name

    db_path = output.parent / (role + '-span-index.sqlite')
    if db_path.exists():
        raise RuntimeError('Span index already exists; no overwrite')
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(db_path)
    db.execute('PRAGMA cache_size=-32768')
    db.execute('CREATE TABLE span (trace_id TEXT, span_id TEXT, PRIMARY KEY(trace_id,span_id))')
    db.execute('CREATE TABLE proxy (trace_id TEXT PRIMARY KEY)')
    minute_counts = {signal: Counter() for signal in ('metrics', 'logs', 'traces')}
    service_counts = Counter()
    chunk_results = []
    duplicate_spans = 0
    total_spans = 0
    forbidden_public_markers = Counter()
    approved_control_span_tags = 0

    def minute(value):
        index = int((value-start)//60)
        return index if 0 <= index < duration//60 else None

    try:
        for name in sorted(schedule['windows']):
            base = run / 'capture' / name
            first = base / 'captures/during'
            mid = base / 'trace-completion/during'
            final = base / 'trace-final/during'
            audit = base / 'trace-late-audit/during'
            first_manifest = read(first / 'capture-manifest.json')
            mid_manifest = read(mid / 'completion-manifest.json')
            final_manifest = read(final / 'final-manifest.json')
            audit_manifest = read(audit / 'late-audit-manifest.json')
            end = timestamp(schedule['windows'][name]['end_utc'])
            timing = (
                5 <= first_manifest['start_lag_seconds'] <= 10 and
                first_manifest['completion_lag_seconds'] <= 60 and
                60 <= mid_manifest['start_lag_seconds'] <= 65 and
                120 <= timestamp(final_manifest['actual_start_utc'])-end <= 125 and
                240 <= timestamp(audit_manifest['actual_start_utc'])-end <= 245 and
                timestamp(audit_manifest['completed_utc'])-end <= 300 and
                final_manifest['first_manifest_sha256'] == sha(first / 'capture-manifest.json') and
                audit_manifest['final_manifest_sha256'] == sha(final / 'final-manifest.json') and
                final_manifest['first_ids'] <= final_manifest['plus60_ids'] <= final_manifest['final_ids'] and
                audit_manifest['late_count'] == 0 and
                audit_manifest['scoring_archive_changed_by_audit'] is False
            )
            for file in sorted((first / 'metrics').glob('*.json')):
                for series in read(file)['data']['result']:
                    for point in series['values']:
                        index = minute(float(point[0]))
                        if index is not None:
                            minute_counts['metrics'][index] += 1
            for file in sorted((first / 'logs').glob('*.json')):
                for hit in read(file)['hits']['hits']:
                    if 'oc-business-workflow' in json.dumps(hit['_source'], ensure_ascii=True):
                        forbidden_public_markers['logs'] += 1
                    value = hit['_source'].get('@timestamp')
                    if value:
                        index = minute(timestamp(value))
                        if index is not None:
                            minute_counts['logs'][index] += 1
            chunk_spans = 0
            for file in sorted((final / 'traces').glob('storage-*.json')):
                for trace in read(file)['data']:
                    processes = trace['processes']
                    for span in trace['spans']:
                        if any(tag.get('value') == 'oc-business-workflow/1.0'
                               for tag in span.get('tags', [])):
                            forbidden_public_markers['traces'] += 1
                        if any(tag.get('value') == 'business-control/1.0'
                               for tag in span.get('tags', [])):
                            approved_control_span_tags += 1
                        trace_id = span['traceID'].lower()
                        span_id = span['spanID'].lower()
                        try:
                            db.execute('INSERT INTO span VALUES (?,?)', (trace_id, span_id))
                        except sqlite3.IntegrityError:
                            duplicate_spans += 1
                        service = processes[span['processID']]['serviceName']
                        service_counts[service] += 1
                        chunk_spans += 1
                        index = minute(float(span['startTime'])/1e6)
                        if index is not None:
                            minute_counts['traces'][index] += 1
                        if service == 'frontend-proxy' and any(
                                tag.get('key') == 'span.kind' and tag.get('value') == 'server'
                                for tag in span.get('tags', [])):
                            db.execute('INSERT OR IGNORE INTO proxy VALUES (?)', (trace_id,))
                db.commit()
            total_spans += chunk_spans
            chunk_results.append({'chunk': name, 'timing_and_manifest_pass': timing,
                                  'span_count': chunk_spans,
                                  'manifest_final_ids': final_manifest['final_ids'],
                                  'logs': first_manifest['log_records'],
                                  'span_count_matches_manifest': chunk_spans == final_manifest['final_ids']})
        proxy_ids = {row[0] for row in db.execute('SELECT trace_id FROM proxy')}
        missing = {name: [] for name in counts}
        for trace_id, name in expected.items():
            if trace_id not in proxy_ids:
                missing[name].append(trace_id)
    finally:
        db.close()

    stats, monitor_samples, memory_peak, freshness, first_proxy, last_proxy = metric_stats(run / 'monitor.jsonl')
    os_breakers = []
    os_heap = []
    with (run / 'monitor.jsonl').open() as stream:
        for line in stream:
            nodes = json.loads(line)['opensearch']['nodes']
            if len(nodes) != 1:
                raise RuntimeError('OpenSearch node inventory changed')
            node = next(iter(nodes.values()))
            os_breakers.append(node['breakers']['parent']['tripped'])
            os_heap.append(node['jvm']['mem']['heap_used_percent'])
    def p95(samples):
        ordered = sorted(samples)
        return ordered[max(0, (95 * len(ordered) + 99) // 100 - 1)] if ordered else None
    control_p95 = {name: {phase: p95(samples) for phase, samples in phases.items()}
                   for name, phases in control_periods.items()}
    control_stable = all(
        all(control_minutes[name][minute] >= plan['criteria']['control_successes_per_minute_min']
            for minute in range(duration // 60)) and
        all(control_p95[name][phase] is not None and
            control_p95[name][phase] <= control_p95[name]['before'] *
            plan['criteria']['control_p95_ratio_max']
            for phase in ('during', 'after'))
        for name in ('image_asset', 'cart_view'))
    stats_by_name = {}
    for row in stats.values():
        stats_by_name.setdefault(row['labels']['__name__'], []).append(row)
    refusal = {}
    for signal in ('spans', 'log_records', 'metric_points'):
        rows = stats_by_name.get('otelcol_receiver_refused_' + signal + '_total', [])
        complete = bool(rows) and all(row['samples'] == monitor_samples and row['resets'] == 0 for row in rows)
        refusal[signal] = {'status': 'OBSERVED' if complete else 'UNKNOWN',
                           'delta': sum(row['last']-row['first'] for row in rows) if complete else None,
                           'series': rows}
    queues = stats_by_name.get('otelcol_exporter_queue_size', [])
    capacities = stats_by_name.get('otelcol_exporter_queue_capacity', [])
    def queue_key(row):
        return metric_key({k:v for k,v in row['labels'].items() if k != '__name__'})
    capacity_by_key = {queue_key(row): row for row in capacities}
    fraction = plan['criteria']['exporter_queue_peak_capacity_fraction_max']
    growth = plan['criteria']['exporter_queue_final_growth_bytes_max']
    queue_stable = bool(queues) and len(queues) == len(capacities) and all(
        row['samples'] == monitor_samples and queue_key(row) in capacity_by_key and
        capacity_by_key[queue_key(row)]['samples'] == monitor_samples and
        capacity_by_key[queue_key(row)]['last'] > 0 and
        row['max']/capacity_by_key[queue_key(row)]['last'] <= fraction and
        row['last'] <= row['first'] + growth for row in queues)
    raw = gzip.open(run / 'otel-collector-runtime.log.gz', 'rt').read()
    bad_logs = [line for line in raw.splitlines() if any(text in line.lower() for text in (
        'failed to upload metrics', 'export timeout', 'refusing data',
        'dropping data', 'dropping spans'))]
    checks = {
        'execution_complete': execution['status'] == 'VALIDATION_EXECUTION_COMPLETE_PENDING_ASSESSMENT',
        'full_rate_controls': all(counts[item['name']] == round(duration * item['rate_per_second'])
                                  for item in plan['workload']['control_streams']),
        'normal_controls_stable': control_stable,
        'target_stream_samples_sufficient': all(
            all(control_minutes[name][minute] >= plan['criteria']['target_successes_per_minute_min']
                for minute in range(duration // 60)) for name in ('home', 'recommendations')),
        'business_and_controls_successful': all(counts[name] == successes[name] for name in counts),
        'request_proxy_identity_complete': all(not rows for rows in missing.values()),
        'receiver_refused_all_signals_zero': all(row['delta'] == 0 for row in refusal.values()),
        'exporter_queue_stable': queue_stable,
        'collector_working_set_safe': memory_peak < plan['criteria']['collector_working_set_stop_bytes'],
        'no_collector_refusal_or_timeout_logs': not bad_logs,
        'proxy_span_drop_counter_zero': first_proxy is not None and last_proxy-first_proxy == 0,
        'all_chunks_complete': all(row['timing_and_manifest_pass'] and
            row['span_count_matches_manifest'] and row['logs'] > 0 for row in chunk_results),
        'all_30_minutes_three_modalities': all(all(minute_counts[signal][i] > 0
            for i in range(duration//60)) for signal in minute_counts),
        'no_duplicate_spans': duplicate_spans == 0,
        'public_safe_business_identity': not forbidden_public_markers and
            approved_control_span_tags >= plan['criteria']['approved_control_span_tags_min'],
        'storage_freshness': all(value <= 60 for value in freshness.values()),
        'opensearch_parent_breaker_no_new_trips': bool(os_breakers) and
            max(os_breakers) == min(os_breakers),
        'registered_sources_unchanged': all(sha(path) == value for path,value in plan['source_sha256'].items()),
    }
    outcome = 'PASS' if all(checks.values()) else 'FAIL'
    result = {'status': ('DIAGNOSTIC_REPLAY_' if diagnostic else 'NO_FAULT_VALIDATION_') + outcome,
              'role': role, 'at_utc': now(), 'checks': checks,
              'blockers': [name for name,value in checks.items() if not value],
              'seed': schedule['seed'], 'duration_seconds': duration,
              'faults_injected': 0, 'diagnostic_only': diagnostic,
              'config_sha256': schedule['config_sha256'],
              'request_counts': dict(counts), 'request_successes': dict(successes),
              'missing_proxy_trace_ids': missing, 'unique_proxy_trace_ids': len(proxy_ids),
              'total_span_records': total_spans, 'duplicate_spans': duplicate_spans,
              'service_span_counts': dict(service_counts), 'chunks': chunk_results,
              'forbidden_public_marker_counts': dict(forbidden_public_markers),
              'approved_control_span_tags': approved_control_span_tags,
              'normal_control_p95_ms': control_p95,
              'normal_control_minute_counts': {name: dict(rows) for name, rows in control_minutes.items()},
              'opensearch_parent_breaker_trips': {'first': os_breakers[0], 'last': os_breakers[-1]},
              'opensearch_heap_peak_percent': max(os_heap),
              'per_minute_counts': {signal: dict(rows) for signal,rows in minute_counts.items()},
              'receiver_refused': refusal, 'exporter_queues': queues,
              'exporter_queue_capacities': capacities, 'working_set_peak_bytes': memory_peak,
              'storage_freshness_max_seconds': freshness, 'collector_error_log_matches': bad_logs,
              'monitor_samples': monitor_samples, 'span_index_path': str(db_path),
              'span_index_sha256': file_sha(db_path)}
    save(output, result)
    print(json.dumps({'status': result['status'], 'role': role,
                      'blockers': result['blockers'], 'request_counts': result['request_counts'],
                      'missing_counts': {name: len(rows) for name,rows in missing.items()},
                      'working_set_peak_bytes': memory_peak}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--role', choices=('VAL6',), required=True)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--run-dir', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--diagnostic', action='store_true')
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_AS, (2_000_000_000, 2_000_000_000))
    plan_path = args.plan or ROOT / 'design/full-chain-low-stable-public-safe-contract.json'
    run_dir = args.run_dir or DATA / 'validation-runs-v6' / args.role
    output_path = args.output or run_dir / 'assessment.json'
    if args.diagnostic and not all((args.plan, args.run_dir, args.output)):
        raise RuntimeError('Diagnostic replay requires explicit plan, run dir and output')
    evaluate(run_dir, read(plan_path), args.role, output_path, plan_path, args.diagnostic)
