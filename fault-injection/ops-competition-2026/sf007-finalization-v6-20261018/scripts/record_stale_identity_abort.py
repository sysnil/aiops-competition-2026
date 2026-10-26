"""Close SF-007 ATTEMPT-06 before injection after stale identity rejection."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile


OPS = Path('/root/ops-competition-2026')
RUN = Path('/data/stage5-v3/ops-competition-2026/runs/SF-007-ATTEMPT-06')
INDEX = Path('/data/stage5-v3/ops-competition-archive-index/SF-007-ATTEMPT-06')
FINAL = OPS / 'sf007-finalization-v6-20261018'
OUTPUT = FINAL / 'failure-record.json'
LEDGER = OPS / 'plans/SF-003-to-SF-012-production-v2-ledger.json'
EXPECTED_LEDGER = '7af5c38ea0c9f0beb59f77ac7881cd41c21a5e595396ddc38556548cb7944b33'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def main():
    backup = LEDGER.parent / 'ledger-before-SF-007-ATTEMPT-06.json'
    if sha(LEDGER) != EXPECTED_LEDGER or OUTPUT.exists() or backup.exists():
        raise RuntimeError('Ledger changed or failed Attempt already closed')
    fault = read(RUN / 'control/fault-controller-terminal.json')
    receipt = read(INDEX / 'w1-verification.json')
    residue = read(OPS / 'low-stable-v4-20261017/results/sf007-att06-postabort-residue.json')
    units = read(OPS / 'low-stable-v4-20261017/results/sf007-att06-postabort-units.json')
    if (fault['status'] != 'SF007_FORMAL_SEQUENCE_FAILED' or
            fault['actual_onsets'] or (RUN / 'terminal.json').exists() or
            receipt['status'] != 'COMPETITION_ARCHIVE_SHA_AND_RESTORED_READ_PASS' or
            residue['status'] != 'HOST_RESIDUE_PASS' or
            units['status'] != 'COMPETITION_UNIT_RESIDUE_PASS'):
        raise RuntimeError('Pre-injection failure, archive, or cleanup evidence incomplete')
    record = {'run_id': 'SF-007-ATTEMPT-06',
              'status': 'FAILED_BEFORE_INJECTION_STALE_IDENTITY',
              'at_utc': datetime.now(timezone.utc).isoformat(),
              'faults_injected': 0, 'capture_complete': False,
              'public_bundle_created': False, 'private_gt_created': False,
              'capture_failure': 'Fresh current target inspection required',
              'capture_failure_source': '/root/ops-competition-2026/sf007-formal-v6-20261018/scripts/capture_controller.py:116',
              'worker_timer_cancelled': True,
              'fault_terminal_sha256': sha(RUN / 'control/fault-controller-terminal.json'),
              'raw_archive_manifest_sha256': sha(INDEX / 'source-manifest.json'),
              'raw_archive_receipt_sha256': sha(INDEX / 'w1-verification.json'),
              'five_host_residue_sha256': sha(OPS / 'low-stable-v4-20261017/results/sf007-att06-postabort-residue.json'),
              'unit_residue_sha256': sha(OPS / 'low-stable-v4-20261017/results/sf007-att06-postabort-units.json'),
              'server_import': 'PROHIBITED_FOR_FAILED_ATTEMPT'}
    with OUTPUT.open('x') as stream:
        json.dump(record, stream, indent=2)
        stream.write('\n')
    backup.write_bytes(LEDGER.read_bytes())
    ledger = read(LEDGER)
    case = next(row for row in ledger['official_cases'] if row['case_id'] == 'SF-007')
    if (case['status'] != 'FAILED_STORAGE_FORECAST_SAFETY_STOP' or
            case['run_id'] != 'SF-007-ATTEMPT-05' or
            ledger['completed_ready_for_import'] != 7):
        raise RuntimeError('Unexpected official case state')
    old = dict(case)
    history = old.pop('attempt_history', [])
    case.clear()
    case.update({'case_id': 'SF-007', 'status': record['status'],
                 'run_id': record['run_id'], 'faults': ['SERVICE_PAUSE_30'],
                 'fault_count': 1, 'faults_injected': 0,
                 'failed_gate': 'CAPTURE_IDENTITY_SNAPSHOT_EXPIRED_BEFORE_T0',
                 'failure_record_sha256': sha(OUTPUT),
                 'formal_manifest_sha256': sha(OPS / 'sf007-real-adapter-v6-20261018/plans/SF-007-ATTEMPT-06/run-manifest.json'),
                 'raw_archive_manifest_sha256': record['raw_archive_manifest_sha256'],
                 'raw_master': str(RUN),
                 'raw_worker1': '/data/rca-archives/ops-competition-2026/SF-007-ATTEMPT-06/raw',
                 'public_package': 'NOT_CREATED',
                 'gt_status': 'NOT_CREATED_BEFORE_INJECTION',
                 'server_import': record['server_import'],
                 'attempt_history': history + [old]})
    ledger['status_changes'].append({'case_id': 'SF-007',
        'from_status': old['status'], 'to_status': case['status'],
        'run_id': record['run_id'], 'at_utc': record['at_utc'],
        'previous_ledger_sha256': EXPECTED_LEDGER,
        'failure_record_sha256': sha(OUTPUT)})
    with tempfile.NamedTemporaryFile('w', dir=LEDGER.parent, delete=False) as stream:
        json.dump(ledger, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
        temporary = Path(stream.name)
    os.replace(temporary, LEDGER)
    print(json.dumps({'status': record['status'],
                      'failure_record_sha256': sha(OUTPUT),
                      'ledger_sha256': sha(LEDGER)}))


if __name__ == '__main__':
    main()
