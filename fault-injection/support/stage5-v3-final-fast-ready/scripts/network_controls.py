#!/usr/bin/env python3
"""Read-only, fresh W2->W3 route and ordinary business packet proof."""

import argparse
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import subprocess


HOSTS = {
    'master': ('192.168.53.52', '/tmp/s5v3-final-master.sock'),
    'k8s-worker1': ('192.168.53.53', '/tmp/s5v3-final-w1.sock'),
    'k8s-worker2': ('192.168.53.54', '/tmp/s5v3-final-w2.sock'),
    'k8s-worker3': ('192.168.53.55', '/tmp/s5v3-final-w3.sock')}
KUBECTL = ['/usr/bin/kubectl', '--kubeconfig=/etc/kubernetes/admin.conf',
           '--context=kubernetes-admin@kubernetes']
CIDR = ipaddress.ip_network('10.244.100.192/26')


def remote(host, argv, timeout=35):
    ip, sock = HOSTS[host]
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes',
        '-o', 'ControlPath=' + sock, 'root@' + ip, *argv],
        capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(host + ' read failed: ' + result.stderr[-300:])
    return result.stdout


def kube(*args):
    return json.loads(remote('master', KUBECTL + list(args) + ['-o', 'json']))


def one(pods, service, node):
    rows = [p for p in pods if p['metadata']['labels'].get('opentelemetry.io/name') ==
        service and p['spec'].get('nodeName') == node and
        p['status'].get('phase') == 'Running' and
        p['status'].get('containerStatuses') and all(c.get('ready') for c in
        p['status']['containerStatuses'])]
    if len(rows) != 1:
        raise RuntimeError('Required Pod placement ambiguous: ' + service + '/' + node)
    p = rows[0]
    return {'service': service, 'name': p['metadata']['name'],
        'uid': p['metadata']['uid'], 'ip': p['status']['podIP'], 'node': node}


def endpoint(slices, pod):
    return any(row.get('targetRef', {}).get('uid') == pod['uid'] and
        pod['ip'] in row.get('addresses', []) and
        row.get('conditions', {}).get('ready') is True
        for item in slices if item['metadata']['labels'].get(
            'kubernetes.io/service-name') == pod['service']
        for row in item.get('endpoints', []))


def route(host, destination):
    rows = json.loads(remote(host, ['ip', '-j', 'route', 'get', destination]))
    if len(rows) != 1:
        raise RuntimeError('Ambiguous route')
    return rows[0]


def packets(host, source, destination):
    expression = 'host ' + source + ' and host ' + destination
    ip, sock = HOSTS[host]
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ControlPath=' + sock,
        'root@' + ip, 'timeout', '25', 'tcpdump', '-i', 'any', '-nn', '-tttt',
        '-c', '100', expression], capture_output=True, text=True, timeout=35)
    if result.returncode not in (0, 124):
        raise RuntimeError('Passive packet read failed on ' + host)
    rows = [line for line in result.stdout.splitlines() if source in line and
            destination in line]
    return {'packet_count': len(rows), 'packet_lines': rows,
            'capture_exit_code': result.returncode,
            'stderr_sha256': hashlib.sha256(result.stderr.encode()).hexdigest()}


def run():
    pods = kube('-n', 'otel-demo', 'get', 'pods')['items']
    slices = kube('-n', 'otel-demo', 'get', 'endpointslices')['items']
    identities = {name: one(pods, service, node) for name, service, node in (
        ('checkout_w2', 'checkout', 'k8s-worker2'),
        ('cart_w3', 'cart', 'k8s-worker3'),
        ('proxy_w2', 'frontend-proxy', 'k8s-worker2'),
        ('image_w2', 'image-provider', 'k8s-worker2'),
        ('payment_w1', 'payment', 'k8s-worker1'))}
    if not all(endpoint(slices, pod) for pod in identities.values()):
        raise RuntimeError('Public EndpointSlice identity mismatch')
    c, cart, proxy, image, pay = identities.values()
    routes = {'target': route('k8s-worker2', cart['ip']),
        'payment': route('k8s-worker2', pay['ip']),
        'image': route('k8s-worker2', image['ip'])}
    qdisc = json.loads(remote('k8s-worker2', ['tc', '-j', 'qdisc', 'show',
                                             'dev', 'ens160']))
    target_ok = (ipaddress.ip_address(cart['ip']) in CIDR and
        routes['target'].get('gateway') == '192.168.53.55' and
        routes['target'].get('dev') == 'ens160' and
        routes['target'].get('prefsrc') == '192.168.53.54')
    payment_ok = (ipaddress.ip_address(pay['ip']) not in CIDR and
        routes['payment'].get('gateway') == '192.168.53.53' and
        routes['payment'].get('dev') == 'ens160')
    image_ok = not routes['image'].get('gateway') and \
        routes['image'].get('dev') != 'ens160'
    qdisc_ok = not any(row.get('kind') in ('netem', 'tbf', 'prio', 'htb')
                       for row in qdisc)
    evidence = {
        'checkout_to_cart': {host: packets(host, c['ip'], cart['ip'])
                             for host in ('k8s-worker2', 'k8s-worker3')},
        'checkout_to_payment': {host: packets(host, c['ip'], pay['ip'])
                                for host in ('k8s-worker2', 'k8s-worker1')},
        'proxy_to_image': {'k8s-worker2': packets('k8s-worker2', proxy['ip'],
                                                image['ip'])}}
    packet_ok = all(row['packet_count'] > 0 for edge in evidence.values()
                    for row in edge.values())
    return {'status': 'ROUTE_FLOW_PROOF_PASS' if all((target_ok, payment_ok,
        image_ok, qdisc_ok, packet_ok)) else 'ROUTE_FLOW_PROOF_FAIL',
        'at_utc': datetime.now(timezone.utc).isoformat(),
        'target_path': 'NETWORK_PATH/k8s-worker2/k8s-worker3',
        'pod_identities': identities, 'endpoint_identity_pass': True,
        'routes': routes, 'worker2_qdisc': qdisc,
        'target_route_pass': target_ok, 'payment_route_avoids_target': payment_ok,
        'image_route_avoids_target': image_ok, 'qdisc_no_fault_residue': qdisc_ok,
        'ordinary_business_packet_proof': evidence,
        'packet_proof_pass': packet_ok,
        'tc_filter_scope_preview_not_executed': ['10.244.100.192/26',
                                                  '192.168.53.55/32'],
        'payment_worker2_replica_counted_as_control': False,
        'two_controls_qualified_by_full_window': False,
        'fault_injected': False, 'private_gt_read': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refusing route proof overwrite')
    value = run()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, indent=2) + '\n')
    print(json.dumps({'status': value['status'], 'packet_proof_pass':
        value['packet_proof_pass'], 'target_route_pass': value['target_route_pass']}))
    if value['status'] != 'ROUTE_FLOW_PROOF_PASS':
        raise SystemExit(1)


if __name__ == '__main__': main()
