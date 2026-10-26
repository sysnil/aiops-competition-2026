#!/usr/bin/env python3
"""Prospective final-only telemetry gate. It never reads GT or injection plans."""

import argparse
import hashlib
import json
import sys
from pathlib import Path
from datetime import datetime


CAPTURE = Path('/root/ops-competition-2026/low-stable-v4-20261017/capture')
if not CAPTURE.is_dir():
    CAPTURE = Path(__file__).resolve().parents[2] / 'low-stable-v4-20261017/capture'
sys.path.insert(0, str(CAPTURE))
from trace_identity import span_map, subset


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024**2), b''):
            digest.update(block)
    return digest.hexdigest()


def normal_requests(rows, controls):
    return [row for row in rows if row.get('operation') in controls and
            row.get('trace_id')]


def collect(run, contract):
    run = Path(run)
    schedule = read(run / 'schedule.json')
    observer = read(run / 'observer/terminal.json')
    controls = read(run / 'controls/result-manifest.json')
    rows = [json.loads(line) for line in
            (run / 'controls/control-events-live.jsonl').read_text().splitlines()]
    proxy_ids = set()
    chunks = []
    trace_indices = None
    for index in range(contract['chunks']):
        path = run / 'capture' / f'chunk-{index:02d}'
        first = path / 'captures/during'
        final = path / 'trace-final/during'
        late = path / 'trace-late-audit/during'
        first_manifest = read(first / 'capture-manifest.json')
        final_manifest = read(final / 'final-manifest.json')
        late_manifest = read(late / 'late-audit-manifest.json')
        first_spans, final_spans, late_spans = (
            span_map(item) for item in (first, final, late))
        subset(first_spans, final_spans)
        subset(final_spans, late_spans)
        proxy_ids.update(key[0].lower() for key, (_, span) in late_spans.items()
                         if span.get('process', {}).get('serviceName') ==
                         'frontend-proxy' and
                         span.get('tag', {}).get('span@kind') == 'server')
        current_indices = first_manifest['trace_storage_indices']
        if trace_indices is None:
            trace_indices = current_indices
        window = schedule['windows'][f'chunk-{index:02d}']
        chunks.append({
            'chunk': index,
            'window_matches': all(first_manifest['window'][key] == window[key]
                                  for key in ('start_utc', 'end_utc')),
            'trace_source': first_manifest['trace_source'],
            'trace_indices_stable': current_indices == trace_indices,
            'trace_records': first_manifest['trace_records'],
            'log_records': first_manifest['log_records'],
            'metric_files': len(list((first / 'metrics').glob('*.json'))),
            'start_lag_seconds': first_manifest['start_lag_seconds'],
            'completion_lag_seconds': first_manifest['completion_lag_seconds'],
            'hash_chain_valid': (
                final_manifest['first_manifest_sha256'] ==
                sha(first / 'capture-manifest.json') and
                late_manifest['final_manifest_sha256'] ==
                sha(final / 'final-manifest.json')),
            'final_spans': len(final_spans),
            'late_spans': len(late_spans) - len(final_spans)
        })
    identified = normal_requests(rows, contract['normal_controls'])
    unmatched = [row['trace_id'] for row in identified
                 if row['trace_id'].lower() not in proxy_ids]
    by_name = {name: {'identified': sum(row['operation'] == name
                                        for row in identified),
                      'matched': sum(row['operation'] == name and
                                     row['trace_id'].lower() in proxy_ids
                                     for row in identified)}
               for name in contract['normal_controls']}
    target_rows = [row for row in rows if row.get('trace_id') and
                   row.get('operation') not in contract['normal_controls']]
    intervals = [(datetime.fromisoformat(schedule['windows'][f'chunk-{index:02d}'][
                      'start_utc']).timestamp(),
                  datetime.fromisoformat(schedule['windows'][f'chunk-{index:02d}'][
                      'end_utc']).timestamp())
                 for index in range(contract['chunks'])]
    control_by_chunk = {name: [0] * contract['chunks']
                        for name in contract['normal_controls']}
    for row in rows:
        name = row.get('operation')
        started = row.get('request_started_utc') or row.get('time_utc')
        if name not in control_by_chunk or row.get('success') is not True or not started:
            continue
        epoch = datetime.fromisoformat(started.replace('Z', '+00:00')).timestamp()
        for index, (begin, end) in enumerate(intervals):
            if begin <= epoch < end:
                control_by_chunk[name][index] += 1
                break
    return {
        'run_id': schedule['run_id'],
        'duration_seconds': schedule['duration_seconds'],
        'chunks': chunks,
        'collector_delta': {key: observer['final_counters'][key] - value
                            for key, value in observer['baseline_counters'].items()},
        'observer_status': observer['status'],
        'control_streams': controls['streams'],
        'control_success_by_chunk': control_by_chunk,
        'identified_control_requests': len(identified),
        'matched_control_proxy_spans': len(identified) - len(unmatched),
        'unmatched_control_trace_ids': unmatched,
        'control_proxy_match_by_name': by_name,
        'target_request_count': len(target_rows),
        'target_request_proxy_span_missing_count': sum(
            row['trace_id'].lower() not in proxy_ids for row in target_rows),
        'normal_control_scope_only': True
    }


def assess(summary, contract):
    issues = []
    if summary['duration_seconds'] != contract['window_seconds'] or \
            len(summary['chunks']) != contract['chunks']:
        issues.append('WINDOW_OR_CHUNK_COUNT')
    if summary['observer_status'] != 'OBSERVER_COMPLETE' or any(
            value > contract['collector_receiver_refused_delta_max']
            for value in summary['collector_delta'].values()):
        issues.append('COLLECTOR_OR_OBSERVER')
    late = 0
    final = 0
    low_lag, high_lag = contract['first_capture_start_lag_seconds']
    for chunk in summary['chunks']:
        if (not chunk['window_matches'] or not chunk['trace_indices_stable'] or
                chunk['trace_source'] != contract['trace_source'] or
                not chunk['hash_chain_valid']):
            issues.append(f'CHUNK_PROVENANCE:{chunk["chunk"]}')
        if min(chunk['trace_records'], chunk['log_records'],
               chunk['metric_files']) <= 0:
            issues.append(f'CHUNK_MODALITY_MISSING:{chunk["chunk"]}')
        if (not low_lag <= chunk['start_lag_seconds'] <= high_lag or
                chunk['completion_lag_seconds'] >
                contract['first_capture_completion_lag_seconds_max']):
            issues.append(f'CHUNK_FRESHNESS:{chunk["chunk"]}')
        late += chunk['late_spans']
        final += chunk['final_spans']
    if final <= 0 or late / max(1, final) > \
            contract['trace_late_after_plus120_fraction_max']:
        issues.append('TRACE_LATE_FRACTION')
    for name in contract['normal_controls']:
        stream = summary['control_streams'].get(name)
        if not stream or stream['planned_requests'] <= 0:
            issues.append(f'CONTROL_ABSENT:{name}')
            continue
        per_chunk = summary['control_success_by_chunk'].get(name)
        if (stream['successful_requests'] / stream['planned_requests'] <
                contract['normal_control_success_fraction_min'] or
                per_chunk is None or len(per_chunk) != contract['chunks'] or
                min(per_chunk) <
                contract['normal_control_min_successes_per_300s_chunk']):
            issues.append(f'CONTROL_COVERAGE:{name}')
        matched = summary['control_proxy_match_by_name'].get(name)
        if (not matched or matched['identified'] <= 0 or
                matched['matched'] / matched['identified'] <
                contract['normal_control_proxy_span_match_fraction_min']):
            issues.append(f'CONTROL_PROXY_SPAN_MATCH:{name}')
    if (summary['identified_control_requests'] <= 0 or
            summary['matched_control_proxy_spans'] /
            summary['identified_control_requests'] <
            contract['normal_control_proxy_span_match_fraction_min']):
        issues.append('CONTROL_PROXY_SPAN_MATCH')
    return {**summary, 'status': 'DATA_COLLECTION_ACCEPTED_FOR_ADMISSION'
            if not issues else 'DATA_COLLECTION_BLOCKED',
            'issues': issues, 'late_span_fraction': late / max(1, final),
            'boundary': 'No injection or GT admission decision is made here.'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--contract', type=Path, required=True)
    args = parser.parse_args()
    contract = read(args.contract)
    result = assess(collect(args.run, contract), contract)
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(result['status'] != 'DATA_COLLECTION_ACCEPTED_FOR_ADMISSION')


if __name__ == '__main__':
    main()
