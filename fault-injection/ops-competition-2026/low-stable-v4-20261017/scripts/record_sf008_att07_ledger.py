"""CAS-append the pre-injection SF-008 storage-reservation failure."""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
LEDGER = OPS / 'plans/SF-003-to-SF-012-production-v2-ledger.json'
RAW = Path('/data/stage5-v3/ops-competition-2026/runs/SF-008-ATTEMPT-07')
INDEX = Path('/data/stage5-v3/ops-competition-archive-index/SF-008-ATTEMPT-07')
OLD_SHA = '002f08bb8998f825433e555fbad34e40ce26e19db2ea384fba277347164fe7ea'
FAILURE_SHA = '4e93256749a1920b1954b14b12601c9b9e7298eb33806e661ffbcce034af3865'
ARCHIVE_SHA = 'a46b9fd5534e40638ab4958f62973093247c73629d2b2b12246cb4e831265b02'


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
            failure['status'] != 'FAILED_BEFORE_INJECTION_W3_RESERVATION_FORECAST' or
            failure['ready_for_import'] is not False or
            failure['faults_injected'] != 0 or
            not all(failure['checks'].values()) or
            archive['status'] != 'COMPETITION_ARCHIVE_SHA_AND_RESTORED_READ_PASS' or
            read(RAW / 'terminal.json')['status'] != 'SF008_FORMAL_CAPTURE_FAILED' or
            (RAW / 'public').exists()):
        raise RuntimeError('Failed-attempt archive or terminal evidence incomplete')
    ledger = json.loads(prior)
    matches = [row for row in ledger['official_cases'] if row['case_id'] == 'SF-008']
    if len(matches) != 1 or matches[0]['status'] != 'FAILED_CAPTURE_STAGE_DEADLINE':
        raise RuntimeError('SF-008 historical attempt state changed')
    case = matches[0]
    case.setdefault('attempt_history', []).append({
        'run_id': 'SF-008-ATTEMPT-07',
        'status': failure['status'],
        'fault_injected': False, 'fault_recovered': False,
        'capture_complete': False, 'public_bundle_created': False,
        'private_gt_created': False,
        'failure_record_sha256': FAILURE_SHA,
        'raw_archive_manifest_sha256': ARCHIVE_SHA,
    })
    case.update({
        'status': 'FAILED_BEFORE_INJECTION_STORAGE_RESERVATION',
        'run_id': 'SF-008-ATTEMPT-07',
        'faults': ['POD_MEMORY_32M_300'], 'fault_count': 1,
        'failed_gate': 'UNNEEDED_WORKER3_FULL_ARCHIVE_RESERVATION_PREDICTED_BELOW_5GB',
        'failure_record_sha256': FAILURE_SHA,
        'raw_archive_manifest_sha256': ARCHIVE_SHA,
        'raw_master': str(RAW),
        'raw_worker2': '/data/rca-archives/ops-competition-2026/SF-008-ATTEMPT-07/raw',
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
        'case_id': 'SF-008', 'from_status': 'FAILED_CAPTURE_STAGE_DEADLINE',
        'to_status': 'FAILED_BEFORE_INJECTION_STORAGE_RESERVATION',
        'run_id': 'SF-008-ATTEMPT-07',
        'at_utc': datetime.now(timezone.utc).isoformat(),
        'previous_ledger_sha256': OLD_SHA,
        'failure_record_sha256': FAILURE_SHA,
        'raw_archive_manifest_sha256': ARCHIVE_SHA,
    })
    backup = LEDGER.with_name('SF-003-to-SF-012-production-v2-ledger.before-sf008-att07-' +
                              OLD_SHA[:12] + '.json')
    with backup.open('xb') as stream:
        stream.write(prior)
        stream.flush()
        os.fsync(stream.fileno())
    temp = LEDGER.with_suffix('.sf008-att07.tmp')
    with temp.open('x') as stream:
        json.dump(ledger, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, LEDGER)
    print(json.dumps({'status': 'SF008_PREINJECTION_RESERVATION_LEDGER_UPDATED',
                      'ready_count': 5, 'remaining': 5,
                      'ledger_sha256': sha(LEDGER), 'backup': str(backup)}))


if __name__ == '__main__':
    main()
