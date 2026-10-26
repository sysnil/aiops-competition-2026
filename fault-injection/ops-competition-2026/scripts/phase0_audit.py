#!/usr/bin/env python3
"""Read-only SF-PILOT-01 qualification review; authenticated SSH sockets only."""

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess


HOSTS = {
    'master': '192.168.53.52', 'w1': '192.168.53.53',
    'w2': '192.168.53.54', 'w3': '192.168.53.55',
    'router': '192.168.53.50', 'server': '192.168.51.51',
    'client': '192.168.51.50',
}
SOURCES = [
    '/root/stage5-v3-final-fast-ready/results/final-decision.json',
    '/root/stage5-v3-final-fast-ready/results/mechanism-readiness.json',
    '/root/stage5-v3-final-fast-ready/results/final-global-safety.json',
    '/root/stage5-v3-final-fast-ready/results/final-five-host-residue.json',
    '/root/stage5-v3-final-fast-ready/results/collector-reliability.json',
    '/root/stage5-v3-final-fast-ready/results/clean-validation.json',
    '/root/stage5-v3-final-fast-ready/results/recommendation-cgroup-current.json',
    '/root/stage5-v3-final-fast-ready/reports/four-layer-readiness.md',
    '/root/stage5-v3-final-fast-ready/reports/final-readiness-report.md',
    '/root/stage5-v3-mechanism-qualification/results/qualification-summary.json',
    '/root/stage5-v3-mechanism-qualification/reports/qualification-phase1.md',
    '/root/stage5-v3-autonomous-preparation/reports/autonomous-preparation-summary.md',
    '/root/stage5-v3-cross-layer-rca/design/fault-catalog-draft.yaml',
]


def now():
    return datetime.now(timezone.utc).isoformat()


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('xb') as stream:
        stream.write(data)
    path.chmod(0o600)


def remote(name, argv, timeout=30):
    return subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o',
        'ConnectTimeout=6', '-S', '/tmp/sfpilot-' + name + '.sock',
        'root@' + HOSTS[name], shlex.join(argv)],
        capture_output=True, text=True, timeout=timeout)


def fetch(path):
    code = ('import base64,hashlib,json,pathlib; p=pathlib.Path(' + repr(path) + '); '
        'b=p.read_bytes(); print(json.dumps({"path":str(p),"sha256":hashlib.sha256(b).hexdigest(),'
        '"mtime_ns":p.stat().st_mtime_ns,"payload":base64.b64encode(b).decode()}))')
    result = remote('master', ['python3', '-c', code])
    if result.returncode:
        return {'path': path, 'status': 'READ_FAILED', 'exit_code': result.returncode}
    row = json.loads(result.stdout)
    data = base64.b64decode(row.pop('payload'))
    if hashlib.sha256(data).hexdigest() != row['sha256']:
        raise RuntimeError('Source transfer SHA mismatch: ' + path)
    row['bytes'] = len(data)
    row['status'] = 'READ_AND_SHA_VERIFIED'
    return row, data


def host_check(item):
    name, address = item
    row = {'role': name, 'address': address, 'at_utc': now()}
    try:
        with socket.create_connection((address, 22), timeout=6):
            row['ssh_tcp'] = 'REACHABLE'
    except OSError as error:
        return {**row, 'ssh_tcp': 'UNREACHABLE', 'access': 'NOT_VERIFIED',
                'reason': type(error).__name__}
    result = remote(name, ['hostname'])
    row['ssh_exit_code'] = result.returncode
    if result.returncode:
        row['access'] = 'AUTHENTICATION_BLOCKED' if 'Permission denied' in result.stderr else 'SSH_FAILED'
        row['project_service_sdk_checks'] = 'NOT_VERIFIED'
    else:
        row['access'] = 'AUTHENTICATED_READ_ONLY'
        row['hostname'] = result.stdout.strip()
    return row


TOPOLOGY_CODE = r'''
import json,subprocess,datetime
k=['/usr/bin/kubectl','--kubeconfig=/etc/kubernetes/admin.conf']
def query(args):return json.loads(subprocess.check_output(k+args+['-o','json'],text=True,timeout=30))
nodes=query(['get','nodes'])['items']
pods=query(['-n','otel-demo','get','pods'])['items']
slices=query(['-n','otel-demo','get','endpointslices'])['items']
services={'recommendation','frontend','ad','payment','checkout','cart','otel-collector','otel-cluster-collector','jaeger','opensearch','prometheus'}
out={'at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
 'scope':'READ_ONLY_IDENTITY_SNAPSHOT_NOT_GLOBAL_SAFETY_GATE',
 'nodes':[{'name':n['metadata']['name'],'conditions':{c['type']:c['status'] for c in n['status']['conditions']}} for n in nodes],
 'pods':[], 'endpoint_slices':[]}
for p in pods:
 s=p['metadata']['labels'].get('opentelemetry.io/name')
 if s not in services and not p['metadata']['name'].startswith(('otel-collector-','otel-cluster-collector-','jaeger-','opensearch-','prometheus-')):continue
 out['pods'].append({'service':s,'name':p['metadata']['name'],'uid':p['metadata']['uid'],
  'node':p['spec'].get('nodeName'),'ip':p['status'].get('podIP'),'phase':p['status']['phase'],
  'containers':[{'name':c['name'],'container_id':c.get('containerID'),'ready':c.get('ready'),'restarts':c.get('restartCount')} for c in p['status'].get('containerStatuses',[])]})
for s in slices:
 name=s['metadata']['labels'].get('kubernetes.io/service-name')
 if name not in services:continue
 out['endpoint_slices'].append({'service':name,'uid':s['metadata']['uid'],'ports':s.get('ports',[]),
  'endpoints':[{'addresses':e.get('addresses'),'pod_uid':e.get('targetRef',{}).get('uid'),
  'node':e.get('nodeName'),'conditions':e.get('conditions')} for e in s.get('endpoints',[])]})
print(json.dumps(out))
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError('Immutable review output already exists')
    args.output.mkdir(parents=True, mode=0o700)
    audit = {'review_status': 'PHASE0_EVIDENCE_COLLECTED_NOT_AUTHORIZATION',
        'internal_id': 'SF-PILOT-01', 'reserved_public_id': 'SF-001',
        'started_utc': now(), 'sources': [], 'checks': {},
        'faults_injected': 0, 'public_case_created': False,
        'research_assets_modified': False, 'test_gt_read': False}
    with ThreadPoolExecutor(max_workers=7) as pool:
        audit['hosts'] = list(pool.map(host_check, HOSTS.items()))
    with ThreadPoolExecutor(max_workers=4) as pool:
        for index, item in enumerate(pool.map(fetch, SOURCES)):
            if isinstance(item, dict):
                audit['sources'].append(item)
                continue
            row, data = item
            filename = 'source-' + str(index + 1).zfill(2) + '-' + Path(row['path']).name
            save(args.output / 'evidence' / filename, data)
            audit['sources'].append({**row, 'evidence_file': 'evidence/' + filename})
    checks = {
        'current_public_topology': ['python3', '-c', TOPOLOGY_CODE],
        'data_mount': ['findmnt', '-n', '-o', 'SOURCE,FSTYPE,UUID,OPTIONS', '-T', '/data/stage5-v3'],
        'disk_budget': ['df', '-B1', '/', '/data/stage5-v3'],
        'active_rehearsal_units': ['systemctl', 'list-units', 's5v3*', '--state=running', '--no-pager', '--plain'],
    }
    for name, argv in checks.items():
        result = remote('master', argv, timeout=55)
        payload = {'at_utc': now(), 'argv': argv, 'exit_code': result.returncode,
                   'stdout': result.stdout, 'stderr': result.stderr}
        filename = 'evidence/' + name + '.json'
        data = (json.dumps(payload, indent=2) + '\n').encode()
        save(args.output / filename, data)
        audit['checks'][name] = {'exit_code': result.returncode,
            'evidence_file': filename, 'sha256': hashlib.sha256(data).hexdigest()}
    audit['completed_utc'] = now()
    save(args.output / 'phase0-evidence.json', (json.dumps(audit, indent=2) + '\n').encode())
    print(json.dumps({'status': audit['review_status'], 'sources': len(audit['sources']),
        'hosts': {r['role']:r['access'] for r in audit['hosts']}}))


if __name__ == '__main__': main()
