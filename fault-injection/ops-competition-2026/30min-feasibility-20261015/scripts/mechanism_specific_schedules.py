#!/usr/bin/env python3
"""Case-specific hypothetical schedules using historical shorts and 60s unknowns."""

import json
from pathlib import Path


HERE = Path(__file__).resolve().parents[1]
PLAN = HERE.parent / 'planning/SF-12-30min-20261015/SF-12-cases-30min-production-plan.json'
FACTS = HERE / 'reports/short-mechanism-qualification-facts.json'
CHECK = 45
PREFLIGHT = 105
RECOVERY = 180
WINDOW = 1800


def schedule(types, known):
    durations = [known[name] if name in known else 60 for name in types]
    count = len(types)
    # The first 300s baseline must finish; the first live capture should have
    # completed before an early event. 330s is still an unverified hypothesis.
    # The ordinary business baseline check runs 55 seconds before onset.
    # It must follow the full first 300-second clean window.
    first = 600 if count <= 3 else 360
    start = first
    events = []
    for index, (name, duration) in enumerate(zip(types, durations), 1):
        events.append({'sequence': index, 'mechanism': name,
                       'start_seconds': start, 'duration_seconds': duration,
                       'duration_evidence': 'HISTORICAL_SHORT_QUALIFIED_NOT_FORMAL_30MIN'
                       if name in known else 'UNQUALIFIED_60S_HYPOTHESIS'})
        if index != count:
            start += duration + RECOVERY + CHECK + PREFLIGHT
    finish = events[-1]['start_seconds'] + durations[-1] + RECOVERY + CHECK
    assert first - 55 >= 300
    return {'events': events, 'complete_by_seconds': finish,
            'fits_mathematically': finish <= WINDOW,
            'requires_deferred_audit': first < 600,
            'unknown_duration_mechanisms': [name for name in types if name not in known],
            'no_overlap': all(b['start_seconds'] - PREFLIGHT >=
                              a['start_seconds'] + a['duration_seconds'] + RECOVERY + CHECK
                              for a, b in zip(events, events[1:])),
            'production_authorized': False}


def main():
    output = HERE / 'reports/mechanism-specific-time-hypotheses-v2.json'
    if output.exists():
        raise RuntimeError('Do not overwrite a timing hypothesis')
    facts = json.loads(FACTS.read_text())['mechanisms']
    known = {name: details['duration_seconds'] for name, details in facts.items()}
    cases = json.loads(PLAN.read_text())['cases']
    rows = []
    for case in cases:
        if case['role'] != 'OFFICIAL':
            continue
        row = schedule(case['fault_types'], known)
        row['case_id'] = case['case_id']
        row['status'] = ('MATHEMATICAL_FIT_ONLY_ALL_GATES_PENDING' if row['fits_mathematically']
                         else 'NOT_FIT_WITH_180S_RECOVERY_AND_60S_UNKNOWN_FAULTS')
        rows.append(row)
    output.write_text(json.dumps({
        'status': 'CASE_SPECIFIC_HYPOTHESES_NOT_EXECUTION_PERMISSION',
        'historical_short_durations_seconds': known,
        'unverified_other_mechanism_duration_seconds': 60,
        'recovery_seconds_after_each_fault': RECOVERY,
        'recovery_check_seconds': CHECK,
        'next_preflight_seconds': PREFLIGHT,
        'window_seconds': WINDOW,
        'cases': rows,
        'no_fault_injected': True,
    }, indent=2) + '\n')
    print(json.dumps({row['case_id']: row['status'] for row in rows}))


if __name__ == '__main__':
    main()
