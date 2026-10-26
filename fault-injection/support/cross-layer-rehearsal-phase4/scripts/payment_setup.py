#!/usr/bin/env python3
"""Back up and temporarily instrument four Checkout clients with rollback timer."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

from checkout_identity_rollout import change, current_deployment, env_of, plan


ROOT = Path('/root/cross-layer-rehearsal-phase4')
UNIT = 'p4-checkout-rollback'


def run(*args, timeout=240):
    return subprocess.check_output(args, text=True, timeout=timeout)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--health', type=Path, required=True)
    args = parser.parse_args()
    health = json.loads(args.health.read_text())
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(health['at_utc'])).total_seconds()
    if health['status'] != 'STAGE5_BASE_HEALTH_PASS' or not 0 <= age <= 180:
        raise ValueError('No fresh five-host health gate')
    output = ROOT / 'runtime/payment-setup.json'
    backup_dir = ROOT / 'backups'
    backup = backup_dir / 'deployment-checkout.json'
    if output.exists() or backup.exists():
        raise ValueError('Payment setup has already started')
    live = current_deployment('checkout')
    if live['spec']['replicas'] != 1 or live['status'].get('readyReplicas') != 1 or any(
            row['name'] in ('WG_POD_UID', 'WG_POD_NAME') for row in env_of(live)):
        raise ValueError('Original Checkout is not one clean uninstrumented replica')
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup.write_text(json.dumps(live, indent=2) + '\n')
    (backup_dir / 'payment-endpointslices.json').write_text(run(
        'kubectl', '-n', 'otel-demo', 'get', 'endpointslice', '-l',
        'kubernetes.io/service-name=payment', '-o', 'json'))
    result = {'status': 'SETUP_INCOMPLETE', 'started_at_utc':
              datetime.now(timezone.utc).isoformat(),
              'health_sha256': sha(args.health), 'backup_sha256': sha(backup),
              'original_replicas': 1, 'test_replicas': 4}
    try:
        run('kubectl', '-n', 'otel-demo', 'scale', 'deployment/checkout', '--replicas=4')
        run('kubectl', '-n', 'otel-demo', 'rollout', 'status', 'deployment/checkout',
            '--timeout=180s', timeout=195)
        plan_path = ROOT / 'design/checkout-identity-plan.json'
        plan(backup_dir, plan_path)
        change('checkout', 'apply', plan_path, ROOT / 'runtime/checkout-identity-apply.json')
        run('systemd-run', '--quiet', '--unit=' + UNIT, '--on-active=1800s',
            '/usr/bin/python3', str(ROOT / 'scripts/payment_cleanup.py'), '--watchdog')
        result['watchdog_unit'] = UNIT + '.timer'
        result['checkout_pods'] = [{'name': item['metadata']['name'],
            'uid': item['metadata']['uid'], 'ip': item['status']['podIP'],
            'node': item['spec']['nodeName']} for item in json.loads(run(
                'kubectl', '-n', 'otel-demo', 'get', 'pods', '-l',
                'opentelemetry.io/name=checkout', '-o', 'json'))['items']
            if not item['metadata'].get('deletionTimestamp')]
        result['status'] = 'PAYMENT_CLIENT_SETUP_PASS'
    except Exception as exc:
        result['error'] = str(exc)
        if (ROOT / 'design/checkout-identity-plan.json').exists():
            cleanup = subprocess.run(['python3', str(ROOT / 'scripts/payment_cleanup.py')],
                                     capture_output=True, text=True, timeout=360)
            result['automatic_cleanup_exit_code'] = cleanup.returncode
            result['automatic_cleanup_output'] = cleanup.stdout[-1000:] + cleanup.stderr[-1000:]
        else:
            subprocess.run(['kubectl', '-n', 'otel-demo', 'scale',
                'deployment/checkout', '--replicas=1'], check=False, timeout=30)
            placement = subprocess.run(['python3',
                str(ROOT / 'scripts/restore_checkout_placement.py')],
                capture_output=True, text=True, timeout=360)
            result['automatic_placement_exit_code'] = placement.returncode
            result['automatic_placement_output'] = (placement.stdout + placement.stderr)[-1000:]
    output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'status': result['status'], 'record': str(output)}), flush=True)
    if result['status'] != 'PAYMENT_CLIENT_SETUP_PASS':
        raise RuntimeError('Payment setup failed: ' + result.get('error', 'unknown'))


if __name__ == '__main__':
    main()
