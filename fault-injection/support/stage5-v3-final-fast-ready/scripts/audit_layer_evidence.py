#!/usr/bin/env python3
"""Read-only four-layer engineering evidence from the preserved no-fault run."""

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path

from clean_protocol import save_new, sha


def analyze(run, topology, cgroup, worker1, route_pass, route_fail):
    schedule = json.loads((run / 'schedule.json').read_text())
    start = datetime.fromisoformat(schedule['scheduled_start_utc']).timestamp()
    topo, cg, w1, passed, failed = [json.loads(path.read_text()) for path in
        (topology, cgroup, worker1, route_pass, route_fail)]
    rec_uids = set(schedule['pod_uids']['recommendation'])
    current_uids = {row['uid'] for row in topo['service_pods']['recommendation']
                    if row['ready']}
    counts = {uid: [0] * 30 for uid in rec_uids}
    missing_uid = 0
    for page in sorted((run / 'timed-traces/first_plus5').glob('page-*.json.gz')):
        with gzip.open(page, 'rt', encoding='utf-8') as source:
            hits = json.load(source)['hits']['hits']
        for item in hits:
            span = item['_source']
            if span['process']['serviceName'] != 'recommendation' or (
                    span.get('tag') or {}).get('span@kind') != 'server':
                continue
            minute = int((span['startTime'] / 1_000_000 - start) // 60)
            if not 0 <= minute < 30:
                continue
            uid = {row.get('key'): row.get('value') for row in
                   span['process'].get('tags') or []}.get('k8s.pod.uid')
            if uid in counts: counts[uid][minute] += 1
            else: missing_uid += 1
    two_replica_coverage = (rec_uids == current_uids and len(rec_uids) == 2 and
                            all(min(values) >= 30 for values in counts.values()))
    return {'status': 'FOUR_LAYER_NO_FAULT_EVIDENCE_AUDITED',
        'at_utc': datetime.now(timezone.utc).isoformat(),
        'SERVICE': {'target': 'recommendation',
            'two_replica_ordinary_business_30_per_min': two_replica_coverage,
            'per_uid_server_spans_per_minute': counts,
            'uid_missing_or_unmatched': missing_uid,
            'status': 'SERVICE_CONDITIONAL'},
        'POD': {'target': 'recommendation single Pod memory pressure',
            'same_uid_current': rec_uids == current_uids,
            'bounded_cgroup_headroom_bytes': [row['bounded_memory_headroom_bytes']
                for row in cg['pods']],
            'cgroup_status': cg['status'],
            'status': 'POD_ENGINEERING_READY' if two_replica_coverage and cg[
                'status'] == 'RECOMMENDATION_CGROUP_PREFLIGHT_PASS' else
                'POD_CONDITIONAL'},
        'NODE': {'target': 'k8s-worker1 CPU contention',
            'active_business_pods': w1['ready_business_pods'],
            'cpu_psi_some_avg60': w1['cpu_psi']['some']['avg60'],
            'safety_contract': 'DRAFT_NOT_APPROVED',
            'status': 'NODE_PENDING_SAFETY_CONTRACT'},
        'NETWORK_PATH': {'target': 'NETWORK_PATH/k8s-worker2/k8s-worker3',
            'verified_during_state_machine_load': passed['status'] ==
                'ROUTE_FLOW_PROOF_PASS',
            'passive_baseline_business_packet_proof': failed['status'],
            'payment_w1_route_avoids_target': passed['payment_route_avoids_target'],
            'image_w2_route_avoids_target': passed['image_route_avoids_target'],
            'two_independent_controls_full_window': False,
            'status': 'NETWORK_CONTROL_BLOCKED'},
        'source_sha256': {str(path): sha(path) for path in
            (run / 'schedule.json', topology, cgroup, worker1, route_pass, route_fail)},
        'qualifying_fault_rehearsal_completed': False,
        'formal_qualification_authorized': False,
        'fault_injected': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--topology', type=Path, required=True)
    parser.add_argument('--cgroup', type=Path, required=True)
    parser.add_argument('--worker1', type=Path, required=True)
    parser.add_argument('--route-pass', type=Path, required=True)
    parser.add_argument('--route-fail', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.run, args.topology, args.cgroup, args.worker1,
                     args.route_pass, args.route_fail)
    save_new(args.output, result)
    print(json.dumps({'status': result['status'],
        'service': result['SERVICE']['status'], 'pod': result['POD']['status']}))


if __name__ == '__main__': main()
