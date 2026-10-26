#!/usr/bin/env python3
"""Full-window ordinary business Image/Payment edge counts and client latencies."""

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import statistics

from clean_protocol import save_new, sha


TRACE_SOURCE = Path('/root/stage5-v3-clean-gate-v2/scripts/trace_identity.py')


def percentile(rows, fraction):
    values = sorted(rows)
    return values[int((len(values) - 1) * fraction)] if values else None


def spans_by_phase(run):
    spec = importlib.util.spec_from_file_location('trace_identity', TRACE_SOURCE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    all_rows, sources = {}, {}
    for phase in ('before', 'during', 'after'):
        root = run / 'capture-pipeline/trace-final' / phase
        rows = mod.span_map(root)
        sources[phase] = sha(root / 'final-manifest.json')
        for identity, row in rows.items():
            if identity in all_rows and all_rows[identity][0] != row[0]:
                raise RuntimeError('Trace identity changed across phases')
            all_rows[identity] = row
    return {key: row[1] for key, row in all_rows.items()}, sources


def analyze(run):
    schedule = json.loads((run / 'schedule.json').read_text())
    start = datetime.fromisoformat(schedule['scheduled_start_utc']).timestamp()
    source_lock = json.loads((run / 'source-lock.json').read_text())
    worker1_pay = [row[0] for row in source_lock['node_placement']['payment']
                   if row[1] == 'k8s-worker1']
    if len(worker1_pay) != 1:
        raise RuntimeError('Payment W1 Pod UID ambiguous')
    spans, sources = spans_by_phase(run)
    classes = {name: [set() for _ in range(25)] for name in
               ('image', 'cart', 'payment')}
    client_ms = {name: [[] for _ in range(25)] for name in
                 ('image', 'cart', 'payment')}
    uid_mismatch = {name: 0 for name in ('image', 'cart', 'payment')}
    expected_image_uid = source_lock['pod_uids']['image-provider'][0]
    expected_cart_uid = source_lock['pod_uids']['cart'][0]
    fixed_ids = {}
    for name, stream in (('image', 'image_asset'), ('cart', 'cart_view')):
        path = run / 'load_pair/controls' / (stream + '.jsonl')
        fixed_ids[name] = {row['trace_id'].lower() for row in
            (json.loads(line) for line in path.read_text().splitlines())
            if row.get('trace_id')}
    for span in spans.values():
        service = span['process']['serviceName']
        if service not in ('image-provider', 'cart', 'payment') or (
                span.get('tag') or {}).get('span@kind') != 'server':
            continue
        minute = int((span['startTime'] / 1_000_000 - start) // 60)
        if not 0 <= minute < 25:
            continue
        uid = {row.get('key'): row.get('value') for row in
               span['process'].get('tags') or []}.get('k8s.pod.uid')
        name = {'image-provider': 'image', 'cart': 'cart',
                'payment': 'payment'}[service]
        if name in fixed_ids and span['traceID'].lower() not in fixed_ids[name]:
            continue
        expected_uid = {'image': expected_image_uid, 'cart': expected_cart_uid,
                        'payment': worker1_pay[0]}[name]
        if uid != expected_uid:
            uid_mismatch[name] += 1
            continue
        expected_parent = {'image': 'frontend-proxy', 'cart': 'frontend',
                           'payment': 'checkout'}[name]
        for ref in span.get('references') or []:
            parent = spans.get((ref.get('traceID', '').lower(),
                                ref.get('spanID', '').lower()))
            if parent and parent['process']['serviceName'] == expected_parent and (
                    parent.get('tag') or {}).get('span@kind') == 'client':
                classes[name][minute].add(span['traceID'].lower())
                client_ms[name][minute].append(parent['duration'] / 1000)
                break
    details = {}
    for name in ('image', 'cart', 'payment'):
        counts = [len(value) for value in classes[name]]
        p95 = [percentile(value, .95) for value in client_ms[name]]
        if any(value is None for value in p95[:10]):
            raise RuntimeError(name + ' Before has missing p95 minute')
        baseline = statistics.median(p95[:10])
        ratios = [value / baseline if value is not None and baseline > 0 else None
                  for value in p95]
        details[name] = {'client_server_edge_trace_ids_per_minute': counts,
            'client_p95_ms_per_minute': p95,
            'client_p95_ratio_to_before_median_per_minute': ratios,
            'baseline_p95_ms': baseline, 'server_uid_mismatch_count': uid_mismatch[name],
            'minimum_edge_spans_per_minute': min(counts),
            'per_minute_30_spans_pass': all(value >= 30 for value in counts),
            'old_per_minute_1_25_ratio_pass': all(value is not None and
                value <= 1.25 for value in ratios[10:])}
    return {'status': 'NETWORK_CONTROLS_FULL_WINDOW_AUDITED',
        'run_id': run.name, 'target_path':
            'NETWORK_PATH/k8s-worker2/k8s-worker3',
        'image': details['image'], 'cart': details['cart'],
        'payment_worker1': details['payment'],
        'payment_worker1_uid': worker1_pay[0],
        'payment_worker2_not_counted_as_control': True,
        'tc_filter_scope_independent_by_destination': True,
        'shared_checkout_source_independence_under_fault_verified': False,
        'normal_control_qualification': 'CONDITIONAL_UNTIL_FAULT_SCOPE_VALIDATION',
        'trace_final_manifest_sha256': sources,
        'source_lock_sha256': sha(run / 'source-lock.json'),
        'fault_injected': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    save_new(args.output, analyze(args.run))
    print(json.dumps({'status': 'NETWORK_CONTROLS_FULL_WINDOW_AUDITED',
        'run_id': args.run.name}))


if __name__ == '__main__': main()
