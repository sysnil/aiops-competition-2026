#!/usr/bin/env python3
"""Fresh, read-only mechanism scope inventory. Never invokes a fault primitive."""

from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import yaml


HERE = Path(__file__).resolve().parents[1]
OPS = HERE.parent
CAPABILITY = OPS / 'planning/SF-12-30min-20261015/evidence/fault-capability-matrix.json'
HOSTS = {
    'master': ('192.168.53.52', '/tmp/sfgen-master.sock'),
    'w1': ('192.168.53.53', '/tmp/sfgen-w1.sock'),
    'w3': ('192.168.53.55', '/tmp/sfgen-w3.sock'),
}
KUBE = ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '-n', 'otel-demo']


def remote(host: str, argv: list[str]) -> str:
    address, socket = HOSTS[host]
    result = subprocess.run(
        ['ssh', '-S', socket, '-o', 'BatchMode=yes', f'root@{address}', shlex.join(argv)],
        capture_output=True, text=True, check=True, timeout=35,
    )
    return result.stdout


def kubectl(*args: str) -> dict:
    return json.loads(remote('master', KUBE + ['get', *args, '-o', 'json']))


def find_named(value: object, name: str) -> list[dict]:
    found = []
    if isinstance(value, dict):
        if value.get('name') == name:
            found.append(value)
        for child in value.values():
            found.extend(find_named(child, name))
    elif isinstance(value, list):
        for child in value:
            found.extend(find_named(child, name))
    return found


def cgroup(host: str, pod: dict) -> dict:
    uid = pod['metadata']['uid']
    container = pod['status']['containerStatuses'][0]['containerID'].removeprefix('containerd://')
    code = '''import pathlib,json
p=pathlib.Path('/sys/fs/cgroup/kubepods.slice') / ('kubepods-pod'+%r.replace('-','_')+'.slice') / ('cri-containerd-'+%r+'.scope')
if not p.is_dir():raise RuntimeError('Target cgroup absent')
print(json.dumps({'cpu_max':(p/'cpu.max').read_text().strip(),'cpu_stat':(p/'cpu.stat').read_text(),
'memory_current_bytes':int((p/'memory.current').read_text()),'memory_max_bytes':int((p/'memory.max').read_text()),
'path':str(p)}))''' % (uid, container)
    return json.loads(remote(host, ['python3', '-c', code]))


def main() -> None:
    out = HERE / 'reports/fresh-mechanism-audit.json'
    if out.exists():
        raise RuntimeError('Do not overwrite an earlier scope audit')
    matrix = json.loads(CAPABILITY.read_text())
    nodes = json.loads(remote('master', KUBE[:2] + ['get', 'nodes', '-o', 'json']))['items']
    pods = kubectl('pods')['items']
    deploy = kubectl('deployment', 'frontend-proxy')
    cm = kubectl('configmap', 's5v3ap-frontend-proxy-idle-c2')
    envoy = yaml.safe_load(cm['data']['envoy.tmpl.yaml'])
    filters = find_named(envoy, 'envoy.filters.http.fault')
    if len(filters) != 1:
        raise RuntimeError('Proxy fault filter not unique')
    config = filters[0]['typed_config']
    ad = [p for p in pods if p['metadata'].get('labels', {}).get('opentelemetry.io/name') == 'ad']
    if len(ad) != 2:
        raise RuntimeError('Ad replica universe changed')
    placements = {}
    for pod in pods:
        node = pod['spec'].get('nodeName')
        service = pod['metadata'].get('labels', {}).get('opentelemetry.io/name')
        if node and service:
            placements.setdefault(node, set()).add(service)
    ad_evidence = []
    for pod in ad:
        node = pod['spec']['nodeName']
        host = {'k8s-worker1': 'w1', 'k8s-worker3': 'w3'}.get(node)
        if host is None:
            raise RuntimeError('New Ad placement requires a fresh worker mapping')
        ad_evidence.append({
            'pod_name': pod['metadata']['name'], 'pod_uid': pod['metadata']['uid'],
            'node': node, 'container_id': pod['status']['containerStatuses'][0]['containerID'],
            'restart_count': pod['status']['containerStatuses'][0]['restartCount'],
            'cgroup': cgroup(host, pod), 'cohost_services': sorted(placements[node]),
        })
    current = []
    for item in matrix:
        status = item['engineering_status']
        current.append({
            'mechanism': item['fault_type'], 'engineering_status': status,
            'actual_batch_executions': item['actual_executions'],
            'blockers': item['blockers'],
            'freshly_qualified_by_this_read_only_audit': False,
            'formal_execution_authorized': False,
        })
    result = {
        'status': 'FRESH_READ_ONLY_AUDIT_NO_NEW_MECHANISM_QUALIFIED',
        'at_utc': datetime.now(timezone.utc).isoformat(),
        'nodes_ready': [n['metadata']['name'] for n in nodes if any(
            c['type'] == 'Ready' and c['status'] == 'True' for c in n['status']['conditions'])],
        'placements': {key: sorted(value) for key, value in placements.items()},
        'pod_cpu_stress': {
            'candidate_ad_pods': ad_evidence,
            'current_target_exists': True,
            'old_worker2_target_valid': False,
            'run_owned_stress_process_and_exact_pid_cleanup': 'NOT_IMPLEMENTED',
            'independent_watchdog_proven': False,
            'protected_collector_storage_cohosts_excluded': False,
            'direct_public_cpu_cfs_signal_qualification': 'NOT_EXECUTED',
            'decision': 'SAFETY_BLOCKED',
        },
        'http_request_delay': {
            'proxy_deployment_uid': deploy['metadata']['uid'],
            'proxy_ready_replicas': deploy['status'].get('readyReplicas', 0),
            'configmap_uid': cm['metadata']['uid'],
            'envoy_template_sha256': hashlib.sha256(cm['data']['envoy.tmpl.yaml'].encode()).hexdigest(),
            'filter': config,
            'header_delay_present': 'header_delay' in config.get('delay', {}),
            'routes_include_telemetry_and_controls': True,
            'run_scoped_route_allowlist': 'NOT_IMPLEMENTED',
            'max_delay_contract': 'NOT_FROZEN',
            'independent_rollback_proven': False,
            'decision': 'SAFETY_BLOCKED',
        },
        'mechanisms': current,
        'private_gt_or_test_access': False,
        'faults_injected': 0,
    }
    out.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'status': result['status'], 'mechanisms': len(current),
                      'ad_placements': [x['node'] for x in ad_evidence],
                      'http_header_delay_present': result['http_request_delay']['header_delay_present']}))


if __name__ == '__main__':
    main()
