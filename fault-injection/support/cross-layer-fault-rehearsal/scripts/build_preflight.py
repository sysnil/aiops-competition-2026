"""Freeze fresh rehearsal Pod identities without opening Stage5-v2 GT."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

import yaml


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(plan_path, health_path, experiment_id):
    plan = yaml.safe_load(plan_path.read_text())
    health = json.loads(health_path.read_text())
    experiments = [row for row in plan['service_pod_experiments']
                   if row['id'] == experiment_id]
    age = (datetime.now(timezone.utc) -
           datetime.fromisoformat(health['at_utc'])).total_seconds()
    if (len(experiments) != 1 or health.get('status') != 'STAGE5_BASE_HEALTH_PASS' or
            not 0 <= age <= 180 or health.get('workload_nonzero_failures_2m') != 0):
        raise ValueError('Rehearsal needs one registered experiment and fresh clean health')
    spec = experiments[0]
    data = json.loads(subprocess.check_output(['kubectl', '-n', 'otel-demo',
        'get', 'pods', '-l', 'opentelemetry.io/name=' + spec['target_service'],
        '-o', 'json'], text=True, timeout=30))
    targets = []
    for item in data['items']:
        statuses = item['status'].get('containerStatuses', [])
        ready = any(row['type'] == 'Ready' and row['status'] == 'True'
                    for row in item['status'].get('conditions', []))
        if not ready or len(statuses) != 1 or not statuses[0].get('ready') or (
                statuses[0]['restartCount'] != 0 or
                statuses[0]['name'] != spec['target_service']):
            raise ValueError('Target Pod is not stable and Ready')
        targets.append({'service': spec['target_service'],
                        'name': item['metadata']['name'],
                        'uid': item['metadata']['uid'],
                        'container_id': statuses[0]['containerID'],
                        'node': item['spec']['nodeName'],
                        'pod_ip': item['status']['podIP']})
    controls = []
    if spec.get('target_scope') == 'both_ready_replicas':
        if len(targets) != 2 or len({row['node'] for row in targets}) != 2:
            raise ValueError('Service-wide rehearsal needs two Nodes/replicas')
    else:
        controls = [row for row in targets if row['node'] != spec['target_node']]
        targets = [row for row in targets if row['node'] == spec['target_node']]
        if len(targets) != 1 or len(controls) != 1:
            raise ValueError('Pod rehearsal needs one target and one stable sibling')
    return {'status': 'RH_PREFLIGHT_READY',
            'experiment_id': experiment_id, 'source_case': spec['source_case'],
            'created_at_utc': datetime.now(timezone.utc).isoformat(),
            'plan_sha256': sha256(plan_path), 'health_sha256': sha256(health_path),
            'targets': sorted(targets, key=lambda row: row['node']),
            'controls': controls,
            'faults_injected': 0, 'formal_case_created': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--health', required=True, type=Path)
    parser.add_argument('--experiment-id', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Preflight output already exists')
    result = build(args.plan, args.health, args.experiment_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'status': result['status'], 'experiment_id': args.experiment_id,
                      'targets': [row['name'] for row in result['targets']],
                      'sha256': sha256(args.output)}))


if __name__ == '__main__':
    main()
