#!/usr/bin/env python3
"""Offline timing bounds. Outputs hypotheses, never execution permission."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / 'planning/SF-12-30min-20261015/SF-12-cases-30min-production-plan.json'
HERE = Path(__file__).resolve().parents[1]
KNOWN_DURATION = {'ServicePause': 300, 'PodMemoryStress': 300}
BASELINE_MIN = 300
PREFLIGHT = 105
RECOVERY_CHECK = 45
WINDOW = 1800

# All rows except the unchanged 300s duration in row 2 require new mechanism
# effectiveness and recovery experiments. The first-chunk final audit is retained.
PATTERNS = {
    2: {'first': 540, 'duration': 300, 'recovery': 240, 'last_recovery': 180},
    3: {'first': 600, 'duration': 180, 'recovery': 90, 'last_recovery': 90},
    4: {'first': 480, 'duration': 120, 'recovery': 60, 'last_recovery': 60},
    5: {'first': 420, 'duration': 90, 'recovery': 45, 'last_recovery': 45},
    6: {'first': 360, 'duration': 60, 'recovery': 30, 'last_recovery': 30},
}


def propose(number):
    p = PATTERNS[number]
    starts = [p['first']]
    for _ in range(1, number):
        starts.append(starts[-1] + p['duration'] + p['recovery'] + RECOVERY_CHECK + PREFLIGHT)
    last_complete = starts[-1] + p['duration'] + p['last_recovery'] + RECOVERY_CHECK
    assert starts[0] >= BASELINE_MIN and last_complete <= WINDOW
    for previous, current in zip(starts, starts[1:]):
        assert current - PREFLIGHT >= previous + p['duration'] + p['recovery'] + RECOVERY_CHECK
    return {
        'event_count': number, 'starts_seconds': starts,
        'ends_seconds': [start + p['duration'] for start in starts],
        'proposed_duration_seconds_each': p['duration'],
        'proposed_between_fault_recovery_seconds': p['recovery'],
        'proposed_last_recovery_seconds': p['last_recovery'],
        'recovery_check_seconds': RECOVERY_CHECK,
        'next_preflight_seconds': PREFLIGHT,
        'complete_by_seconds': last_complete,
        'first_300s_chunk_end_seconds': 300,
        'first_chunk_plus240_audit_start_seconds': 540,
        'first_chunk_full_quality_latest_seconds': 720,
        'requires_deferred_full_chunk_audit': starts[0] < 720,
        'execution_authorized': False,
        'prerequisites': [
            'live first-capture and public telemetry sanity before first fault',
            'immutable +60/+120/+240 and final identity audit still run',
            'if an audit fails, cancel pending events and retain invalid case',
            'duration and recovery verified independently for every mechanism',
            'fresh target, scope, control-plane, lease and watchdog proof',
            'fixed workload and no overlapping faults',
        ],
    }


def main():
    cases = json.loads(PLAN.read_text())['cases']
    patterns = {str(n): propose(n) for n in range(2, 7)}
    assessed = []
    for case in cases:
        if case['role'] != 'OFFICIAL':
            continue
        pattern = patterns[str(len(case['fault_types']))]
        fixed = [name for name in case['fault_types']
                 if name in KNOWN_DURATION and KNOWN_DURATION[name] > pattern['proposed_duration_seconds_each']]
        assessed.append({
            'case_id': case['case_id'], 'fault_types': case['fault_types'],
            'mathematical_pattern': str(len(case['fault_types'])),
            'existing_300s_duration_conflicts': fixed,
            'status': ('DURATION_CONFLICT_UNDER_THIS_PATTERN' if fixed else
                       'ONLY_MATHEMATICAL_CANDIDATE_UNQUALIFIED_MECHANISMS_AND_AUDIT_GATE'),
            'mechanism_qualification_missing': case['mechanism_blockers'],
            'execution_authorized': False,
        })
    output = HERE / 'reports/candidate-schedules.json'
    if output.exists():
        raise RuntimeError('Never replace a timing hypothesis')
    output.write_text(json.dumps({
        'status': 'MATHEMATICAL_CANDIDATES_NOT_VALIDATED_FOR_PRODUCTION',
        'patterns': patterns, 'cases': assessed,
        'no_existing_300_second_contract_modified': True,
        'no_fault_injection': True,
    }, indent=2) + '\n')
    print(json.dumps({'status': 'TIMING_HYPOTHESES_GENERATED',
                      'cases': len(assessed), 'patterns': sorted(patterns)}))


if __name__ == '__main__':
    main()
