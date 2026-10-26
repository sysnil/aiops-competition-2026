"""CAS-append the storage-stopped SF-007 attempt to the production ledger."""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
LEDGER = OPS / 'plans/SF-003-to-SF-012-production-v2-ledger.json'
RAW = Path('/data/stage5-v3/ops-competition-2026/runs/SF-007-ATTEMPT-02')
INDEX = Path('/data/stage5-v3/ops-competition-archive-index/SF-007-ATTEMPT-02')
OLD_SHA = 'f398dcce5db8ab5533ad3d9b8dd11d5ff1d8c88946a5eb95866e34ac6ea7162d'
FAILURE_SHA = 'db878fd1bc06edb4b43132ef79df1a33d0c4faa1adc38cd2db303f7a7a9b0bcb'
ARCHIVE_SHA = '4c8e78415d4fd3f544aeedfdf799285dfafb4b8df1473075028172b0c0413073'


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
            failure['status'] != 'FAILED_DYNAMIC_STORAGE_FORECAST_AFTER_RECOVERY' or
            failure['ready_for_import'] is not False or
            not all(failure['checks'].values()) or
            archive['status'] != 'COMPETITION_ARCHIVE_SHA_AND_RESTORED_READ_PASS' or
            (RAW / 'terminal.json').exists() or (RAW / 'public').exists()):
        raise RuntimeError('Failed-attempt archive or terminal evidence incomplete')
    ledger = json.loads(prior)
    matches = [row for row in ledger['official_cases'] if row['case_id'] == 'SF-007']
    if len(matches) != 1 or matches[0]['status'] != 'FAILED_TARGET_STREAM_COVERAGE':
        raise RuntimeError('SF-007 historical attempt state changed')
    case = matches[0]
    case.setdefault('attempt_history', []).append({
        'run_id': 'SF-007-ATTEMPT-02',
        'status': failure['status'],
        'fault_injected': True, 'fault_recovered': True,
        'capture_complete': False, 'public_bundle_created': False,
        'private_gt_created': False,
        'failure_record_sha256': FAILURE_SHA,
        'raw_archive_manifest_sha256': ARCHIVE_SHA,
    })
    case.update({
        'status': 'FAILED_DYNAMIC_STORAGE_FORECAST',
        'run_id': 'SF-007-ATTEMPT-02',
        'failed_gate': 'WORKER1_SHORT_INTERVAL_OPENSEARCH_WRITE_FORECAST_BELOW_5GB',
        'failure_record_sha256': FAILURE_SHA,
        'raw_archive_manifest_sha256': ARCHIVE_SHA,
        'raw_master': str(RAW),
        'raw_worker1': '/data/rca-archives/ops-competition-2026/SF-007-ATTEMPT-02/raw',
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
        'case_id': 'SF-007', 'from_status': 'FAILED_TARGET_STREAM_COVERAGE',
        'to_status': 'FAILED_DYNAMIC_STORAGE_FORECAST',
        'run_id': 'SF-007-ATTEMPT-02',
        'at_utc': datetime.now(timezone.utc).isoformat(),
        'previous_ledger_sha256': OLD_SHA,
        'failure_record_sha256': FAILURE_SHA,
        'raw_archive_manifest_sha256': ARCHIVE_SHA,
    })
    backup = LEDGER.with_name('SF-003-to-SF-012-production-v2-ledger.before-sf007-att02-' +
                              OLD_SHA[:12] + '.json')
    with backup.open('xb') as stream:
        stream.write(prior)
        stream.flush()
        os.fsync(stream.fileno())
    temp = LEDGER.with_suffix('.sf007-att02.tmp')
    with temp.open('x') as stream:
        json.dump(ledger, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, LEDGER)
    print(json.dumps({'status': 'SF007_STORAGE_STOP_LEDGER_UPDATED',
                      'ready_count': 5, 'remaining': 5,
                      'ledger_sha256': sha(LEDGER), 'backup': str(backup)}))


if __name__ == '__main__':
    main()
