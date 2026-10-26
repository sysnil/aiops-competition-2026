#!/usr/bin/env python3
"""Public-only no-fault check of SF-004's unchanged control rule."""

import argparse
from collections import defaultdict
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import resource
from urllib.parse import urlparse


def sha(path):
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024**2), b''):
            checksum.update(block)
    return checksum.hexdigest()


def read(path):
    return json.loads(path.read_text())


def seconds(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def p95(values):
    ordered = sorted(values)
    return ordered[math.ceil(.95 * len(ordered)) - 1] if ordered else None


def evaluate(plan_path, role, output):
    plan = read(plan_path)
    run = Path(plan['runs'][role]['run_dir'])
    schedule = read(run / 'schedule.json')
    validation = read(run / 'assessment.json')
    if (validation['status'] != 'NO_FAULT_VALIDATION_PASS' or
            schedule['seed'] != plan['runs'][role]['seed'] or
            sha(Path(plan['admission_contract'])) != plan['admission_contract_sha256'] or
            sha(Path(__file__)) != plan['evaluator_sha256'] or output.exists()):
        raise RuntimeError('Unfrozen, incomplete or mismatched no-fault evidence')
    t0 = seconds(schedule['start_utc'])
    start = t0 + plan['anchor_offset_seconds']
    end = start + plan['during_seconds']
    windows = {'before': (start-plan['before_seconds'], start),
               'during': (start, end),
               'after': (end, end+plan['after_seconds'])}
    values = {name: defaultdict(dict) for name in ('image', 'cart')}
    for chunk in ('chunk-02', 'chunk-03'):
        directory = run / 'capture' / chunk / 'trace-final/during/traces'
        for file in sorted(directory.glob('storage-*.json')):
            for trace in read(file)['data']:
                processes = trace['processes']
                for span in trace['spans']:
                    if processes[span['processID']]['serviceName'] != 'frontend-proxy':
                        continue
                    tags = {tag['key']: tag.get('value') for tag in span.get('tags', [])}
                    if tags.get('span.kind') != 'server' or tags.get('user_agent') != 'business-control/1.0':
                        continue
                    path = urlparse(tags.get('http.url') or '').path
                    if path == '/api/cart':
                        name = 'cart'
                    elif path.startswith('/images/products/'):
                        name = 'image'
                    else:
                        continue
                    at = span['startTime'] / 1e6
                    for label, (left, right) in windows.items():
                        if left <= at < right:
                            trace_id = span['traceID'].lower()
                            value = span['duration'] / 1000
                            values[name][label][trace_id] = max(value,
                                values[name][label].get(trace_id, 0))
    observed = {name: {label: {'trace_ids': len(values[name][label]),
                              'p95_ms': p95(values[name][label].values())}
                       for label in windows} for name in values}
    checks = {}
    for name in observed:
        windows_for_name = observed[name]
        minimum = plan['minimum_control_trace_ids'][name]
        checks[name+'_samples'] = all(windows_for_name[label]['trace_ids'] >= minimum[label]
                                     for label in windows)
        baseline = windows_for_name['before']['p95_ms']
        checks[name+'_p95_stability'] = baseline is not None and all(
            windows_for_name[label]['p95_ms'] is not None and
            windows_for_name[label]['p95_ms'] <= baseline * plan['maximum_p95_ratio']
            for label in ('during', 'after'))
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    result = {'status': 'CONTROL_STABILITY_PASS' if all(checks.values()) else 'CONTROL_STABILITY_FAIL',
              'role': role, 'seed': schedule['seed'], 'faults_injected': 0,
              'checks': checks, 'observed': observed,
              'plan_sha256': sha(plan_path), 'validation_sha256': sha(run/'assessment.json'),
              'no_threshold_change': True}
    with output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps(result))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--role', choices=('VAL1', 'VAL2'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_AS, (1_500_000_000, 1_500_000_000))
    evaluate(args.plan, args.role, args.output)
