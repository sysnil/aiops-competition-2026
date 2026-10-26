#!/usr/bin/env python3
"""Fail-closed production admission; it has no fault-execution capability."""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


EXPECTED_PLAN_SHA256 = '1c208f9d5b91630fd297c25dee7c4c673e1728c567a6ec3a55d3339b89cfd3de'
EXPECTED_ORDER = [f'SF-{index:03d}' for index in range(3, 13)]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fresh(record, max_age_seconds=300):
    try:
        age = (datetime.now(timezone.utc) -
               datetime.fromisoformat(record['at_utc'])).total_seconds()
    except (KeyError, TypeError, ValueError):
        return False
    return 0 <= age <= max_age_seconds


def assess(plan, storage, residue, formal_runner=None):
    if plan['disk_floor_bytes_decimal'] != 5_000_000_000 or (
            [case['case_id'] for case in plan['official_cases']] != EXPECTED_ORDER):
        raise ValueError('Formal scope or storage floor changed')
    global_checks = {
        'storage_next_case': fresh(storage) and
                             storage['one_case']['status'] == 'DYNAMIC_STORAGE_PASS',
        'five_host_residue': fresh(residue) and
                             residue['status'] == 'HOST_RESIDUE_PASS',
        'formal_30min_runner': bool(formal_runner and
                                    formal_runner.get('status') == 'FORMAL_30MIN_RUNNER_VALIDATED'),
    }
    cases = []
    for case in plan['official_cases']:
        blockers = list(case['qualification_blockers'])
        if case['time_contract_status'] != 'FORMAL_TIME_CONTRACT_VALIDATED':
            blockers.append('FORMAL_TIME_CONTRACT_NOT_VALIDATED')
        if not global_checks['storage_next_case']:
            blockers.append('FIVE_GB_DYNAMIC_STORAGE_GATE')
        if not global_checks['five_host_residue']:
            blockers.append('FIVE_HOST_RESIDUE_GATE')
        if not global_checks['formal_30min_runner']:
            blockers.append('FORMAL_30MIN_RUNNER_NOT_VALIDATED')
        cases.append({'case_id': case['case_id'],
                      'fault_types_in_original_order': case['fault_types_in_original_order'],
                      'planned_fault_count': case['fault_event_count'],
                      'actual_fault_count': 0, 'collection_started': False,
                      'status': 'BLOCKED' if blockers else 'PRE_INJECTION_READY',
                      'blockers': blockers})
    ready_count = sum(case['status'] == 'PRE_INJECTION_READY' for case in cases)
    status = ('NO_FORMAL_CASE_ELIGIBLE' if ready_count == 0 else
              'ALL_CASES_PRE_INJECTION_READY' if ready_count == len(cases) else
              'SOME_CASES_PRE_INJECTION_READY')
    return {'status': status,
            'at_utc': datetime.now(timezone.utc).isoformat(),
            'global_checks': global_checks, 'cases': cases,
            'faults_started_by_gate': 0, 'public_created_by_gate': False,
            'gt_created_by_gate': False,
            'this_gate_is_not_fault_permission': True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--storage', type=Path, required=True)
    parser.add_argument('--residue', type=Path, required=True)
    parser.add_argument('--runner-validation', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or sha(args.plan) != EXPECTED_PLAN_SHA256:
        raise RuntimeError('No overwrite and exact production plan required')
    inputs = [args.plan, args.storage, args.residue]
    runner = None
    if args.runner_validation:
        inputs.append(args.runner_validation)
        runner = json.loads(args.runner_validation.read_text())
    result = assess(json.loads(args.plan.read_text()),
                    json.loads(args.storage.read_text()),
                    json.loads(args.residue.read_text()), runner)
    result['input_sha256'] = {str(path): sha(path) for path in inputs}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': result['status'],
                      'blocked_cases': [c['case_id'] for c in result['cases']
                                        if c['status'] == 'BLOCKED']}))


if __name__ == '__main__':
    main()
