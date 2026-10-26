#!/usr/bin/env python3
"""Idempotent rollback of the temporary Checkout client experiment."""

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import subprocess
import time

from checkout_identity_rollout import change, current_deployment, env_of


ROOT = Path('/root/cross-layer-rehearsal-phase4')
UNIT = 'p4-checkout-rollback'


def run(*args, timeout=240):
    return subprocess.check_output(args, text=True, timeout=timeout)


def ready_pods():
    items = json.loads(run('kubectl', '-n', 'otel-demo', 'get', 'pods', '-l',
                           'opentelemetry.io/name=checkout', '-o', 'json'))['items']
    return [item for item in items if not item['metadata'].get('deletionTimestamp') and
            item['status']['phase'] == 'Running' and all(status.get('ready') for status in
                                                     item['status'].get('containerStatuses', []))]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--watchdog', action='store_true')
    args = parser.parse_args()
    lock = ROOT / 'runtime/payment-cleanup.lock'
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        result = {'mode': 'WATCHDOG' if args.watchdog else 'MANUAL',
                  'started_at_utc': datetime.now(timezone.utc).isoformat(),
                  'actions': [], 'status': 'CLEANUP_INCOMPLETE'}
        try:
            plan_path = ROOT / 'design/checkout-identity-plan.json'
            backup_path = ROOT / 'backups/deployment-checkout.json'
            if not plan_path.exists() or not backup_path.exists():
                raise ValueError('Checkout backup or identity plan is missing')
            plan = json.loads(plan_path.read_text())['services']['checkout']
            backup = json.loads(backup_path.read_text())
            live = current_deployment('checkout')
            if live['metadata']['uid'] != backup['metadata']['uid']:
                raise ValueError('Deployment UID changed outside the experiment')
            if env_of(live) == plan['new_env']:
                if live['spec']['replicas'] != 4:
                    raise ValueError('Cannot roll back identity with unexpected replicas')
                rollback = ROOT / 'runtime/checkout-identity-rollback.json'
                if rollback.exists():
                    raise ValueError('Rollback record exists while new env remains live')
                change('checkout', 'rollback', plan_path, rollback)
                result['actions'].append('RESTORED_ORIGINAL_OTEL_ENV')
            elif env_of(live) != plan['original_env']:
                raise ValueError('Checkout environment differs from both registered versions')
            run('kubectl', '-n', 'otel-demo', 'scale', 'deployment/checkout', '--replicas=1')
            run('kubectl', '-n', 'otel-demo', 'rollout', 'status', 'deployment/checkout',
                '--timeout=180s', timeout=195)
            for _ in range(60):
                pods = ready_pods()
                if len(pods) == 1:
                    break
                time.sleep(2)
            else:
                raise ValueError('Checkout did not converge to one Ready Pod')
            result['actions'].append('SCALED_CHECKOUT_TO_ONE')
            if pods[0]['spec']['nodeName'] != 'k8s-worker3':
                run('python3', str(ROOT / 'scripts/restore_checkout_placement.py'), timeout=300)
                pods = ready_pods()
                result['actions'].append('RESTORED_WORKER3_PLACEMENT')
            live = current_deployment('checkout')
            if (len(pods) != 1 or pods[0]['spec']['nodeName'] != 'k8s-worker3' or
                    live['spec']['replicas'] != 1 or live['status'].get('readyReplicas') != 1 or
                    env_of(live) != plan['original_env'] or
                    live['spec']['template'] != backup['spec']['template']):
                raise ValueError('Original Checkout Deployment template or placement not restored')
            result['status'] = 'CHECKOUT_ROLLBACK_PASS'
            result['checkout_pod_uid'] = pods[0]['metadata']['uid']
        except Exception as exc:
            result['error'] = str(exc)
        finally:
            subprocess.run(['systemctl', 'stop', UNIT + '.timer'], check=False,
                           capture_output=True, timeout=15)
            result['completed_at_utc'] = datetime.now(timezone.utc).isoformat()
            output = ROOT / 'runtime' / ('payment-cleanup-' +
                result['completed_at_utc'].replace(':', '-').replace('+', '_') + '.json')
            output.write_text(json.dumps(result, indent=2) + '\n')
            print(json.dumps({'status': result['status'], 'actions': result['actions'],
                              'record': str(output)}), flush=True)
            if result['status'] != 'CHECKOUT_ROLLBACK_PASS':
                raise RuntimeError('Checkout rollback incomplete: ' + result.get('error', 'unknown'))


if __name__ == '__main__':
    main()
