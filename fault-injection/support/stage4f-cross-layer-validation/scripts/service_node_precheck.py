#!/usr/bin/env python3
"""Public pre-fault Service and Node case admission prerequisites."""

import argparse
import hashlib
import json
from pathlib import Path


MIN_SAMPLES = 20


def service(snapshot):
    if snapshot.get('schema_version') != 1 or snapshot.get('public_only') is not True:
        raise ValueError('Expected public pre-fault service snapshot')
    target = snapshot['target_service']
    if target == 'product-catalog':
        raise ValueError('F01 must use a different service from Stage4E E01')
    if f'SERVICE/{target}' not in snapshot['candidate_entities']:
        raise ValueError('Target service absent from public candidates')
    if snapshot.get('rollout_pending'):
        raise ValueError('Service placement is not stable')
    before = snapshot['before_window']
    if before['end_unix_ns'] - before['start_unix_ns'] < 600 * 1_000_000_000:
        raise ValueError('Service clean baseline shorter than 10 minutes')
    replicas = [item for item in snapshot['pods'] if item['service'] == target and item.get('ready') is True]
    uids = {item['uid'] for item in replicas}
    if len(uids) < 2 or len(uids) != len(replicas):
        raise ValueError('Service root requires at least two distinct stable ready Pods')
    if any(item.get('stable_through_before') is not True for item in replicas):
        raise ValueError('Service Pod changed during clean baseline')
    observed = snapshot['pod_metric_sample_counts']
    if any(observed.get(uid, {}).get('cpu_usage', 0) < MIN_SAMPLES for uid in uids):
        raise ValueError('Service replica CPU evidence insufficient')
    return {'status': 'SERVICE_CANDIDATE_PRECHECK_PASS', 'candidate': f'SERVICE/{target}',
            'replica_uids': sorted(uids), 'replica_count': len(uids)}


def node(snapshot):
    if snapshot.get('schema_version') != 1 or snapshot.get('public_only') is not True:
        raise ValueError('Expected public pre-fault node snapshot')
    target = snapshot['target_node']
    if target == 'k8s-worker3':
        raise ValueError('F03 must use a different node from Stage4E E03')
    if f'NODE/{target}' not in snapshot['candidate_entities']:
        raise ValueError('Target node absent from public candidates')
    if snapshot.get('node_ready_before') is not True or snapshot.get('rollout_pending'):
        raise ValueError('Target node or Pod placement is unstable')
    before = snapshot['before_window']
    if before['end_unix_ns'] - before['start_unix_ns'] < 600 * 1_000_000_000:
        raise ValueError('Node clean baseline shorter than 10 minutes')
    pods = [item for item in snapshot['pods'] if item['node'] == target and item.get('ready') is True
            and item.get('business_service') is True]
    if len(pods) < 2 or len({item['service'] for item in pods}) < 2:
        raise ValueError('Node requires Ready Pods from at least two business services')
    metrics = snapshot['node_metric_sample_counts']
    for family in ('cpu_usage', 'cpu_time', 'memory_available'):
        if metrics.get(family, 0) < MIN_SAMPLES:
            raise ValueError('Node baseline metric insufficient: ' + family)
    return {'status': 'NODE_CANDIDATE_PRECHECK_PASS', 'candidate': f'NODE/{target}',
            'business_pod_count': len(pods), 'business_service_count': len({item['service'] for item in pods})}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('layer', choices=('service', 'node'))
    parser.add_argument('--public-snapshot', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('Refusing to overwrite service/node precheck')
    snapshot = json.loads(args.public_snapshot.read_text())
    result = (service if args.layer == 'service' else node)(snapshot)
    result['input_sha256'] = hashlib.sha256(args.public_snapshot.read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(result['status'])


if __name__ == '__main__':
    main()
