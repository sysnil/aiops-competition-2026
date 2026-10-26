"""Freeze a private, UID-bound rehearsal preflight before any bounded fault."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

import yaml


BUSINESS = {'ad', 'frontend', 'recommendation', 'payment', 'checkout',
            'cart', 'shipping', 'product-catalog', 'currency'}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def current_pods():
    raw = subprocess.check_output(['kubectl', '-n', 'otel-demo', 'get', 'pods',
                                   '-o', 'json'], text=True, timeout=30)
    by_name = {}
    for item in json.loads(raw)['items']:
        statuses = item['status'].get('containerStatuses', [])
        if item['metadata'].get('deletionTimestamp') or not statuses:
            continue
        name = item['metadata']['name']
        by_name[name] = {
            'name': name, 'uid': item['metadata']['uid'],
            'service': item['metadata']['labels'].get('opentelemetry.io/name'),
            'node': item['spec']['nodeName'], 'ip': item['status'].get('podIP'),
            'container_id': statuses[0].get('containerID'),
            'ready': all(row.get('ready') for row in statuses),
            'restarts': sum(row.get('restartCount', 0) for row in statuses),
        }
    return by_name, hashlib.sha256(raw.encode()).hexdigest()


def exact_pod(expected, actual):
    if (not actual or not actual['ready'] or actual['restarts'] != 0 or
            any(actual.get(key) != expected.get(key) for key in
                ('name', 'uid', 'service', 'node', 'container_id'))):
        raise ValueError('Registered Pod UID/container/placement changed: ' +
                         expected['name'])
    return actual


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--registration-manifest', required=True, type=Path)
    parser.add_argument('--health', required=True, type=Path)
    parser.add_argument('--route-proof', type=Path)
    parser.add_argument('--experiment-id', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refusing overwrite of frozen preflight')
    plan = yaml.safe_load(args.plan.read_text())
    registration = json.loads(args.registration_manifest.read_text())
    health = json.loads(args.health.read_text())
    age = (datetime.now(timezone.utc) -
           datetime.fromisoformat(health['at_utc'])).total_seconds()
    if (registration.get('status') != 'QUALIFICATION_REGISTRATION_FROZEN_PRE_FAULT' or
            registration.get('plan_sha256') != sha(args.plan) or
            health.get('status') != 'STAGE5_BASE_HEALTH_PASS' or
            not 0 <= age <= 180):
        raise ValueError('Registration or health gate is stale or changed')
    rows = [row for row in plan['experiments'] if row['id'] == args.experiment_id]
    if len(rows) != 1:
        raise ValueError('Experiment is not uniquely registered')
    spec = rows[0]
    pods, pod_hash = current_pods()
    hosts = []
    for expected in spec.get('target_pods', []):
        actual = exact_pod(expected, pods.get(expected['name']))
        hosts.append({'node': actual['node'], 'pod_name': actual['name'],
                      'pod_uid': actual['uid'],
                      'container_id': actual['container_id']})
    for expected in spec.get('control_pods', []):
        exact_pod(expected, pods.get(expected['name']))
    layer = spec['layer']
    if layer == 'SERVICE' and len(hosts) != 2:
        raise ValueError('Service-wide fault needs two current registered replicas')
    if layer == 'POD' and len(hosts) != 1:
        raise ValueError('Pod fault needs one current registered target')
    if layer == 'NODE':
        node = spec['target_entity'].split('/', 1)[1]
        registered_business = spec.get('node_business_pods', [])
        if len(registered_business) < 2:
            raise ValueError('Node fault lacks two preregistered business Pods')
        for expected in registered_business:
            actual = exact_pod(expected, pods.get(expected['name']))
            if actual['node'] != node or actual['service'] not in BUSINESS:
                raise ValueError('Node business Pod changed placement or role')
        active = [pod for pod in pods.values() if pod['node'] == node and
                  pod['service'] in BUSINESS and pod['ready']]
        if len(active) < 2:
            raise ValueError('Node lacks multiple active business Pods')
        hosts = [{'node': node}]
    common = {
        'case_id': args.experiment_id, 'root_layer': layer,
        'family': spec['family'], 'target_entity': spec['target_entity'],
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'design_freeze_sha256': sha(args.registration_manifest),
        'registration_plan_sha256': sha(args.plan),
        'health_sha256': sha(args.health), 'pod_snapshot_sha256': pod_hash,
        'fault_hosts': hosts, 'faults_injected': 0, 'gt_read': False,
        'formal_case_created': False,
    }
    if layer == 'NETWORK_PATH':
        if not args.route_proof or not args.route_proof.is_file():
            raise ValueError('Network attempt lacks fresh route and packet proof')
        proof = json.loads(args.route_proof.read_text())
        freshness = (datetime.now(timezone.utc) -
                     datetime.fromisoformat(proof['at_utc'])).total_seconds()
        proof_rows = {row['pair']: row for row in proof['rows']}
        expected_pairs = ('checkout_cart_path', 'image_control',
                          'product_control_worker2', 'product_control_worker1')
        if (proof.get('status') != 'ROUTE_FLOW_PROOF_PASS' or
                not 0 <= freshness <= 180 or
                any(proof_rows.get(name, {}).get('status') != 'VERIFIED'
                    for name in expected_pairs)):
            raise ValueError('Current business target or controls are not proven')
        target = proof_rows['checkout_cart_path']
        for role in ('source_pod', 'destination_pod'):
            expected = spec['network_' + role]
            observed = target[role]
            if any(observed.get(key) != expected.get(key) for key in
                   ('name', 'uid', 'node', 'ip')):
                raise ValueError('Network target UID/placement changed')
        for name in expected_pairs[1:]:
            row = proof_rows[name]
            if row['source_pod']['node'] == 'k8s-worker3' and (
                    row['destination_pod']['node'] == 'k8s-worker2'):
                raise ValueError('Registered normal control traverses target path')
        common.update({
            'status': 'QUALIFICATION_NETWORK_PREFLIGHT_FROZEN',
            'experiment_id': args.experiment_id,
            'plan_sha256': sha(args.plan),
            'path': spec['target_entity'],
            'source_node': 'k8s-worker3', 'destination_node': 'k8s-worker2',
            'source_node_ip': '192.168.53.55',
            'destination_node_ip': '192.168.53.54',
            'business_route_prefix': target['route_prefix'],
            'verified_traversing_edges': [{
                'edge': target['edge'],
                'source_pod_uid': target['source_pod']['uid'],
                'destination_pod_uid': target['destination_pod']['uid'],
                'destination_pod_ip': target['destination_pod']['ip'],
                'packet_output_sha256': target['packet_output_sha256']}],
            'registered_control_proof_sha256': sha(args.route_proof),
        })
    else:
        common['status'] = 'QUALIFICATION_PREFLIGHT_FROZEN_PRE_FAULT'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(common, stream, indent=2)
        stream.write('\n')
    args.output.chmod(0o444)
    print(json.dumps({'status': common['status'],
                      'experiment_id': args.experiment_id,
                      'preflight_sha256': sha(args.output)}))


if __name__ == '__main__':
    main()
