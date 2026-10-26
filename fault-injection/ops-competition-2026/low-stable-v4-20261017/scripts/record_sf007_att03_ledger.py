"""CAS-append the pre-injection SF-007 failure to the production ledger."""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
LEDGER = OPS / 'plans/SF-003-to-SF-012-production-v2-ledger.json'
RAW = Path('/data/stage5-v3/ops-competition-2026/runs/SF-007-ATTEMPT-03')
INDEX = Path('/data/stage5-v3/ops-competition-archive-index/SF-007-ATTEMPT-03')
OLD_SHA = 'f85439c5ceb18e890f50d57b46991a5189ddbd3e97b918dc2c040bcf7556bac2'
FAILURE_SHA = '1b4cdc70ba95f4ac9198368f4dd7c3046dc94361878aa80a2f547dd72c8bcf6d'
ARCHIVE_SHA = '8dafd3a86e78addade6c2f4c9a4bebc5115ca17342e638e1fddf52094866dc81'


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
            failure['status'] != 'FAILED_BEFORE_INJECTION_MONITOR_PATH_KEY' or
            failure['ready_for_import'] is not False or
            failure['faults_injected'] != 0 or
            failure['capture_started'] is not False or
            failure['master_execution_units_absent'] is not True or
            failure['worker3_timer_absent'] is not True or
            archive['status'] != 'COMPETITION_ARCHIVE_SHA_AND_RESTORED_READ_PASS' or
            (RAW / 'terminal.json').exists() or (RAW / 'public').exists()):
        raise RuntimeError('Failed-attempt archive or terminal evidence incomplete')
    ledger = json.loads(prior)
    matches = [row for row in ledger['official_cases'] if row['case_id'] == 'SF-007']
    if len(matches) != 1 or matches[0]['status'] != 'FAILED_DYNAMIC_STORAGE_FORECAST':
        raise RuntimeError('SF-007 historical attempt state changed')
    case = matches[0]
    case.setdefault('attempt_history', []).append({
        'run_id': 'SF-007-ATTEMPT-03',
        'status': failure['status'],
        'fault_injected': False, 'fault_recovered': False,
        'capture_complete': False, 'public_bundle_created': False,
        'private_gt_created': False,
        'failure_record_sha256': FAILURE_SHA,
        'raw_archive_manifest_sha256': ARCHIVE_SHA,
    })
    case.update({
        'status': 'FAILED_BEFORE_INJECTION',
        'run_id': 'SF-007-ATTEMPT-03',
        'failed_gate': 'MAC_MASTER_MONITOR_SOURCE_PATH_KEY_MISMATCH',
        'failure_record_sha256': FAILURE_SHA,
        'raw_archive_manifest_sha256': ARCHIVE_SHA,
        'raw_master': str(RAW),
        'raw_worker1': '/data/rca-archives/ops-competition-2026/SF-007-ATTEMPT-03/raw',
        'public_package': 'NOT_CREATED',
        'gt_status': 'NOT_CREATED_PREINJECTION_FAILURE',
        'server_import': 'PROHIBITED_FOR_FAILED_ATTEMPT',
    })
    ledger['completed_ready_for_import'] = sum(
        row['status'] == 'READY_FOR_IMPORT' for row in ledger['official_cases'])
    ledger['remaining'] = len(ledger['official_cases']) - ledger['completed_ready_for_import']
    if (ledger['completed_ready_for_import'], ledger['remaining']) != (5, 5):
        raise RuntimeError('Failure changed READY denominator')
    ledger['status_changes'].append({
        'case_id': 'SF-007', 'from_status': 'FAILED_DYNAMIC_STORAGE_FORECAST',
        'to_status': 'FAILED_BEFORE_INJECTION',
        'run_id': 'SF-007-ATTEMPT-03',
        'at_utc': datetime.now(timezone.utc).isoformat(),
        'previous_ledger_sha256': OLD_SHA,
        'failure_record_sha256': FAILURE_SHA,
        'raw_archive_manifest_sha256': ARCHIVE_SHA,
    })
    backup = LEDGER.with_name('SF-003-to-SF-012-production-v2-ledger.before-sf007-att03-' +
                              OLD_SHA[:12] + '.json')
    with backup.open('xb') as stream:
        stream.write(prior)
        stream.flush()
        os.fsync(stream.fileno())
    temp = LEDGER.with_suffix('.sf007-att03.tmp')
    with temp.open('x') as stream:
        json.dump(ledger, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, LEDGER)
    print(json.dumps({'status': 'SF007_PREINJECTION_FAILURE_LEDGER_UPDATED',
                      'ready_count': 5, 'remaining': 5,
                      'ledger_sha256': sha(LEDGER), 'backup': str(backup)}))


if __name__ == '__main__':
    main()
