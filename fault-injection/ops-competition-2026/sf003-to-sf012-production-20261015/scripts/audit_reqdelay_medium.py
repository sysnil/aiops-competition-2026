#!/usr/bin/env python3
"""Audit request-level latency and Jaeger storage identity after MEDIUM rehearsal."""

import argparse
import hashlib
import json
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from audit_reqdelay_trace import fetch


RUN = Path('/data/stage5-v3/ops-competition-2026/rehearsals/SF30-REQDELAY-MEDIUM-20261015')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def phase(at, start, fault_start, fault_end, end):
    if not start <= at < end:
        return None
    if at < fault_start:
        return 'before'
    if at < fault_end:
        return 'during'
    return 'after'


def distribution(rows):
    values = sorted(row['latency_ms'] for row in rows if row.get('latency_ms') is not None)
    return {'count': len(rows), 'latency_samples': len(values),
            'p50_ms': statistics.median(values) if values else None,
            'p95_ms': values[int(.95 * (len(values) - 1))] if values else None,
            'successes': sum(row.get('success') is True for row in rows)}


def proxy_match(span_count):
    return span_count >= 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--audit-offset-seconds', type=int, choices=(60, 120, 240), required=True)
    args = parser.parse_args()
    if not (RUN / 'terminal.json').exists():
        raise RuntimeError('Rehearsal has not reached terminal state')
    plan = json.loads((RUN / 'registration.json').read_text())
    bounds = [stamp(plan[key]) for key in ('start_utc', 'fault_start_utc',
                                             'fault_end_utc', 'end_utc')]
    if datetime.now(timezone.utc).timestamp() < bounds[-1] + args.audit_offset_seconds:
        raise RuntimeError('Trace completion audit was requested too early')
    business_path = RUN / 'state-machine/request-events-live.jsonl'
    control_path = RUN / 'controls/control-events-live.jsonl'
    header_path = RUN / 'state-machine/home-header-events.jsonl'
    business = [json.loads(line) for line in business_path.read_text().splitlines()]
    controls = [json.loads(line) for line in control_path.read_text().splitlines()]
    headers = [json.loads(line) for line in header_path.read_text().splitlines()]
    header_by_trace = {row['traceparent'].split('-')[1]: row for row in headers}
    home = [row for row in business if row['operation'] == 'home']
    if len(header_by_trace) != len(headers) or set(header_by_trace) != {
            row['trace_id'] for row in home}:
        raise RuntimeError('Home request/header identity mismatch')
    for row in home:
        header = header_by_trace[row['trace_id']]
        expected = phase(stamp(header['at_utc']), *bounds) == 'during'
        if header['delay_header_applied'] != expected:
            raise RuntimeError('Header timing mismatch')
    selected = home + controls
    ids = [row['trace_id'] for row in selected]
    if None in ids or len(ids) != len(set(ids)):
        raise RuntimeError('Missing or duplicate request identity')
    index = 'jaeger-jaeger-span-' + datetime.fromtimestamp(bounds[0], timezone.utc).date().isoformat()
    proxy = Counter()
    span_count = 0
    span_ids = set()
    for offset in range(0, len(ids), 50):
        group = ids[offset:offset + 50]
        for hit in fetch(index, group):
            span_count += 1
            span = hit['_source']
            identity = (span['traceID'], span['spanID'])
            if identity in span_ids:
                raise RuntimeError('Duplicate stored Trace/Span identity')
            span_ids.add(identity)
            if span['process']['serviceName'] == 'frontend-proxy' and (
                    span.get('tag', {}).get('span@kind') == 'server'):
                proxy[span['traceID']] += 1
    by_phase = {}
    for name in ('before', 'during', 'after'):
        home_rows = [row for row in home if phase(
            stamp(header_by_trace[row['trace_id']]['at_utc']), *bounds) == name]
        control_rows = {label: [row for row in controls if row['operation'] == label
            and phase(stamp(row['time_utc']), *bounds) == name]
            for label in ('image_asset', 'cart_view')}
        by_phase[name] = {'home': distribution(home_rows),
                          'image': distribution(control_rows['image_asset']),
                          'cart': distribution(control_rows['cart_view']),
                          'home_delay_header_count': sum(header_by_trace[row['trace_id']]
                              ['delay_header_applied'] for row in home_rows),
                          'proxy_server_matches': sum(proxy_match(proxy[row['trace_id']]) for row in
                              home_rows + control_rows['image_asset'] + control_rows['cart_view']),
                          'multi_ingress_trace_count': sum(proxy[row['trace_id']] > 1 for row in
                              home_rows + control_rows['image_asset'] + control_rows['cart_view']),
                          'selected_request_count': len(home_rows) + sum(
                              len(group) for group in control_rows.values())}
    complete = all(value['proxy_server_matches'] == value['selected_request_count']
                   for value in by_phase.values())
    result = {'status': 'MEDIUM_TRACE_MATCH_COMPLETE' if complete else 'MEDIUM_TRACE_MATCH_INCOMPLETE',
              'at_utc': datetime.now(timezone.utc).isoformat(),
              'audit_offset_seconds': args.audit_offset_seconds,
              'trace_source': 'JAEGER_OPENSEARCH_STORAGE', 'index': index,
              'selected_request_count': len(selected), 'unique_spans_read': span_count,
              'request_files_sha256': {str(path): sha(path) for path in
                  (business_path, control_path, header_path)},
              'phases': by_phase, 'formal_case_created': False}
    path = RUN / f'medium-trace-audit-plus-{args.audit_offset_seconds}.json'
    with path.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': result['status'],
                      'selected_requests': result['selected_request_count'],
                      'phase_matches': {name: [row['proxy_server_matches'],
                          row['selected_request_count']] for name, row in by_phase.items()},
                      'path': str(path)}))


if __name__ == '__main__':
    main()
