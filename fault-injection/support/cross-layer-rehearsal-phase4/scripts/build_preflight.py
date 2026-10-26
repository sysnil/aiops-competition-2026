#!/usr/bin/env python3
"""Fail-closed preflight for registered Phase 4 network attempts."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

import yaml


NODES = {'k8s-worker1': '192.168.53.53', 'k8s-worker2': '192.168.53.54',
         'k8s-worker3': '192.168.53.55'}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fresh(path, seconds=180):
    value = json.loads(path.read_text())
    at = datetime.fromisoformat(value['at_utc'])
    if not 0 <= (datetime.now(timezone.utc) - at).total_seconds() <= seconds:
        raise ValueError('Preflight evidence is stale: ' + str(path))
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--adapters', type=Path, required=True)
    parser.add_argument('--health', type=Path, required=True)
    parser.add_argument('--route-proof', type=Path)
    parser.add_argument('--readiness', type=Path, required=True)
    parser.add_argument('--id', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refusing overwrite')
    plan = yaml.safe_load(args.plan.read_text())
    adapters = yaml.safe_load(args.adapters.read_text())
    if args.id not in {row['id'] for row in plan['experiments']} or not args.id.startswith('P4-W3W2'):
        raise ValueError('Attempt was not registered')
    readiness = json.loads(args.readiness.read_text())
    if (readiness.get('status') != 'PHASE4_NO_FAULT_READINESS_PASS' or
            readiness.get('plan_sha256') != sha(args.plan) or
            min(readiness.get('target_edge_spans_per_minute', [0])) <
            plan['network_path']['minimum_valid_target_edge_spans_per_60s'] or
            readiness.get('controls_qualified') is not True or
            readiness.get('probe_windows_complete') is not True or
            readiness.get('workload_health_pass') is not True):
        raise ValueError('Frozen no-fault workload/probe readiness is absent or failed')
    health = fresh(args.health)
    if health['status'] != 'STAGE5_BASE_HEALTH_PASS':
        raise ValueError('Base health failed')
    items = json.loads(subprocess.check_output(
        ['kubectl', '-n', 'otel-demo', 'get', 'pods', '-o', 'json'], text=True))['items']
    def pod(p):
        statuses = p['status'].get('containerStatuses', [])
        if (p['metadata'].get('deletionTimestamp') or len(statuses) != 1 or
                not statuses[0].get('ready') or not statuses[0].get('containerID')):
            raise ValueError('A target/control Pod is not stable')
        return {'service': p['metadata']['labels'].get('opentelemetry.io/name'),
                'name': p['metadata']['name'], 'uid': p['metadata']['uid'],
                'pod_ip': p['status']['podIP'], 'node': p['spec']['nodeName'],
                'container_id': statuses[0]['containerID']}
    common = {'created_at_utc': datetime.now(timezone.utc).isoformat(),
              'experiment_id': args.id, 'faults_injected': 0,
              'plan_sha256': sha(args.adapters), 'phase4_design_sha256': sha(args.plan),
              'no_fault_readiness_sha256': sha(args.readiness),
              'health_sha256': sha(args.health), 'formal_case_created': False}
    if not args.route_proof:
        raise ValueError('Network route proof required')
    proof = fresh(args.route_proof)
    target = [row for row in proof['rows'] if row['pair'] == 'checkout_cart_path'
              and row['status'] == 'VERIFIED']
    controls = [row for row in proof['rows'] if row['pair'] in
                ('image_control', 'cart_control') and row['status'] == 'VERIFIED']
    if proof['status'] != 'ROUTE_FLOW_PROOF_PASS' or len(target) != 1 or len(controls) != 3:
        raise ValueError('Path and two independent controls lack pre-fault proof')
    current = {p['metadata']['uid']: pod(p) for p in items
               if p['metadata']['uid'] in {r['source_pod']['uid'] for r in target+controls}
               | {r['destination_pod']['uid'] for r in target+controls}}
    for row in target + controls:
        for key in ('source_pod', 'destination_pod'):
            p = row[key]
            if p['uid'] not in current or current[p['uid']]['pod_ip'] != p['ip']:
                raise ValueError('Pod UID/IP changed after route proof')
    target_row = target[0]
    result = {**common, 'status': 'RH_NETWORK_PREFLIGHT_READY',
              'path': 'NETWORK_PATH/k8s-worker3/k8s-worker2',
              'source_node': 'k8s-worker3', 'destination_node': 'k8s-worker2',
              'source_node_ip': NODES['k8s-worker3'],
              'destination_node_ip': NODES['k8s-worker2'],
              'business_route_prefix': target_row['route_prefix'],
              'verified_traversing_edges': [{
                  'business_edge': target_row['edge'],
                  'source_pod_uid': target_row['source_pod']['uid'],
                  'source_pod_ip': target_row['source_pod']['ip'],
                  'destination_pod_uid': target_row['destination_pod']['uid'],
                  'destination_pod_ip': target_row['destination_pod']['ip'],
                  'flow_proof': target_row['packet']}],
              'qualified_local_controls': [{'edge': row['edge'],
                  'source_uid': row['source_pod']['uid'],
                  'destination_uid': row['destination_pod']['uid'],
                  'packet': row['packet']} for row in controls],
              'route_proof_sha256': sha(args.route_proof)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'status': result['status'], 'experiment_id': args.id}))


if __name__ == '__main__':
    main()
