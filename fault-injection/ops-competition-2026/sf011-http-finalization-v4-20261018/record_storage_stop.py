"""Close SF-011 ATTEMPT-04 after its frozen forecast safety stop."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile


OPS = Path('/root/ops-competition-2026')
RUN_ID = 'SF-011-ATTEMPT-04'
RUN = Path('/data/stage5-v3/ops-competition-2026/runs') / RUN_ID
INDEX = Path('/data/stage5-v3/ops-competition-archive-index') / RUN_ID
FINAL = OPS / 'sf011-http-finalization-v4-20261018'
LEDGER = OPS / 'plans/SF-003-to-SF-012-production-v2-ledger.json'
OUTPUT = FINAL / 'attempt-failure.json'
EXPECTED_LEDGER = '433f6703c21f6ef6e3097343aec1300d89c7511bf65bd8d134d613a1a9341b44'
FLOOR = 5_000_000_000


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def main():
    backup = LEDGER.parent / 'ledger-before-SF-011-ATTEMPT-04.json'
    if sha(LEDGER) != EXPECTED_LEDGER or OUTPUT.exists() or backup.exists():
        raise RuntimeError('Ledger changed or failed Attempt already closed')
    capture = read(RUN / 'terminal.json')
    fault = read(RUN / 'control/fault-controller-terminal.json')
    health = read(FINAL / 'final-health-after-stop.json')
    residue = read(OPS / 'low-stable-v4-20261017/results/sf011-att04-postabort-residue.json')
    units = read(OPS / 'low-stable-v4-20261017/results/sf011-att04-postabort-units.json')
    receipt = read(INDEX / 'w3-verification.json')
    rows = [json.loads(line) for line in (INDEX / 'five-host-live.jsonl').read_text().splitlines()]
    stop = next((row for row in rows if row['status'] == 'STOP_NEW_WORK'), None)
    if stop is None:
        raise RuntimeError('Immutable five-host STOP_NEW_WORK evidence missing')
    margins = {host: stop['snapshot'][host]['data_free_bytes'] -
               stop['forecast'][host]['data_free_bytes'] - FLOOR
               for host in ('master', 'worker1', 'worker2', 'worker3')}
    if (capture['status'] != 'SF011_FORMAL_CAPTURE_FAILED' or
            fault['status'] != 'SF011_FAULT_SEQUENCE_COMPLETE_PENDING_CAPTURE' or
            not fault['stages']['http_recovered'] or
            fault['stages']['http_delay']['delayed_requests'] != 102 or
            margins['worker1'] >= 0 or
            any(value < 0 for host, value in margins.items() if host != 'worker1') or
            health['status'] != 'SF011_ATT04_FINAL_LIVE_ENVIRONMENT_PASS' or
            health['collector']['refused'] != {'grpc': 0.0, 'http': 0.0} or
            residue['status'] != 'HOST_RESIDUE_PASS' or
            units['status'] != 'COMPETITION_UNIT_RESIDUE_PASS' or
            receipt['status'] != 'COMPETITION_ARCHIVE_SHA_AND_RESTORED_READ_PASS' or
            (RUN / 'public').exists()):
        raise RuntimeError('Safety stop, recovery, or raw archive evidence incomplete')
    record = {'run_id': RUN_ID,
              'status': 'FAILED_STORAGE_FORECAST_AFTER_HTTP_RECOVERY',
              'at_utc': datetime.now(timezone.utc).isoformat(),
              'faults_injected': 1, 'faults_recovered': True,
              'capture_complete': False, 'public_bundle_created': False,
              'private_gt_created': False, 'actual_below_five_gb': False,
              'forecast_margin_bytes_by_host': margins,
              'stop_snapshot_worker1': stop['snapshot']['worker1'],
              'stop_forecast_worker1': stop['forecast']['worker1'],
              'stop_at_utc': stop['at_utc'],
              'raw_archive_manifest_sha256': sha(INDEX / 'source-manifest.json'),
              'raw_archive_receipt_sha256': sha(INDEX / 'w3-verification.json'),
              'capture_terminal_sha256': sha(RUN / 'terminal.json'),
              'fault_terminal_sha256': sha(RUN / 'control/fault-controller-terminal.json'),
              'storage_heartbeat_sha256': sha(INDEX / 'five-host-live.jsonl'),
              'five_host_residue_sha256': sha(OPS / 'low-stable-v4-20261017/results/sf011-att04-postabort-residue.json'),
              'unit_residue_sha256': sha(OPS / 'low-stable-v4-20261017/results/sf011-att04-postabort-units.json'),
              'server_import': 'PROHIBITED_FOR_FAILED_ATTEMPT'}
    with OUTPUT.open('x') as stream:
        json.dump(record, stream, indent=2)
        stream.write('\n')
    backup.write_bytes(LEDGER.read_bytes())
    ledger = read(LEDGER)
    case = next(row for row in ledger['official_cases'] if row['case_id'] == 'SF-011')
    if (case['status'] != 'FAILED_ADMISSION' or
            case['run_id'] != 'SF-011-ATTEMPT-03' or
            ledger['completed_ready_for_import'] != 9 or ledger['remaining'] != 1):
        raise RuntimeError('Unexpected official case state')
    old = dict(case)
    history = old.pop('attempt_history', [])
    case.clear()
    case.update({'case_id': 'SF-011', 'status': record['status'],
                 'run_id': RUN_ID, 'faults': ['HTTP_HOME_DELAY_250MS_60'],
                 'fault_count': 1, 'failed_gate': 'WORKER1_FORECAST_BELOW_FIVE_GB',
                 'failure_record_sha256': sha(OUTPUT),
                 'formal_manifest_sha256': sha(OPS / 'sf011-http-adapter-v4-20261018/plans' / RUN_ID / 'run-manifest.json'),
                 'raw_archive_manifest_sha256': record['raw_archive_manifest_sha256'],
                 'raw_master': str(RUN),
                 'raw_worker3': '/data/rca-archives/ops-competition-2026/' + RUN_ID + '/raw',
                 'public_package': 'NOT_CREATED',
                 'gt_status': 'NOT_CREATED_FAILED_CAPTURE',
                 'server_import': record['server_import'],
                 'attempt_history': history + [old]})
    ledger['status_changes'].append({'case_id': 'SF-011',
        'from_status': old['status'], 'to_status': case['status'],
        'run_id': RUN_ID, 'at_utc': record['at_utc'],
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
