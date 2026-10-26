"""Close the completed but non-admitted SF-011 ATTEMPT-03 without GT."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile


OPS = Path('/root/ops-competition-2026')
RUN_ID = 'SF-011-ATTEMPT-03'
RUN = Path('/data/stage5-v3/ops-competition-2026/runs') / RUN_ID
PACKAGE = Path('/data/stage5-v3/ops-competition-2026/packages') / RUN_ID
INDEX = Path('/data/stage5-v3/ops-competition-archive-index') / RUN_ID
FINAL = OPS / 'sf011-http-finalization-v3-20261018'
LEDGER = OPS / 'plans/SF-003-to-SF-012-production-v2-ledger.json'
EXPECTED_LEDGER = '359c15c5896a14137e78323c92fad9442140d67eeb6432522117632db9d3d46a'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def main():
    record_path = FINAL / 'attempt-failure.json'
    backup_path = LEDGER.parent / 'ledger-before-SF-011-ATTEMPT-03.json'
    if sha(LEDGER) != EXPECTED_LEDGER or record_path.exists() or backup_path.exists():
        raise RuntimeError('Ledger changed or failed Attempt already closed')
    capture = read(RUN / 'terminal.json')
    fault = read(RUN / 'control/fault-controller-terminal.json')
    admission = read(FINAL / 'http-admission.json')
    verified = read(FINAL / 'public-verification.json')
    health = read(FINAL / 'final-health.json')
    raw_receipt = read(INDEX / 'raw-worker3-verification.json')
    output_receipt = read(INDEX / 'rejected-output-worker3-verification.json')
    if (capture['status'] != 'SF011_FORMAL_CAPTURE_PASS_PENDING_EXPORT' or
            not capture['all_chunks_pass'] or
            fault['status'] != 'SF011_FAULT_SEQUENCE_COMPLETE_PENDING_CAPTURE' or
            fault['stages']['http_delay']['delayed_requests'] != 102 or
            admission['status'] != 'SF011_HTTP_HOME_ADMISSION_FAILED' or
            admission['checks']['image_normal_latency'] is not False or
            not all(value for key, value in admission['checks'].items()
                    if key != 'image_normal_latency') or
            verified['status'] != 'PUBLIC_FULL_READBACK_SCHEMA_IDENTITY_HASH_LEAK_PASS' or
            health['status'] != 'SF011_ATT03_FINAL_LIVE_ENVIRONMENT_PASS' or
            any(receipt['status'] != 'COMPETITION_ARCHIVE_SHA_AND_RESTORED_READ_PASS'
                for receipt in (raw_receipt, output_receipt)) or
            (PACKAGE / 'private/anomaly-gt-draft.json').exists()):
        raise RuntimeError('Non-admission evidence or safe archive is incomplete')
    record = {'run_id': RUN_ID, 'status': 'FAILED_ADMISSION',
              'at_utc': datetime.now(timezone.utc).isoformat(),
              'faults_injected': 1, 'faults_recovered': True,
              'capture_complete': True, 'public_integrity_pass': True,
              'admitted': False, 'failed_check': 'image_normal_latency',
              'image_before_p95_ms': admission['public_proxy_span_windows']['image']['before']['p95_ms'],
              'image_during_p95_ms': admission['public_proxy_span_windows']['image']['during']['p95_ms'],
              'image_after_p95_ms': admission['public_proxy_span_windows']['image']['after']['p95_ms'],
              'private_gt_created': False,
              'capture_terminal_sha256': sha(RUN / 'terminal.json'),
              'fault_terminal_sha256': sha(RUN / 'control/fault-controller-terminal.json'),
              'public_manifest_sha256': sha(PACKAGE / 'public/manifest.json'),
              'public_verification_sha256': sha(FINAL / 'public-verification.json'),
              'admission_decision_sha256': sha(FINAL / 'http-admission.json'),
              'raw_archive_manifest_sha256': sha(INDEX / 'raw-source-manifest.json'),
              'rejected_output_archive_manifest_sha256': sha(INDEX / 'rejected-output-source-manifest.json'),
              'server_import': 'PROHIBITED_FOR_FAILED_ATTEMPT'}
    with record_path.open('x') as stream:
        json.dump(record, stream, indent=2)
        stream.write('\n')
    backup_path.write_bytes(LEDGER.read_bytes())
    ledger = read(LEDGER)
    case = next(row for row in ledger['official_cases'] if row['case_id'] == 'SF-011')
    if case['status'] != 'FAILED_ADMISSION' or case['run_id'] != 'SF-011-ATTEMPT-02' or ledger['completed_ready_for_import'] != 7:
        raise RuntimeError('Unexpected official case state')
    old = dict(case)
    history = old.pop('attempt_history', [])
    case.clear()
    case.update({'case_id': 'SF-011', 'status': 'FAILED_ADMISSION',
                 'run_id': RUN_ID, 'faults': ['HTTP_HOME_DELAY_250MS_60'],
                 'fault_count': 1, 'failed_checks': ['image_normal_latency'],
                 'formal_manifest_sha256': sha(OPS / 'sf011-http-adapter-v3-20261018/plans' / RUN_ID / 'run-manifest.json'),
                 'public_manifest_sha256': record['public_manifest_sha256'],
                 'public_verification_sha256': record['public_verification_sha256'],
                 'admission_decision_sha256': record['admission_decision_sha256'],
                 'failure_record_sha256': sha(record_path),
                 'raw_archive_manifest_sha256': record['raw_archive_manifest_sha256'],
                 'rejected_output_archive_manifest_sha256': record['rejected_output_archive_manifest_sha256'],
                 'raw_master': str(RUN), 'rejected_package_master': str(PACKAGE),
                 'raw_and_rejected_package_worker3': '/data/rca-archives/ops-competition-2026/' + RUN_ID,
                 'gt_status': 'NOT_CREATED_FAILED_ADMISSION',
                 'server_import': record['server_import'],
                 'attempt_history': history + [old]})
    ledger['status_changes'].append({'case_id': 'SF-011',
        'from_status': old['status'], 'to_status': case['status'],
        'run_id': RUN_ID, 'at_utc': record['at_utc'],
        'previous_ledger_sha256': EXPECTED_LEDGER,
        'failure_record_sha256': sha(record_path)})
    with tempfile.NamedTemporaryFile('w', dir=LEDGER.parent, delete=False) as stream:
        json.dump(ledger, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
        temporary = Path(stream.name)
    os.replace(temporary, LEDGER)
    print(json.dumps({'status': record['status'],
                      'failure_record_sha256': sha(record_path),
                      'ledger_sha256': sha(LEDGER)}))


if __name__ == '__main__':
    main()
