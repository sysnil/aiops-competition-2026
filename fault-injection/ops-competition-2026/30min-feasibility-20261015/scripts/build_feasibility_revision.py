#!/usr/bin/env python3
"""Versioned DRAFT revision of the 12-case plan, preserving the authority file."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


HERE = Path(__file__).resolve().parents[1]
OPS = HERE.parent
SOURCES = {
    'authority': OPS / 'planning/SF-12-30min-20261015/SF-12-cases-30min-production-plan.json',
    'generic_timing': HERE / 'reports/candidate-schedules.json',
    'case_timing': HERE / 'reports/mechanism-specific-time-hypotheses-v2.json',
    'mechanism_audit': HERE / 'reports/fresh-mechanism-audit.json',
    'short_qualifications': HERE / 'reports/short-mechanism-qualification-facts.json',
    'measured_rehearsal': HERE / 'reports/nofault30-summary.json',
    'capacity_forecast': HERE / 'reports/capacity-after-rehearsal.json',
}
FLOOR = 20_000_000_000
GIB = 1024**3


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    output = HERE / 'reports/SF-12-cases-30min-feasibility-revision-v2.json'
    if output.exists():
        raise RuntimeError('No overwrite of DRAFT revision')
    material = {name: read(path) for name, path in SOURCES.items()}
    authority = material['authority']
    timing = {row['case_id']: row for row in material['case_timing']['cases']}
    audit = {row['mechanism']: row for row in material['mechanism_audit']['mechanisms']}
    rehearsal = material['measured_rehearsal']
    observed = rehearsal['terminal']['status'] == 'NOFAULT_30MIN_CAPTURE_PASS'
    archive = bool(rehearsal['dual_archive'] and
                   rehearsal['dual_archive']['status'] == 'NOFAULT_DUAL_ARCHIVE_PASS')
    preview = bool(rehearsal['w2']['preview_verification'] and
                   rehearsal['w2']['preview_verification']['status'] ==
                   'NOFAULT_PREVIEW_FULL_READBACK_PASS')
    demand = {'master': 34 * GIB, 'w2': 31 * GIB, 'w3': 29 * GIB}
    free = {
        'master': rehearsal['master_data_free_after'],
        'w2': rehearsal['w2']['data_free_bytes'],
        'w3': rehearsal['w3']['data_free_bytes'],
    }
    capacity = {name: {'available_after_rehearsal_bytes': free[name],
                       'retained_old_peak_demand_bytes': demand[name],
                       'passes_next_case_old_peak_floor':
                           free[name] - demand[name] >= FLOOR}
                for name in demand}
    cases = []
    for original in authority['cases']:
        if original['role'] == 'PRACTICE':
            cases.append({
                'case_id': original['case_id'], 'role': 'PRACTICE',
                'collection_status': 'COLLECTED_60MIN_UNCHANGED',
                'publication_status': original['public_server_status'],
                'gt_status': original['gt_status'],
            })
            continue
        row = timing[original['case_id']]
        cases.append({
            'case_id': original['case_id'], 'role': 'OFFICIAL',
            'duration_seconds': 1800, 'collection_status': 'NOT_STARTED',
            'original_fault_types_preserved': original['fault_types'],
            'timing_hypothesis': row,
            'mechanism_status': [
                {'fault_type': name, 'engineering_status': audit[name]['engineering_status'],
                 'current_formal_execution_authorized': False}
                for name in original['fault_types']
            ],
            'production_ready': False,
            'blocking_dimensions': ['NEW_FORMAL_30MIN_RUNNER_NOT_VALIDATED',
                                    'EARLY_GATE_NOT_INTEGRATED',
                                    'MECHANISM_SAFETY_OR_SHORT_DURATION_NOT_QUALIFIED',
                                    'FRESH_CASE_STORAGE_ADMISSION_REQUIRED'],
        })
    result = {
        'schema': 'SF_12_CASES_30MIN_FEASIBILITY_REVISION_DRAFT_V2',
        'status': 'DRAFT_NOT_FROZEN_NOT_EXECUTION_PERMISSION',
        'generated_utc': datetime.now(timezone.utc).isoformat(),
        'source_sha256': {name: sha(path) for name, path in SOURCES.items()},
        'cases': cases,
        'rehearsal': {'capture_pass': observed, 'dual_archive_pass': archive,
                      'parquet_preview_pass': preview},
        'disk_floor_bytes': FLOOR, 'capacity_after_rehearsal': capacity,
        'capacity_forecast': material['capacity_forecast'],
        'first_formal_case_production_ready': False,
        'historical_formal_case_manifests_modified': False,
        'formal_faults_injected_this_round': 0,
    }
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({'status': result['status'],
                      'cases': len(cases), 'rehearsal': result['rehearsal'],
                      'first_case_ready': False}))


if __name__ == '__main__':
    main()
