"""Close SF-007 ATTEMPT-05 after a verified storage-forecast safety stop."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import os


OPS = Path('/root/ops-competition-2026')
RUN = Path('/data/stage5-v3/ops-competition-2026/runs/SF-007-ATTEMPT-05')
INDEX = Path('/data/stage5-v3/ops-competition-archive-index/SF-007-ATTEMPT-05')
OUTPUT = OPS / 'sf007-finalization-v5-20261018/failure-record.json'
LEDGER = OPS / 'plans/SF-003-to-SF-012-production-v2-ledger.json'
EXPECTED_LEDGER = '3736e5ab32d2ebf5c2286265f19119a796f663e1c3db7e617114769d74ef79c0'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if sha(LEDGER) != EXPECTED_LEDGER or OUTPUT.exists():
        raise RuntimeError('Ledger changed or failure record already exists')
    fault = json.loads((RUN / 'control/fault-controller-terminal.json').read_text())
    receipt = json.loads((INDEX / 'w1-verification.json').read_text())
    residue = json.loads((OPS / 'low-stable-v4-20261017/results/sf007-att05-postabort-residue.json').read_text())
    units = json.loads((OPS / 'low-stable-v4-20261017/results/sf007-att05-postabort-units.json').read_text())
    if (fault['status'] != 'SF007_FORMAL_SEQUENCE_FAILED' or
            len(fault['actual_onsets']) != 1 or
            receipt['status'] != 'COMPETITION_ARCHIVE_SHA_AND_RESTORED_READ_PASS' or
            residue['status'] != 'HOST_RESIDUE_PASS' or
            units['status'] != 'COMPETITION_UNIT_RESIDUE_PASS'):
        raise RuntimeError('Failed attempt has not been safely archived and cleared')
    heartbeat = (INDEX / 'five-host-live.jsonl').read_text().splitlines()
    if not heartbeat:
        raise RuntimeError('Storage heartbeat evidence missing')
    events = [json.loads(line) for line in heartbeat]
    stop = next((row for row in events if row['status'] == 'STOP_NEW_WORK'), None)
    if stop is None or stop['decision']['reason'] != 'SUSTAINED_FORECAST_worker1:data_free_bytes':
        raise RuntimeError('Expected storage stop is not evidenced')
    record = {'run_id': 'SF-007-ATTEMPT-05',
              'status': 'FAILED_STORAGE_FORECAST_SAFETY_STOP',
              'at_utc': datetime.now(timezone.utc).isoformat(),
              'fault_started': True, 'fault_duration_contract_completed': False,
              'capture_complete': False, 'public_bundle_created': False,
              'private_gt_created': False,
              'stop_reason': stop['decision']['reason'],
              'stop_snapshot': stop['snapshot']['worker1'],
              'stop_forecast': stop['forecast']['worker1'],
              'fault_actual_onset': fault['actual_onsets'],
              'fault_terminal_sha256': sha(RUN / 'control/fault-controller-terminal.json'),
              'storage_heartbeat_sha256': sha(INDEX / 'five-host-live.jsonl'),
              'raw_archive_manifest_sha256': sha(INDEX / 'source-manifest.json'),
              'raw_archive_receipt_sha256': sha(INDEX / 'w1-verification.json'),
              'five_host_residue_sha256': sha(OPS / 'low-stable-v4-20261017/results/sf007-att05-postabort-residue.json'),
              'unit_residue_sha256': sha(OPS / 'low-stable-v4-20261017/results/sf007-att05-postabort-units.json'),
              'server_import': 'PROHIBITED_FOR_FAILED_ATTEMPT'}
    with OUTPUT.open('x') as stream:
        json.dump(record, stream, indent=2)
        stream.write('\n')
    ledger = json.loads(LEDGER.read_text())
    case = next(row for row in ledger['official_cases'] if row['case_id'] == 'SF-007')
    if case['status'] != 'FAILED_STORAGE_GATE_SEMANTICS_MISMATCH' or ledger['completed_ready_for_import'] != 7:
        raise RuntimeError('Unexpected official case state')
    old = dict(case)
    history = old.pop('attempt_history', [])
    case.clear()
    case.update({'case_id': 'SF-007', 'status': record['status'],
                 'run_id': record['run_id'], 'faults': ['SERVICE_PAUSE_30'],
                 'fault_count': 1, 'failure_record_sha256': sha(OUTPUT),
                 'formal_manifest_sha256': sha(OPS / 'sf007-real-adapter-v5-20261017/plans/SF-007-ATTEMPT-05/run-manifest.json'),
                 'raw_archive_manifest_sha256': record['raw_archive_manifest_sha256'],
                 'raw_master': str(RUN),
                 'raw_worker1': '/root/ops-competition-2026/failed-attempt-archives/SF-007-ATTEMPT-05/raw',
                 'public_package': 'NOT_CREATED', 'gt_status': 'NOT_CREATED_FAILED_CAPTURE',
                 'server_import': record['server_import'],
                 'attempt_history': history + [old]})
    ledger['status_changes'].append({'case_id': 'SF-007', 'from_status': old['status'],
        'to_status': record['status'], 'run_id': record['run_id'],
        'at_utc': record['at_utc'], 'previous_ledger_sha256': EXPECTED_LEDGER,
        'failure_record_sha256': sha(OUTPUT)})
    with tempfile.NamedTemporaryFile('w', dir=LEDGER.parent, delete=False) as stream:
        json.dump(ledger, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
        temporary = Path(stream.name)
    os.replace(temporary, LEDGER)
    print(json.dumps({'status': record['status'], 'ledger_sha256': sha(LEDGER),
                      'failure_record_sha256': sha(OUTPUT)}))


if __name__ == '__main__':
    main()
