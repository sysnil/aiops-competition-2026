#!/usr/bin/env python3
"""Restore Worker3 checkout placement after a reversible client-scale test."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time


ROOT = Path('/root/cross-layer-rehearsal-phase4')


def call(*args, timeout=60):
    return subprocess.check_output(args, text=True, timeout=timeout)


def pods():
    items = json.loads(call('kubectl', '-n', 'otel-demo', 'get', 'pods', '-l',
                           'opentelemetry.io/name=checkout', '-o', 'json'))['items']
    return [item for item in items if not item['metadata'].get('deletionTimestamp')]


def main():
    output = ROOT / 'runtime/checkout-placement-restoration.json'
    if output.exists():
        raise ValueError('Restoration has already been attempted')
    before = json.loads(call('kubectl', '-n', 'otel-demo', 'get', 'deployment',
                             'checkout', '-o', 'json'))
    original = pods()
    if before['spec']['replicas'] != 1 or len(original) != 1:
        raise ValueError('Checkout is not at one stable replica')
    template_hash = hashlib.sha256(json.dumps(before['spec']['template'],
        sort_keys=True).encode()).hexdigest()
    result = {'at_utc': datetime.now(timezone.utc).isoformat(),
              'before_uid': original[0]['metadata']['uid'],
              'before_node': original[0]['spec']['nodeName'],
              'deployment_uid': before['metadata']['uid'],
              'template_sha256': template_hash, 'attempts': []}
    try:
        target = None
        for replica_count in (2, 3):
            call('kubectl', '-n', 'otel-demo', 'scale', 'deployment/checkout',
                 '--replicas=' + str(replica_count))
            call('kubectl', '-n', 'otel-demo', 'rollout', 'status',
                 'deployment/checkout', '--timeout=180s', timeout=195)
            current = pods()
            result['attempts'].append({'replicas': replica_count,
                'pods': [{'name': p['metadata']['name'], 'uid': p['metadata']['uid'],
                          'node': p['spec']['nodeName']} for p in current]})
            matches = [p for p in current if p['spec']['nodeName'] == 'k8s-worker3'
                       and p['status']['phase'] == 'Running']
            if matches:
                target = matches[0]
                break
        if not target:
            raise ValueError('No new checkout Pod scheduled on Worker3')
        for pod in current:
            cost = '1000' if pod['metadata']['uid'] == target['metadata']['uid'] else '-1000'
            call('kubectl', '-n', 'otel-demo', 'annotate', 'pod', pod['metadata']['name'],
                 'controller.kubernetes.io/pod-deletion-cost=' + cost, '--overwrite')
        result['preferred_uid'] = target['metadata']['uid']
    finally:
        call('kubectl', '-n', 'otel-demo', 'scale', 'deployment/checkout', '--replicas=1')
        call('kubectl', '-n', 'otel-demo', 'rollout', 'status',
             'deployment/checkout', '--timeout=180s', timeout=195)
        for _ in range(60):
            current = pods()
            if len(current) == 1 and current[0]['status']['phase'] == 'Running':
                break
            time.sleep(2)
        after = json.loads(call('kubectl', '-n', 'otel-demo', 'get', 'deployment',
                                'checkout', '-o', 'json'))
        result.update({'after_replicas': after['spec']['replicas'],
            'after_ready_replicas': after['status'].get('readyReplicas'),
            'after_template_sha256': hashlib.sha256(json.dumps(after['spec']['template'],
                sort_keys=True).encode()).hexdigest(),
            'after_pods': [{'name': p['metadata']['name'], 'uid': p['metadata']['uid'],
                            'node': p['spec']['nodeName']} for p in current],
            'status': 'RESTORED_WORKER3' if len(current) == 1 and
                current[0]['spec']['nodeName'] == 'k8s-worker3' and
                after['spec']['replicas'] == 1 and after['status'].get('readyReplicas') == 1 and
                after['metadata']['uid'] == before['metadata']['uid'] and
                template_hash == hashlib.sha256(json.dumps(after['spec']['template'],
                    sort_keys=True).encode()).hexdigest() else 'PLACEMENT_NOT_RESTORED'})
        if len(current) == 1:
            subprocess.run(['kubectl', '-n', 'otel-demo', 'annotate', 'pod',
                current[0]['metadata']['name'], 'controller.kubernetes.io/pod-deletion-cost-'],
                check=False, capture_output=True, text=True, timeout=30)
        output.write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps({'status': result['status'], 'after_pods': result['after_pods']}))
        if result['status'] != 'RESTORED_WORKER3':
            raise RuntimeError('Checkout Worker3 placement not restored')


if __name__ == '__main__':
    main()
