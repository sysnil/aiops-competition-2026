"""Bind fresh Pod, route and packet-flow evidence to a rehearsal NetworkPath."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import subprocess

import yaml


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def observe_flow(source_node, source_ip, destination_ip, node_ip, socket_prefix):
    source_ip = str(ipaddress.ip_address(source_ip))
    destination_ip = str(ipaddress.ip_address(destination_ip))
    socket = socket_prefix + source_node.removeprefix('k8s-') + '-ssh'
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
        '-o', 'ControlPath=' + socket, 'root@' + node_ip, 'timeout', '35',
        'tcpdump', '-nn', '-l', '-i', 'ens160', '-c', '1', 'tcp', 'and',
        'src', 'host', source_ip, 'and', 'dst', 'host', destination_ip],
        capture_output=True, text=True, timeout=42)
    if result.returncode != 0 or source_ip not in result.stdout or (
            destination_ip not in result.stdout):
        raise ValueError('No fresh interface-bound business flow: ' +
                         source_ip + ' -> ' + destination_ip + ' / ' +
                         result.stderr[-500:])
    return {'captured_at_utc': datetime.now(timezone.utc).isoformat(),
            'interface': 'ens160', 'line': result.stdout.strip()[:1000],
            'observation_sha256': hashlib.sha256(result.stdout.encode()).hexdigest()}


def build(plan_path, route_plan_path, metadata_path, routes_path, health_path,
          experiment_id, socket_prefix):
    plan = yaml.safe_load(plan_path.read_text())
    route_plan = yaml.safe_load(route_plan_path.read_text())
    metadata = json.loads(metadata_path.read_text())
    routes = json.loads(routes_path.read_text())
    health = json.loads(health_path.read_text())
    rows = [row for row in plan['network_experiments'] if row['id'] == experiment_id]
    age = (datetime.now(timezone.utc) -
           datetime.fromisoformat(health['at_utc'])).total_seconds()
    if (len(rows) != 1 or health.get('status') != 'STAGE5_BASE_HEALTH_PASS' or
            not 0 <= age <= 180 or metadata.get('public_only') is not True or
            routes.get('public_only') is not True or
            routes['capture_manifest_sha256'] != metadata['capture_manifest_sha256']):
        raise ValueError('Network rehearsal inputs are stale or not same-source public evidence')
    spec = rows[0]
    path = spec['path']
    selected = [row for row in route_plan['paths'] if row['entity'] == path]
    observed = routes['paths'].get(path)
    if len(selected) != 1 or not observed or len(observed.get('path_latency_ms', [])) < 20:
        raise ValueError('Target path is absent from registered route plan')
    selected = selected[0]
    source, destination = selected['source_node'], selected['destination_node']
    if path != f'NETWORK_PATH/{source}/{destination}':
        raise ValueError('Path direction differs from registered endpoints')
    node_ips = metadata['node_internal_ips']
    pods = metadata['pods']
    prefix = ipaddress.ip_network(observed['business_route_prefix'], strict=True)
    route_texts = observed['routes']
    pairs = []
    for edge in selected['traversing']:
        matches = [p for p in pods if p['service'] == edge['source_service'] and
                   p['node'] == source]
        destinations = [p for p in pods if p['service'] == edge['destination_service'] and
                        p['node'] == destination]
        if len(matches) != 1 or len(destinations) != 1:
            raise ValueError('One traversing business edge has ambiguous Pod endpoints')
        src, dst = matches[0], destinations[0]
        route = route_texts.get(dst['pod_ip'], '')
        if (ipaddress.ip_address(dst['pod_ip']) not in prefix or
                'via ' + node_ips[destination] not in route or
                ' dev ens160 ' not in route):
            raise ValueError('Current public Pod route does not traverse target Node pair')
        pairs.append({'business_edge': edge['source_service'] + '->' + edge['destination_service'],
                      'source_pod': src['name'], 'source_pod_uid': src['uid'],
                      'source_pod_ip': src['pod_ip'], 'destination_pod': dst['name'],
                      'destination_pod_uid': dst['uid'],
                      'destination_pod_ip': dst['pod_ip'],
                      'route_text': route.strip()})
    with ThreadPoolExecutor(max_workers=len(pairs)) as pool:
        futures = [pool.submit(observe_flow, source, row['source_pod_ip'],
                               row['destination_pod_ip'], node_ips[source], socket_prefix)
                   for row in pairs]
        for row, future in zip(pairs, futures):
            row['flow_proof'] = future.result()
    return {'status': 'RH_NETWORK_PREFLIGHT_READY',
            'created_at_utc': datetime.now(timezone.utc).isoformat(),
            'experiment_id': experiment_id, 'source_case': spec['source_case'],
            'path': path, 'source_node': source, 'destination_node': destination,
            'source_node_ip': node_ips[source], 'destination_node_ip': node_ips[destination],
            'business_route_prefix': str(prefix),
            'verified_traversing_edges': pairs,
            'registered_control': selected['control'],
            'normal_route_rtt_ms': observed['path_latency_ms'],
            'plan_sha256': sha256(plan_path),
            'route_plan_sha256': sha256(route_plan_path),
            'metadata_sha256': sha256(metadata_path),
            'route_observations_sha256': sha256(routes_path),
            'health_sha256': sha256(health_path),
            'faults_injected': 0, 'formal_case_created': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--route-plan', required=True, type=Path)
    parser.add_argument('--metadata', required=True, type=Path)
    parser.add_argument('--routes', required=True, type=Path)
    parser.add_argument('--health', required=True, type=Path)
    parser.add_argument('--experiment-id', required=True)
    parser.add_argument('--socket-prefix', default='/tmp/rca-rehearsal-')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Network preflight output already exists')
    result = build(args.plan, args.route_plan, args.metadata, args.routes,
                   args.health, args.experiment_id, args.socket_prefix)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'status': result['status'], 'path': result['path'],
                      'verified_edges': len(result['verified_traversing_edges']),
                      'sha256': sha256(args.output)}))


if __name__ == '__main__':
    main()
