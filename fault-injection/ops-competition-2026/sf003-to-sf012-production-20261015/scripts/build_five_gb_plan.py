#!/usr/bin/env python3
"""Create a separate 5 GB execution candidate without rewriting the source plan."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path('/root/ops-competition-2026')
SOURCE = ROOT / 'plans/SF-12-cases-30min-production-plan.json'
POLICY = ROOT / 'sf003-to-sf012-production-20261015/design/storage-policy-5gb.json'
OUTPUT = ROOT / 'sf003-to-sf012-production-20261015/design/production-plan-5gb.json'
EXPECTED_SOURCE_SHA256 = '762ff940a8198e0ef4470614f410edaddab3bc73a277f8a36f04015cc34943f4'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(source, policy):
    official = [c for c in source['cases'] if c['role'] == 'OFFICIAL']
    expected_ids = [f'SF-{i:03d}' for i in range(3, 13)]
    if [c['case_id'] for c in official] != expected_ids:
        raise ValueError('Source case order or count changed')
    if any(c['duration_seconds'] != 1800 or c['collection_status'] != 'NOT_STARTED'
           for c in official):
        raise ValueError('Unexpected source case state or duration')
    if policy['authorized_absolute_free_floor_bytes'] != 5_000_000_000:
        raise ValueError('Storage floor mismatch')
    return {
        'schema': 'SF003_SF012_INDEPENDENT_EXECUTION_CANDIDATE_V1',
        'status': 'BLOCKED_PENDING_MECHANISM_AND_RUNNER_QUALIFICATION',
        'generated_utc': datetime.now(timezone.utc).isoformat(),
        'source_plan_sha256': EXPECTED_SOURCE_SHA256,
        'storage_policy_sha256': sha(POLICY),
        'disk_floor_bytes_decimal': 5_000_000_000,
        'all_relevant_hosts': policy['hosts'],
        'old_60min_static_peak_reservation_is_start_gate': False,
        'dynamic_monitoring_required_during': [
            'fault', 'capture', 'export', 'archive'],
        'abort_on_predicted_floor_crossing': True,
        'automatic_archive_deletion': False,
        'formal_fault_permission': False,
        'practice_cases_unchanged': ['SF-001', 'SF-002'],
        'official_cases': [{
            'case_id': case['case_id'],
            'duration_seconds': case['duration_seconds'],
            'fault_types_in_original_order': case['fault_types'],
            'fault_event_count': len(case['fault_events']),
            'collection_status': 'NOT_STARTED',
            'qualification_blockers': case['mechanism_blockers'],
            'time_contract_status': case['time_plan_status'],
            'execution_permission': False,
        } for case in official],
    }


def main():
    if OUTPUT.exists():
        raise RuntimeError('Never overwrite an execution candidate')
    if sha(SOURCE) != EXPECTED_SOURCE_SHA256:
        raise RuntimeError('Authoritative source plan hash changed')
    source, policy = json.loads(SOURCE.read_text()), json.loads(POLICY.read_text())
    result = build(source, policy)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': result['status'], 'cases': len(result['official_cases']),
                      'floor': result['disk_floor_bytes_decimal'], 'sha256': sha(OUTPUT)}))


if __name__ == '__main__':
    main()
