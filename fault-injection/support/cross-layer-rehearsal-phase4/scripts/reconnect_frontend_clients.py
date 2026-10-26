#!/usr/bin/env python3
"""Reopen the two real Frontend->Checkout channels once, without retries."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time

import yaml


ROOT = Path('/root/cross-layer-rehearsal-phase4')


def call(*args, timeout=40):
    return subprocess.check_output(args, text=True, timeout=timeout)


def deployment():
    return json.loads(call('kubectl', '-n', 'otel-demo', 'get', 'deployment',
                           'frontend', '-o', 'json'))


def pods():
    items = json.loads(call('kubectl', '-n', 'otel-demo', 'get', 'pods', '-l',
                           'opentelemetry.io/name=frontend', '-o', 'json'))['items']
    return [item for item in items if not item['metadata'].get('deletionTimestamp')]


def ready(item):
    return (item['status']['phase'] == 'Running' and
            all(s.get('ready') for s in item['status'].get('containerStatuses', [])))


def wait_two(excluded_uid):
    for _ in range(90):
        current = pods()
        if len(current) == 2 and all(ready(p) for p in current) and (
                excluded_uid not in {p['metadata']['uid'] for p in current}):
            return current
        time.sleep(2)
    raise ValueError('Frontend failed to converge to two Ready replicas after replacement')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--health', type=Path, required=True)
    args = parser.parse_args()
    output = ROOT / 'runtime/frontend-client-reconnect.json'
    backup_dir = ROOT / 'backups'
    backup = backup_dir / 'deployment-frontend.json'
    if output.exists() or backup.exists():
        raise ValueError('Reconnect attempt is not new')
    policy_path = ROOT / 'design/payment-connection-policy.yaml'
    policy = yaml.safe_load(policy_path.read_text())
    health = json.loads(args.health.read_text())
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(health['at_utc'])).total_seconds()
    if (policy['frontend_reconnect']['maximum_replacements'] != 2 or
            health['status'] != 'STAGE5_BASE_HEALTH_PASS' or not 0 <= age <= 180):
        raise ValueError('Registered policy or fresh health missing')
    before = deployment()
    original = sorted(pods(), key=lambda p: p['metadata']['uid'])
    if (before['spec']['replicas'] != 2 or before['status'].get('readyReplicas') != 2 or
            len(original) != 2 or not all(ready(p) for p in original)):
        raise ValueError('Frontend does not have two stable Ready replicas')
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup.write_text(json.dumps(before, indent=2) + '\n')
    result = {'status': 'RECONNECT_INCOMPLETE',
              'started_at_utc': datetime.now(timezone.utc).isoformat(),
              'policy_sha256': hashlib.sha256(policy_path.read_bytes()).hexdigest(),
              'deployment_backup_sha256': hashlib.sha256(backup.read_bytes()).hexdigest(),
              'original_pods': [{'name': p['metadata']['name'],
                                 'uid': p['metadata']['uid'],
                                 'node': p['spec']['nodeName']} for p in original],
              'replacements': [], 'fault_injected': False,
              'direct_payment_pod_requests': 0}
    try:
        for old in original:
            current = {p['metadata']['uid'] for p in pods()}
            if old['metadata']['uid'] not in current:
                raise ValueError('Original Frontend Pod disappeared outside policy')
            call('kubectl', '-n', 'otel-demo', 'delete', 'pod',
                 old['metadata']['name'], '--wait=false')
            new = wait_two(old['metadata']['uid'])
            result['replacements'].append({'deleted_uid': old['metadata']['uid'],
                'deleted_name': old['metadata']['name'],
                'ready_pods_after': [{'name': p['metadata']['name'],
                                      'uid': p['metadata']['uid'],
                                      'node': p['spec']['nodeName']} for p in new]})
        after = deployment()
        final = pods()
        if (len(final) != 2 or not all(ready(p) for p in final) or
                after['spec']['template'] != before['spec']['template'] or
                after['spec']['replicas'] != 2 or after['metadata']['uid'] != before['metadata']['uid']):
            raise ValueError('Frontend template or two-replica health changed')
        result['status'] = 'FRONTEND_CHANNEL_RECONNECT_PASS'
    except Exception as exc:
        result['error'] = str(exc)
    finally:
        result['completed_at_utc'] = datetime.now(timezone.utc).isoformat()
        output.write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps({'status': result['status'],
                          'replacements': len(result['replacements'])}), flush=True)
        if result['status'] != 'FRONTEND_CHANNEL_RECONNECT_PASS':
            raise RuntimeError('Frontend reconnect incomplete: ' + result.get('error', 'unknown'))


if __name__ == '__main__':
    main()
