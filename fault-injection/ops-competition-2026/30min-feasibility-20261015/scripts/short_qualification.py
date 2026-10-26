#!/usr/bin/env python3
"""Extract only non-GT duration/effect facts from existing competition qualifications."""

import hashlib
import json
import shlex
import subprocess
from pathlib import Path


HERE = Path(__file__).resolve().parents[1]
SOURCE = '/root/ops-competition-2026/qualification'
PLAN = '/root/ops-competition-2026/plans'
HOST = ['ssh', '-S', '/tmp/sfgen-master.sock', '-o', 'BatchMode=yes',
        'root@192.168.53.52']


def remote_json(path):
    result = subprocess.run(HOST + [shlex.join(['cat', path])],
                            capture_output=True, text=True, check=True, timeout=30)
    return json.loads(result.stdout), hashlib.sha256(result.stdout.encode()).hexdigest()


def main():
    output = HERE / 'reports/short-mechanism-qualification-facts.json'
    if output.exists():
        raise RuntimeError('Do not overwrite a prior evidence extract')
    facts = {}
    for kind, name in [('ServicePause', 'COMP-SF01-SERVICE-SHORT'),
                       ('PodMemoryStress', 'COMP-SF01-POD-SHORT')]:
        result, result_hash = remote_json(f'{SOURCE}/{name}/decision.json')
        plan, plan_hash = remote_json(f'{PLAN}/{name}.json')
        if result['qualified'] is not True or result['blockers'] or not all(result['checks'].values()):
            raise RuntimeError('Short mechanism qualification is not complete: ' + kind)
        if result['private_injection_used_as_public_anomaly_evidence']:
            raise RuntimeError('Private evidence used for anomaly qualification')
        evidence = result['public_anomaly_evidence']
        facts[kind] = {
            'qualification_id': name,
            'duration_seconds': plan['duration_seconds'],
            'actual_duration_seconds': result['actual_injection_duration_seconds'],
            'historical_before_during_after_seconds': plan['durations_seconds'],
            'qualified': True,
            'public_direct_evidence': {
                'kind': evidence['kind'],
                'failed_with_public_error_spans': evidence.get('failed_with_public_error_spans'),
                'during_median_increase_bytes': evidence.get('during_median_increase_bytes'),
                'consecutive_abnormal_samples': evidence.get('consecutive_abnormal_samples'),
                'memory_recovered': evidence.get('memory_recovered'),
            },
            'decision_sha256': result_hash,
            'plan_sha256': plan_hash,
            'formal_30min_executor_integrated': False,
            'current_target_identity_requalified': False,
        }
    output.write_text(json.dumps({'status': 'HISTORICAL_SHORT_QUALIFICATIONS_VERIFIED',
                                  'mechanisms': facts, 'no_fault_injected_this_audit': True},
                                 indent=2) + '\n')
    print(json.dumps({kind: value['duration_seconds'] for kind, value in facts.items()}))


if __name__ == '__main__':
    main()
