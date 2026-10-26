"""CAS-append the storage-gate semantics failure to the production ledger."""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
LEDGER = OPS / 'plans/SF-003-to-SF-012-production-v2-ledger.json'
RAW = Path('/data/stage5-v3/ops-competition-2026/runs/SF-007-ATTEMPT-04')
INDEX = Path('/data/stage5-v3/ops-competition-archive-index/SF-007-ATTEMPT-04')
OLD_SHA = '5eefaaa7cab49b76a7477b90bf537e6cdf5b79769e86afcbde91ddcb6bfc72f3'
FAILURE_SHA = 'b1e31a70f39813d334a307b85787e1b42ba4ada4520d488e877b31acabd1e9da'
ARCHIVE_SHA = '4e03f0cbd2d9df32fcda1d6cd9097af4e3f3e5315af8e8baf71388e7a37bf434'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def main():
    prior = LEDGER.read_bytes()
    if hashlib.sha256(prior).hexdigest() != OLD_SHA:
        raise RuntimeError('Production ledger changed')
    failure = read(RAW / 'attempt-failure.json')
    archive = read(INDEX / 'w1-verification.json')
    if (sha(RAW / 'attempt-failure.json') != FAILURE_SHA or
            sha(INDEX / 'source-manifest.json') != ARCHIVE_SHA or
            failure['status'] != 'FAILED_STORAGE_GATE_SEMANTICS_MISMATCH_AFTER_RECOVERY' or
            failure['ready_for_import'] is not False or
            not all(failure['checks'].values()) or
            archive['status'] != 'COMPETITION_ARCHIVE_SHA_AND_RESTORED_READ_PASS' or
            read(RAW / 'terminal.json')['status'] != 'SF007_FORMAL_CAPTURE_FAILED' or
            (RAW / 'public').exists()):
        raise RuntimeError('Failed-attempt archive or terminal evidence incomplete')
    ledger = json.loads(prior)
    matches = [row for row in ledger['official_cases'] if row['case_id'] == 'SF-007']
    if len(matches) != 1 or matches[0]['status'] != 'FAILED_BEFORE_INJECTION':
        raise RuntimeError('SF-007 historical attempt state changed')
    case = matches[0]
    case.setdefault('attempt_history', []).append({
        'run_id': 'SF-007-ATTEMPT-04',
        'status': failure['status'],
        'fault_injected': True, 'fault_recovered': True,
        'capture_complete': False, 'public_bundle_created': False,
        'private_gt_created': False,
        'failure_record_sha256': FAILURE_SHA,
        'raw_archive_manifest_sha256': ARCHIVE_SHA,
    })
    case.update({
        'status': 'FAILED_STORAGE_GATE_SEMANTICS_MISMATCH',
        'run_id': 'SF-007-ATTEMPT-04',
        'failed_gate': 'MASTER_PREFLIGHT_REJECTED_V2_PENDING_FORECAST_PASS',
        'failure_record_sha256': FAILURE_SHA,
        'raw_archive_manifest_sha256': ARCHIVE_SHA,
        'raw_master': str(RAW),
        'raw_worker1': '/data/rca-archives/ops-competition-2026/SF-007-ATTEMPT-04/raw',
        'public_package': 'NOT_CREATED',
        'gt_status': 'NOT_CREATED_FAILED_CAPTURE',
        'server_import': 'PROHIBITED_FOR_FAILED_ATTEMPT',
    })
    ledger['completed_ready_for_import'] = sum(
        row['status'] == 'READY_FOR_IMPORT' for row in ledger['official_cases'])
    ledger['remaining'] = len(ledger['official_cases']) - ledger['completed_ready_for_import']
    if (ledger['completed_ready_for_import'], ledger['remaining']) != (5, 5):
        raise RuntimeError('Failure changed READY denominator')
    ledger['status_changes'].append({
        'case_id': 'SF-007', 'from_status': 'FAILED_BEFORE_INJECTION',
        'to_status': 'FAILED_STORAGE_GATE_SEMANTICS_MISMATCH',
        'run_id': 'SF-007-ATTEMPT-04',
        'at_utc': datetime.now(timezone.utc).isoformat(),
        'previous_ledger_sha256': OLD_SHA,
        'failure_record_sha256': FAILURE_SHA,
        'raw_archive_manifest_sha256': ARCHIVE_SHA,
    })
    backup = LEDGER.with_name('SF-003-to-SF-012-production-v2-ledger.before-sf007-att04-' +
                              OLD_SHA[:12] + '.json')
    with backup.open('xb') as stream:
        stream.write(prior)
        stream.flush()
        os.fsync(stream.fileno())
    temp = LEDGER.with_suffix('.sf007-att04.tmp')
    with temp.open('x') as stream:
        json.dump(ledger, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, LEDGER)
    print(json.dumps({'status': 'SF007_STORAGE_GATE_MISMATCH_LEDGER_UPDATED',
                      'ready_count': 5, 'remaining': 5,
                      'ledger_sha256': sha(LEDGER), 'backup': str(backup)}))


if __name__ == '__main__':
    main()
