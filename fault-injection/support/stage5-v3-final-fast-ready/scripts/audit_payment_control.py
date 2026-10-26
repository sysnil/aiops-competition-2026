#!/usr/bin/env python3
"""Descriptive Worker1 Payment control quality; no post-hoc qualification."""

from collections import defaultdict
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import statistics

from clean_protocol import save_new, sha


RUN = Path('/data/stage5-v3/final-fast-ready/runs/S5V3FFR-CLEAN-20261013-S1')
OUT = Path('/root/stage5-v3-final-fast-ready/results/payment-control-full-window-diagnostic.json')
SOURCE = Path('/root/stage5-v3-clean-gate-v2/scripts/trace_identity.py')
ROUTE = Path('/root/stage5-v3-final-fast-ready/results/network-proof-clean-s1-v2.json')


def percentile(values, fraction):
    values = sorted(values)
    return values[int((len(values) - 1) * fraction)] if values else None


def main():
    spec = importlib.util.spec_from_file_location('trace_identity', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    schedule = json.loads((RUN / 'schedule.json').read_text())
    lock = json.loads((RUN / 'source-lock.json').read_text())
    route = json.loads(ROUTE.read_text())
    start = datetime.fromisoformat(schedule['scheduled_start_utc']).timestamp()
    worker1_uid = [row[0] for row in lock['node_placement']['payment']
                   if row[1] == 'k8s-worker1']
    if len(worker1_uid) != 1 or route['status'] != 'ROUTE_FLOW_PROOF_PASS' or not \
            route['payment_route_avoids_target']:
        raise RuntimeError('Payment control Pod/route identity absent')
    by_minute = [dict() for _ in range(25)]
    missing_uid = 0
    sources = {}
    for phase in ('before', 'during', 'after'):
        root = RUN / 'capture-pipeline/trace-final' / phase
        spans = {key: row[1] for key, row in module.span_map(root).items()}
        sources[phase] = sha(root / 'final-manifest.json')
        for span in spans.values():
            if span['process']['serviceName'] != 'payment' or (
                    span.get('tag') or {}).get('span@kind') != 'server':
                continue
            minute = int((span['startTime'] / 1_000_000 - start) // 60)
            if not 0 <= minute < 25: continue
            uid = {r.get('key'): r.get('value') for r in span[
                'process'].get('tags') or []}.get('k8s.pod.uid')
            if uid != worker1_uid[0]:
                missing_uid += 1
                continue
            for ref in span.get('references') or []:
                parent = spans.get((ref.get('traceID', '').lower(),
                                    ref.get('spanID', '').lower()))
                if parent and parent['process']['serviceName'] == 'checkout' and (
                        parent.get('tag') or {}).get('span@kind') == 'client':
                    by_minute[minute][span['traceID']] = parent['duration'] / 1000
                    break
    count = [len(bucket) for bucket in by_minute]
    p95 = [percentile(list(bucket.values()), .95) for bucket in by_minute]
    before = p95[:10]
    baseline = statistics.median(before) if all(x is not None for x in before) else None
    ratio = [x / baseline if x is not None and baseline else None for x in p95]
    result = {'status': 'PAYMENT_W1_FULL_WINDOW_DESCRIPTIVE_NOT_QUALIFIED',
        'run_id': RUN.name, 'worker1_payment_pod_uid': worker1_uid[0],
        'trace_edge_ids_per_minute': count,
        'client_p95_ms_per_minute': p95,
        'client_p95_ratio_to_before_median_per_minute': ratio,
        'minimum_spans_per_minute': min(count),
        'all_minutes_at_least_30_spans': min(count) >= 30,
        'all_evaluated_minutes_ratio_at_most_1_25': all(x is not None and
            x <= 1.25 for x in ratio[10:]),
        'server_uid_unattributed_or_other_count': missing_uid,
        'route_avoids_w2_w3_target': True,
        'shared_checkout_source_independence_under_fault': 'UNVERIFIED',
        'original_scoring_status_changed': False,
        'source_sha256': {'route': sha(ROUTE), 'source_lock': sha(RUN /
            'source-lock.json'), **sources}, 'fault_injected': False}
    save_new(OUT, result)
    print(json.dumps({'status': result['status'], 'minimum': min(count),
        'p95_stable': result['all_evaluated_minutes_ratio_at_most_1_25']}))


if __name__ == '__main__': main()
