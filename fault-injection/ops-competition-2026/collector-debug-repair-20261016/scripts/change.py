#!/usr/bin/env python3
"""Guarded removal of non-storage Collector debug exporters."""

import argparse
import copy
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import yaml


ROOT = Path('/root/ops-competition-2026/collector-debug-repair-20261016')
ANNOTATION = 'competition-debug-repair'
TIMER = 'ops-collector-debug-repair-rollback.timer'
PIPELINES = ('metrics', 'logs', 'traces', 'profiles')


def run(*args, timeout=300):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f'{args[0]} failed: {result.stderr[-1000:]}')
    return result.stdout


def kube(*args):
    return run('kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '-n',
               'otel-demo', *args)


def obj(kind, name):
    return json.loads(kube('get', kind, name, '-o', 'json'))


def digest(data):
    return hashlib.sha256(data.encode()).hexdigest()


def write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write('\n')
    path.chmod(0o600)


def read(path):
    return json.loads(path.read_text())


def now():
    return datetime.now(timezone.utc).isoformat()


def prepare():
    cm = obj('configmap', 'otel-collector')
    dep = obj('deployment', 'otel-collector')
    pods = json.loads(kube('get', 'pods', '-o', 'json'))['items']
    gateway = [p for p in pods if p['metadata']['name'].startswith('otel-collector-')
               and p['status'].get('phase') == 'Running'
               and not p['metadata'].get('deletionTimestamp')]
    if len(gateway) != 1 or dep['status'].get('readyReplicas') != 1:
        raise RuntimeError('Collector is not uniquely Ready')
    relay = cm['data']['relay']
    if digest(relay) != '128ed286e8a959aacddc2ee6c00e2d6e8b0a3eb18ea43e5de730a20e3793af2d':
        raise RuntimeError('Collector config differs from diagnosed version')
    original = yaml.safe_load(relay)
    candidate = copy.deepcopy(original)
    if candidate['processors']['memory_limiter'] != {
            'check_interval': '5s', 'limit_percentage': 80,
            'spike_limit_percentage': 15}:
        raise RuntimeError('Memory limiter changed')
    for name in PIPELINES:
        exports = candidate['service']['pipelines'][name]['exporters']
        if exports.count('debug') != 1 or len(exports) < 2:
            raise RuntimeError(f'Unexpected {name} exporters')
        exports.remove('debug')
    if candidate['exporters'].pop('debug') != {}:
        raise RuntimeError('Unexpected debug exporter configuration')
    if any(original[k] != candidate[k] for k in original if k != 'exporters' and k != 'service'):
        raise RuntimeError('Unexpected configuration diff')
    new_relay = yaml.safe_dump(candidate, sort_keys=False, allow_unicode=True)
    if yaml.safe_load(new_relay) != candidate:
        raise RuntimeError('YAML roundtrip changed semantics')
    annotation = dep['spec']['template']['metadata'].get('annotations', {})
    if ANNOTATION in annotation or dep['spec']['template']['spec']['containers'][0]['resources']['limits']['memory'] != '400Mi':
        raise RuntimeError('Deployment differs from diagnosed version')
    backup = ROOT / 'backup'
    write_new(backup / 'configmap.json', cm)
    write_new(backup / 'deployment.json', dep)
    write_new(backup / 'pod.json', gateway[0])
    write_new(ROOT / 'plan.json', {
        'created_utc': now(), 'original_relay': relay, 'candidate_relay': new_relay,
        'original_sha256': digest(relay), 'candidate_sha256': digest(new_relay),
        'configmap_uid': cm['metadata']['uid'], 'deployment_uid': dep['metadata']['uid'],
        'pod_uid': gateway[0]['metadata']['uid'],
        'backup_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in backup.iterdir()},
        'change': 'remove debug exporter from all four pipelines; retain all storage exporters, processors, limits and queues',
        'faults_injected': 0})
    print(json.dumps({'status': 'BACKED_UP', 'plan': str(ROOT / 'plan.json'),
                      'candidate_sha256': digest(new_relay)}))


def patch_config(expected, replacement, uid):
    cm = obj('configmap', 'otel-collector')
    if cm['metadata']['uid'] != uid or cm['data']['relay'] != expected:
        raise RuntimeError('ConfigMap drift; refusing patch')
    operations = [
        {'op': 'test', 'path': '/metadata/resourceVersion',
         'value': cm['metadata']['resourceVersion']},
        {'op': 'test', 'path': '/data/relay', 'value': expected},
        {'op': 'replace', 'path': '/data/relay', 'value': replacement},
    ]
    kube('patch', 'configmap', 'otel-collector', '--type=json', '-p', json.dumps(operations))


def patch_deployment(plan, add):
    dep = obj('deployment', 'otel-collector')
    if dep['metadata']['uid'] != plan['deployment_uid']:
        raise RuntimeError('Deployment identity drift')
    annotations = dep['spec']['template']['metadata'].get('annotations', {})
    value = plan['candidate_sha256'][:16]
    if add and ANNOTATION in annotations:
        raise RuntimeError('Candidate annotation already exists')
    if not add and annotations.get(ANNOTATION) != value:
        raise RuntimeError('Candidate annotation drift')
    operations = [{'op': 'test', 'path': '/metadata/resourceVersion',
                   'value': dep['metadata']['resourceVersion']},
                  {'op': 'add' if add else 'remove',
                   'path': '/spec/template/metadata/annotations/' + ANNOTATION,
                   **({'value': value} if add else {})}]
    kube('patch', 'deployment', 'otel-collector', '--type=json', '-p', json.dumps(operations))
    kube('rollout', 'status', 'deployment/otel-collector', '--timeout=240s')


def rollback():
    plan = read(ROOT / 'plan.json')
    cm = obj('configmap', 'otel-collector')
    if cm['data']['relay'] == plan['candidate_relay']:
        patch_config(plan['candidate_relay'], plan['original_relay'], plan['configmap_uid'])
    elif cm['data']['relay'] != plan['original_relay']:
        raise RuntimeError('Unknown Collector config; manual intervention needed')
    annotations = obj('deployment', 'otel-collector')['spec']['template']['metadata'].get('annotations', {})
    if annotations.get(ANNOTATION) == plan['candidate_sha256'][:16]:
        patch_deployment(plan, False)
    elif ANNOTATION in annotations:
        raise RuntimeError('Unknown Deployment annotation; manual intervention needed')
    if obj('configmap', 'otel-collector')['data']['relay'] != plan['original_relay']:
        raise RuntimeError('Rollback verification failed')
    write_new(ROOT / ('rollback-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.json'),
              {'status': 'ROLLED_BACK', 'at_utc': now()})
    print('ROLLED_BACK')


def apply():
    plan = read(ROOT / 'plan.json')
    if run('systemctl', 'is-active', TIMER).strip() != 'active':
        raise RuntimeError('Independent rollback timer is not active')
    if obj('configmap', 'otel-collector')['data']['relay'] != plan['original_relay']:
        raise RuntimeError('Config changed after backup')
    try:
        patch_config(plan['original_relay'], plan['candidate_relay'], plan['configmap_uid'])
        patch_deployment(plan, True)
        if obj('configmap', 'otel-collector')['data']['relay'] != plan['candidate_relay']:
            raise RuntimeError('Collector candidate not live')
        write_new(ROOT / 'applied.json', {'status': 'CANDIDATE_APPLIED', 'at_utc': now(),
                  'config_sha256': plan['candidate_sha256'], 'faults_injected': 0})
        print('CANDIDATE_APPLIED')
    except BaseException:
        rollback()
        raise


def retain():
    plan = read(ROOT / 'plan.json')
    if obj('configmap', 'otel-collector')['data']['relay'] != plan['candidate_relay']:
        raise RuntimeError('Collector candidate drift')
    for name in ('VAL1', 'VAL2'):
        result = read(ROOT / 'results' / name / 'assessment.json')
        if result['status'] != 'NO_FAULT_VALIDATION_PASS':
            raise RuntimeError('Two complete independent validations required')
    run('systemctl', 'stop', TIMER)
    write_new(ROOT / 'retained.json', {'status': 'REPAIR_RETAINED', 'at_utc': now(),
              'config_sha256': plan['candidate_sha256']})
    print('REPAIR_RETAINED')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('prepare', 'apply', 'rollback', 'retain'))
    action = parser.parse_args().action
    {'prepare': prepare, 'apply': apply, 'rollback': rollback, 'retain': retain}[action]()
