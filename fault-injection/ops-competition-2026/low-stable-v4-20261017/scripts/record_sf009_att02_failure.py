"""Close SF-009 ATTEMPT-02 without a fault or a Public package."""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
LEDGER = OPS / 'plans/SF-003-to-SF-012-production-v2-ledger.json'
RUN = Path('/data/stage5-v3/ops-competition-2026/runs/SF-009-ATTEMPT-02')
INDEX = Path('/data/stage5-v3/ops-competition-archive-index/SF-009-ATTEMPT-02')
PRIOR_SHA = 'bea5dc3dbce9cf4d8b100b108efbeaa519a4cfbdde3a521f0cbaed074873cb61'
FAULT_SHA = '3151bbfad09202381d2ea4904886ad9a6c7c31d0a6400e1f3f7cb5c2851369fd'
ARCHIVE_SHA = '58b3e6286ed906e2e5b9f699721da8ed0842459116c324da7152ca89f3deb46a'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path):
    return json.loads(path.read_text())


def main():
    prior = LEDGER.read_bytes()
    if hashlib.sha256(prior).hexdigest() != PRIOR_SHA:
        raise RuntimeError('Production ledger CAS mismatch')
    failure = load(RUN / 'attempt-failure.json')
    terminal = load(RUN / 'control/fault-controller-terminal.json')
    archive = load(INDEX / 'worker2-verification.json')
    if (failure['status'] != 'FAILED_BEFORE_INJECTION_MASTER_TO_WORKER3_AUTH' or
            failure['fault_injected'] is not False or
            failure['public_bundle_created'] is not False or
            failure['private_gt_created'] is not False or
            sha(RUN / 'control/fault-controller-terminal.json') != FAULT_SHA or
            sha(INDEX / 'source-manifest.json') != ARCHIVE_SHA or
            archive['status'] != 'COMPETITION_ARCHIVE_SHA_AND_RESTORED_READ_PASS' or
            terminal['faults'] or terminal['actual_onsets'] or
            (RUN / 'terminal.json').exists() or (RUN / 'public').exists()):
        raise RuntimeError('ATTEMPT-02 is not a verified pre-injection failure')
    ledger = json.loads(prior)
    rows = [row for row in ledger['official_cases'] if row['case_id'] == 'SF-009']
    if len(rows) != 1 or rows[0]['run_id'] != 'SF-009-ATTEMPT-01':
        raise RuntimeError('Historical SF-009 state changed')
    row = rows[0]
    old = {key: value for key, value in row.items() if key != 'attempt_history'}
    history = row.setdefault('attempt_history', [])
    if any(item.get('run_id') == 'SF-009-ATTEMPT-02' for item in history):
        raise RuntimeError('ATTEMPT-02 already recorded')
    history.extend((old, {
        'run_id': 'SF-009-ATTEMPT-02', 'status': failure['status'],
        'fault_injected': False, 'capture_complete': False,
        'public_bundle_created': False, 'private_gt_created': False,
        'formal_manifest_sha256': failure['formal_manifest_sha256'],
        'failure_record_sha256': sha(RUN / 'attempt-failure.json'),
        'raw_archive_manifest_sha256': ARCHIVE_SHA,
    }))
    for key in list(row):
        if key != 'attempt_history':
            del row[key]
    row.update({'case_id': 'SF-009', 'status': failure['status'],
                'run_id': 'SF-009-ATTEMPT-02',
                'faults': ['HTTP_HOME_DELAY_250MS_60', 'SERVICE_PAUSE_30'],
                'fault_count': 2, 'failed_gate': 'MASTER_TO_WORKER3_AUTH_BEFORE_READY',
                'formal_manifest_sha256': failure['formal_manifest_sha256'],
                'failure_record_sha256': sha(RUN / 'attempt-failure.json'),
                'raw_archive_manifest_sha256': ARCHIVE_SHA,
                'raw_master': str(RUN),
                'raw_worker2': '/data/rca-archives/ops-competition-2026/SF-009-ATTEMPT-02/raw',
                'public_package': 'NOT_CREATED',
                'gt_status': 'NOT_CREATED_FAILED_BEFORE_INJECTION',
                'server_import': 'PROHIBITED_FOR_FAILED_ATTEMPT'})
    ledger['completed_ready_for_import'] = sum(
        item['status'] == 'READY_FOR_IMPORT' for item in ledger['official_cases'])
    ledger['remaining'] = len(ledger['official_cases']) - ledger['completed_ready_for_import']
    if (ledger['completed_ready_for_import'], ledger['remaining']) != (6, 4):
        raise RuntimeError('Pre-injection failure changed READY count')
    ledger['status_changes'].append({
        'case_id': 'SF-009', 'from_status': old['status'],
        'to_status': failure['status'], 'run_id': row['run_id'],
        'at_utc': datetime.now(timezone.utc).isoformat(),
        'previous_ledger_sha256': PRIOR_SHA,
        'failure_record_sha256': row['failure_record_sha256'],
        'raw_archive_manifest_sha256': ARCHIVE_SHA})
    backup = LEDGER.with_name(LEDGER.stem + '.before-sf009-att02-' + PRIOR_SHA[:12] + '.json')
    with backup.open('xb') as stream:
        stream.write(prior)
        stream.flush()
        os.fsync(stream.fileno())
    tmp = LEDGER.with_suffix('.sf009-att02.tmp')
    with tmp.open('x') as stream:
        json.dump(ledger, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, LEDGER)
    print(json.dumps({'status': 'SF009_ATT02_FAILURE_LEDGER_UPDATED',
                      'ready_count': 6, 'remaining': 4,
                      'ledger_sha256': sha(LEDGER)}))


if __name__ == '__main__':
    main()
