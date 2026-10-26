#!/usr/bin/env python3
"""Audit immutable six-chunk metrics/logs/traces and no-fault controls."""

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

CAP = Path('/root/ops-competition-2026/low-stable-v8-20261020-capture')
sys.path.insert(0, str(CAP))
from trace_identity import span_map, subset


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024**2), b''):
            digest.update(block)
    return digest.hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def audit(run, freeze):
    run = Path(run)
    freeze = read(freeze)
    if freeze['status'] != 'FROZEN_HARMLESS_1800_CANDIDATE':
        raise ValueError('NOFAULT_FREEZE_STATUS')
    for source, expected in freeze['source_sha256'].items():
        if sha(source) != expected:
            raise ValueError('NOFAULT_SOURCE_DRIFT:' + source)
    schedule = read(run / 'schedule.json')
    if schedule['run_id'] != freeze['run_id'] or schedule['duration_seconds'] != 1800:
        raise ValueError('NOFAULT_SCHEDULE_IDENTITY')
    collector = read(run / 'observer/terminal.json')
    controller = read(run / 'control/controller-terminal.json')
    guard = read(run / 'control/guard-terminal.json')
    workload = read(run / 'state-machine/result-manifest.json')
    controls = read(run / 'controls/result-manifest.json')
    watchdog = read(run / 'control/watchdog-result.json')
    if (collector['status'] != 'OBSERVER_COMPLETE' or
            collector['baseline_counters'] != collector['final_counters'] or
            controller['status'] != 'CONTROL_SEQUENCE_COMPLETE_PENDING_ADMISSION' or
            guard['status'] != 'GUARD_CLEANUP_VERIFIED' or
            watchdog['status'] != 'SACRIFICIAL_WATCHDOG_PASS' or
            workload['status'] != 'LOAD_PASS' or
            controls['status'] != 'CONTROL_STREAMS_COMPLETE' or
            any(row['missed_arrivals'] or
                row['successful_requests'] != row['planned_requests']
                for row in controls['streams'].values())):
        raise RuntimeError('NOFAULT_CONTROL_OBSERVER_OR_WORKLOAD_FAILED')
    chunks = []
    proxy_ids = set()
    late_counts = []
    source_indices = None
    for index in range(6):
        chunk = run / 'capture' / f'chunk-{index:02d}'
        first = chunk / 'captures/during'
        plus60 = chunk / 'trace-completion/during'
        final = chunk / 'trace-final/during'
        late = chunk / 'trace-late-audit/during'
        fm = read(first / 'capture-manifest.json')
        cm = read(plus60 / 'completion-manifest.json')
        fin = read(final / 'final-manifest.json')
        am = read(late / 'late-audit-manifest.json')
        window = schedule['windows'][f'chunk-{index:02d}']
        if (fm['window']['start_utc'] != window['start_utc'] or
                fm['window']['end_utc'] != window['end_utc'] or
                fm['trace_source'] != 'JAEGER_OPENSEARCH_STORAGE' or
                fm['trace_records'] <= 0 or fm['span_records'] <= 0 or
                fm['log_records'] <= 0 or
                not list((first / 'metrics').glob('*.json')) or
                cm.get('status') not in ('LATE_SPANS_ADDED', 'NO_LATE_SPANS') or
                fin['status'] != 'FINAL_SNAPSHOT_FROZEN' or
                am['status'] != 'LATE_AUDIT_FROZEN_UNCLASSIFIED'):
            raise RuntimeError('NOFAULT_CHUNK_MODALITY_OR_WINDOW:' + str(index))
        if (fin['first_manifest_sha256'] != sha(first / 'capture-manifest.json') or
                am['final_manifest_sha256'] != sha(final / 'final-manifest.json')):
            raise RuntimeError('NOFAULT_CHUNK_HASH_CHAIN:' + str(index))
        if not (5 <= fm['start_lag_seconds'] <= 10 and
                fm['completion_lag_seconds'] <= 60):
            raise RuntimeError('NOFAULT_FIRST_CAPTURE_FRESHNESS:' + str(index))
        current_indices = fm['trace_storage_indices']
        if source_indices is None:
            source_indices = current_indices
        elif current_indices != source_indices:
            raise RuntimeError('NOFAULT_TRACE_STORAGE_SOURCE_CHANGED')
        snapshots = [span_map(path) for path in (first, plus60, final, late)]
        for prior, later in zip(snapshots, snapshots[1:]):
            subset(prior, later)
        proxy_ids.update(key[0].lower() for key, (_, span) in snapshots[2].items()
                         if span.get('process', {}).get('serviceName') == 'frontend-proxy'
                         and span.get('tag', {}).get('span@kind') == 'server')
        late_count = len(snapshots[3]) - len(snapshots[2])
        late_counts.append(late_count)
        chunks.append({'chunk': index, 'metrics_files': len(list((first / 'metrics').glob('*.json'))),
                       'logs': fm['log_records'], 'traces': fm['trace_records'],
                       'first_spans': len(snapshots[0]),
                       'final_spans': len(snapshots[2]),
                       'late_after_plus120': late_count,
                       'final_manifest_sha256': sha(final / 'final-manifest.json')})
    controls_path = run / 'controls/control-events-live.jsonl'
    requests = [json.loads(row) for row in controls_path.read_text().splitlines()]
    identified = [row for row in requests if row.get('trace_id')]
    matched = [row for row in identified if row['trace_id'].lower() in proxy_ids]
    missing = [row['trace_id'] for row in identified
               if row['trace_id'].lower() not in proxy_ids]
    if missing:
        raise RuntimeError('NOFAULT_CONTROL_PROXY_SPAN_GAP:' + str(len(missing)))
    if any(late_counts):
        raise RuntimeError('NOFAULT_UNCLASSIFIED_LATE_SPANS:' + str(sum(late_counts)))
    return {'status': 'FULL_1800_NOFAULT_DATA_QUALITY_PASS',
            'run_id': schedule['run_id'], 'chunks': chunks,
            'control_requests': len(identified),
            'matched_proxy_traces': len(matched),
            'collector_refused_delta': {key: 0 for key in collector['baseline_counters']},
            'manifest_sha256': freeze['manifest_sha256']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--freeze', required=True, type=Path)
    args = parser.parse_args()
    output = args.run / 'quality-audit.json'
    if output.exists():
        raise ValueError('NOFAULT_AUDIT_IMMUTABLE')
    try:
        result = audit(args.run, args.freeze)
    except BaseException as error:
        result = {'status': 'FULL_1800_NOFAULT_DATA_QUALITY_FAILED',
                  'reason': type(error).__name__ + ':' + str(error)}
        output.write_text(json.dumps(result, indent=2) + '\n')
        raise
    output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'status': result['status'], 'chunks': len(result['chunks']),
                      'control_requests': result['control_requests']}))


if __name__ == '__main__':
    main()
