"""CAS-record the immutable SF-007 failed attempt without changing READY counts."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
DATA = Path('/data/stage5-v3/ops-competition-2026')
INDEX = Path('/data/stage5-v3/ops-competition-archive-index/SF-007-ATTEMPT-01')
LEDGER = OPS / 'plans/SF-003-to-SF-012-production-v2-ledger.json'
OLD_SHA = '55dd88eb494ac0b738d4c354036987a07c198ca58d21d7032810e6852f537ead'
FAILURE = OPS / 'low-stable-v4-20261017/results/sf007-attempt01-control-coverage-failure.json'
FAILURE_SHA = 'a9f7da87f0f9de660a6dd3a9c4c437c7312d2dda0922926b7c11fde3910c2412'
ARCHIVE_SHA = '45db324015272c190e7aef917594573a1b8a194b0d84aa4c949662071eafe8d5'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    prior = LEDGER.read_bytes()
    if hashlib.sha256(prior).hexdigest() != OLD_SHA:
        raise RuntimeError('Production ledger changed; refusing overwrite')
    failure = read(FAILURE)
    archive = read(INDEX / 'w1-verification.json')
    health = read(OPS / 'low-stable-v4-20261017/results/sf007-attempt01-final-live-health.json')
    residue = read(OPS / 'low-stable-v4-20261017/results/sf007-attempt01-final-residue.json')
    units = read(OPS / 'low-stable-v4-20261017/results/sf007-attempt01-final-units.json')
    raw = DATA / 'runs/SF-007-ATTEMPT-01'
    if (sha(FAILURE) != FAILURE_SHA or
            sha(INDEX / 'source-manifest.json') != ARCHIVE_SHA or
            failure['status'] != 'FAILED_TARGET_STREAM_COVERAGE' or
            failure['ready_for_import'] is not False or
            read(raw / 'terminal.json')['status'] != 'SF007_FORMAL_CAPTURE_FAILED' or
            (DATA / 'packages/SF-007-ATTEMPT-01/public/manifest.json').exists() or
            archive['status'] != 'COMPETITION_ARCHIVE_SHA_AND_RESTORED_READ_PASS' or
            health['status'] != 'FINAL_LIVE_ENVIRONMENT_PASS' or
            residue['status'] != 'HOST_RESIDUE_PASS' or
            units['status'] != 'COMPETITION_UNIT_RESIDUE_PASS'):
        raise RuntimeError('SF-007 failed-attempt evidence incomplete')
    ledger = json.loads(prior)
    matches = [row for row in ledger['official_cases'] if row['case_id'] == 'SF-007']
    if len(matches) != 1 or matches[0]['status'] != 'NOT_STARTED':
        raise RuntimeError('SF-007 ledger state changed')
    case = matches[0]
    old_design = dict(case)
    case.clear()
    case.update({
        'case_id': 'SF-007', 'status': 'FAILED_TARGET_STREAM_COVERAGE',
        'run_id': 'SF-007-ATTEMPT-01',
        'faults': ['SERVICE_PAUSE_30'], 'fault_count': 1,
        'failed_gate': 'RECOMMENDATIONS_STREAM_18_MISSED_ARRIVALS',
        'failure_record_sha256': FAILURE_SHA,
        'raw_archive_manifest_sha256': ARCHIVE_SHA,
        'raw_master': str(raw),
        'raw_worker1': '/data/rca-archives/ops-competition-2026/SF-007-ATTEMPT-01/raw',
        'public_package': 'NOT_CREATED',
        'gt_status': 'NOT_CREATED_FAILED_CAPTURE',
        'server_import': 'PROHIBITED_FOR_FAILED_ATTEMPT',
        'design_history': old_design,
    })
    ledger['completed_ready_for_import'] = sum(
        row['status'] == 'READY_FOR_IMPORT' for row in ledger['official_cases'])
    ledger['remaining'] = len(ledger['official_cases']) - ledger['completed_ready_for_import']
    if (ledger['completed_ready_for_import'], ledger['remaining']) != (5, 5):
        raise RuntimeError('Failed SF-007 changed formal denominator')
    ledger['status_changes'].append({
        'case_id': 'SF-007', 'from_status': 'NOT_STARTED',
        'to_status': 'FAILED_TARGET_STREAM_COVERAGE',
        'run_id': 'SF-007-ATTEMPT-01',
        'at_utc': datetime.now(timezone.utc).isoformat(),
        'previous_ledger_sha256': OLD_SHA,
        'failure_record_sha256': FAILURE_SHA,
        'raw_archive_manifest_sha256': ARCHIVE_SHA,
    })
    if args.dry_run:
        print(json.dumps({'status': 'SF007_FAILED_LEDGER_DRY_RUN_PASS',
                          'ready_count': 5, 'remaining': 5,
                          'preserved_design': old_design}))
        return
    backup = LEDGER.with_name('SF-003-to-SF-012-production-v2-ledger.before-sf007-att01-' +
                              OLD_SHA[:12] + '.json')
    with backup.open('xb') as stream:
        stream.write(prior)
        stream.flush()
        os.fsync(stream.fileno())
    temp = LEDGER.with_suffix('.sf007-att01.tmp')
    with temp.open('x') as stream:
        json.dump(ledger, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, LEDGER)
    print(json.dumps({'status': 'SF007_FAILED_LEDGER_UPDATED',
                      'ready_count': 5, 'remaining': 5,
                      'old_sha256': OLD_SHA, 'new_sha256': sha(LEDGER),
                      'backup': str(backup)}))


if __name__ == '__main__':
    main()
