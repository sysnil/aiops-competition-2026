#!/usr/bin/env python3
"""Score the registered home-delay effect from frozen Public evidence."""

import argparse
from collections import defaultdict
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import urllib.parse


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def p95(values):
    ordered = sorted(values)
    return ordered[math.ceil(.95 * len(ordered)) - 1] if ordered else None


def main():
    import pyarrow.parquet as pq
    import yaml

    parser = argparse.ArgumentParser()
    for key in ('public', 'private-plan', 'capture-terminal', 'fault-terminal',
                'header-events', 'contract', 'output'):
        parser.add_argument('--' + key, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError('HTTP admission output already exists')
    contract = yaml.safe_load(args.contract.read_text())
    plan = json.loads(args.private_plan.read_text())
    capture = json.loads(args.capture_terminal.read_text())
    fault = json.loads(args.fault_terminal.read_text())
    manifest = json.loads((args.public / 'manifest.json').read_text())
    if (manifest['case_id'] != 'SF-004' or plan['id'] != 'SF-004-ATTEMPT-01' or
            sha(args.contract) != plan['http_admission_sha256'] or
            capture['status'] != 'SF004_FORMAL_CAPTURE_PASS_PENDING_EXPORT'):
        raise RuntimeError('Frozen Public or HTTP contract identity mismatch')
    for name, item in manifest['files'].items():
        if sha(args.public / name) != item['sha256']:
            raise RuntimeError('Public source hash changed before admission')
    t0 = datetime.fromisoformat(
        plan['start_utc'].replace('Z', '+00:00')).timestamp()
    start = t0 + contract['fault_window']['start_offset_seconds']
    end = start + contract['fault_window']['duration_seconds']
    windows = {'before': (start - 120, start), 'during': (start, end),
               'after': (end, end + 120)}
    groups = defaultdict(lambda: defaultdict(dict))
    for batch in pq.ParquetFile(args.public / 'traces.parquet').iter_batches(
            batch_size=5000, columns=['start_time', 'service', 'trace_id',
                                      'duration_ms', 'attributes']):
        for row in batch.to_pylist():
            if row['service'] != 'frontend-proxy':
                continue
            attributes = json.loads(row['attributes'] or '{}')
            if attributes.get('span.kind') != 'server':
                continue
            agent = attributes.get('user_agent')
            path = urllib.parse.urlparse(attributes.get('http.url') or '').path
            if agent == 'business-workload/1.0' and path == '/':
                group = 'home'
            elif agent == 'business-control/1.0' and path == '/api/cart':
                group = 'cart'
            elif agent == 'business-control/1.0' and path.startswith('/images/products/'):
                group = 'image'
            else:
                continue
            at = row['start_time'].timestamp()
            for label, (left, right) in windows.items():
                if left <= at < right:
                    previous = groups[group][label].get(row['trace_id'])
                    groups[group][label][row['trace_id']] = max(
                        row['duration_ms'], previous or 0)
    observed = {name: {label: {'trace_ids': len(groups[name][label]),
        'p95_ms': p95(groups[name][label].values())} for label in windows}
        for name in ('home', 'image', 'cart')}
    effect = contract['public_effect']
    controls = contract['normal_controls']
    checks = {}
    checks['home_public_samples'] = all(
        observed['home'][label]['trace_ids'] >= effect['minimum_' + label + '_trace_ids']
        for label in windows)
    before = observed['home']['before']['p95_ms']
    during = observed['home']['during']['p95_ms']
    after = observed['home']['after']['p95_ms']
    checks['home_public_latency_increase'] = (before is not None and during is not None and
        during >= before + effect['minimum_p95_increase_ms'] and
        during >= before * effect['minimum_p95_ratio'])
    checks['home_public_recovery'] = (before is not None and after is not None and
        after <= max(before * effect['recovery_max_p95_ratio'],
                     before + effect['recovery_max_absolute_increase_ms']))
    for group in ('image', 'cart'):
        minimum = controls['require_' + group + '_successes_before_after']
        during_min = controls['require_' + group + '_successes_during']
        periods = observed[group]
        checks[group + '_normal_coverage'] = (
            periods['before']['trace_ids'] >= minimum and
            periods['during']['trace_ids'] >= during_min and
            periods['after']['trace_ids'] >= minimum)
        normal = periods['before']['p95_ms']
        checks[group + '_normal_latency'] = (normal is not None and
            periods['during']['p95_ms'] is not None and
            periods['after']['p95_ms'] is not None and
            periods['during']['p95_ms'] <=
                normal * controls['maximum_during_to_before_p95_ratio'] and
            periods['after']['p95_ms'] <=
                normal * controls['maximum_after_to_before_p95_ratio'])
    rows = [json.loads(line) for line in args.header_events.read_text().splitlines()]
    marked = [row for row in rows if row['delay_header_applied']]
    when = lambda row: datetime.fromisoformat(row['at_utc'].replace('Z', '+00:00')).timestamp()
    checks['injection_window_and_strength'] = (
        len(marked) >= contract['private_execution']['minimum_delayed_home_requests'] and
        len(marked) == fault['stages']['http_delay']['delayed_requests'] and
        all(start <= when(row) < end and row['requested_delay_ms'] == 250
            for row in marked) and
        all(row['delay_header_applied'] for row in rows if start <= when(row) < end))
    checks['bounded_no_worker_fault_and_recovery'] = (
        plan['actions'] == [] and plan['no_worker_timer'] is True and
        fault['status'] == 'SF004_FAULT_SEQUENCE_COMPLETE_PENDING_CAPTURE' and
        fault['faults'] == {} and fault['stages'].get('http_recovered') is True)
    result = {'status': 'SF004_HTTP_HOME_ADMISSION_PASS' if all(checks.values())
              else 'SF004_HTTP_HOME_ADMISSION_FAILED',
              'case_id': 'SF-004', 'run_id': plan['id'], 'checks': checks,
              'public_proxy_span_windows': observed,
              'observed_delayed_home_requests': len(marked),
              'public_manifest_sha256': sha(args.public / 'manifest.json'),
              'admission_contract_sha256': sha(args.contract),
              'public_evidence_reference': 'traces.parquet: frontend-proxy/server/ordinary business URL',
              'private_header_evidence_never_exported': True}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': result['status'], 'checks': checks}))
    if result['status'] != 'SF004_HTTP_HOME_ADMISSION_PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
