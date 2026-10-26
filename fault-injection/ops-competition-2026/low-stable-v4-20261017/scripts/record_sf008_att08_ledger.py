"""CAS-append the SF-008 Worker1 storage-forecast failure."""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
LEDGER = OPS / 'plans/SF-003-to-SF-012-production-v2-ledger.json'
RAW = Path('/data/stage5-v3/ops-competition-2026/runs/SF-008-ATTEMPT-08')
INDEX = Path('/data/stage5-v3/ops-competition-archive-index/SF-008-ATTEMPT-08')
OLD_SHA = '8698a7957c3d0d74147825e33d74cfc5357b68e83f7073ebe3e5d311d7c52269'
FAILURE_SHA = '405cd0a40347ad9559f6510f68202bb4ff250758e1d76d5cd6a3ca88ab32a486'
ARCHIVE_SHA = 'c85989e52fea351dfafbd1b62f79c3464ff5b2d632aa0f5dd1dbf22215468b96'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def main():
    prior = LEDGER.read_bytes()
    if hashlib.sha256(prior).hexdigest() != OLD_SHA:
        raise RuntimeError('Production ledger changed')
    failure = read(RAW / 'attempt-failure.json')
    archive = read(INDEX / 'raw-worker2-verification.json')
    if (sha(RAW / 'attempt-failure.json') != FAILURE_SHA or
            sha(INDEX / 'raw-source-manifest.json') != ARCHIVE_SHA or
            failure['status'] != 'FAILED_SUSTAINED_WORKER1_STORAGE_FORECAST_AFTER_RECOVERY' or
            failure['ready_for_import'] is not False or
            not all(failure['checks'].values()) or
            archive['status'] != 'COMPETITION_ARCHIVE_SHA_AND_RESTORED_READ_PASS' or
            (RAW / 'terminal.json').exists() or (RAW / 'public').exists()):
        raise RuntimeError('Failed-attempt archive or terminal evidence incomplete')
    ledger = json.loads(prior)
    matches = [row for row in ledger['official_cases'] if row['case_id'] == 'SF-008']
    if len(matches) != 1 or matches[0]['status'] != 'FAILED_BEFORE_INJECTION_STORAGE_RESERVATION':
        raise RuntimeError('SF-008 historical attempt state changed')
    case = matches[0]
    case.setdefault('attempt_history', []).append({
        'run_id': 'SF-008-ATTEMPT-08',
        'status': failure['status'],
        'fault_injected': True, 'fault_recovered': True,
        'capture_complete': False, 'public_bundle_created': False,
        'private_gt_created': False,
        'failure_record_sha256': FAILURE_SHA,
        'raw_archive_manifest_sha256': ARCHIVE_SHA,
    })
    case.update({
        'status': 'FAILED_STORAGE_FORECAST',
        'run_id': 'SF-008-ATTEMPT-08',
        'faults': ['POD_MEMORY_32M_300'], 'fault_count': 1,
        'failed_gate': 'THREE_CONSECUTIVE_WORKER1_FORECASTS_BELOW_5GB',
        'failure_record_sha256': FAILURE_SHA,
        'raw_archive_manifest_sha256': ARCHIVE_SHA,
        'raw_master': str(RAW),
        'raw_worker2': '/data/rca-archives/ops-competition-2026/SF-008-ATTEMPT-08/raw',
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
        'case_id': 'SF-008', 'from_status': 'FAILED_BEFORE_INJECTION_STORAGE_RESERVATION',
        'to_status': 'FAILED_STORAGE_FORECAST',
        'run_id': 'SF-008-ATTEMPT-08',
        'at_utc': datetime.now(timezone.utc).isoformat(),
        'previous_ledger_sha256': OLD_SHA,
        'failure_record_sha256': FAILURE_SHA,
        'raw_archive_manifest_sha256': ARCHIVE_SHA,
    })
    backup = LEDGER.with_name('SF-003-to-SF-012-production-v2-ledger.before-sf008-att08-' +
                              OLD_SHA[:12] + '.json')
    with backup.open('xb') as stream:
        stream.write(prior)
        stream.flush()
        os.fsync(stream.fileno())
    temp = LEDGER.with_suffix('.sf008-att08.tmp')
    with temp.open('x') as stream:
        json.dump(ledger, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, LEDGER)
    print(json.dumps({'status': 'SF008_WORKER1_STORAGE_STOP_LEDGER_UPDATED',
                      'ready_count': 5, 'remaining': 5,
                      'ledger_sha256': sha(LEDGER), 'backup': str(backup)}))


if __name__ == '__main__':
    main()
