"""Close SF-012 ATTEMPT-02 after its frozen exporter rejected a boundary log."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import os


OPS = Path('/root/ops-competition-2026')
RUN = Path('/data/stage5-v3/ops-competition-2026/runs/SF-012-ATTEMPT-02')
INDEX = Path('/data/stage5-v3/ops-competition-archive-index/SF-012-ATTEMPT-02')
OUTPUT = OPS / 'sf012-finalization-v1-20261018/failure-record.json'
LEDGER = OPS / 'plans/SF-003-to-SF-012-production-v2-ledger.json'
EXPECTED_LEDGER = '694937cb16458c7c5d3c962467def53d515b3579ea39d4adbb3481223695c431'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if sha(LEDGER) != EXPECTED_LEDGER or OUTPUT.exists():
        raise RuntimeError('Ledger changed or failure record already exists')
    terminal = json.loads((RUN / 'terminal.json').read_text())
    receipt = json.loads((INDEX / 'w1-verification.json').read_text())
    if (terminal['status'] != 'SF012_FORMAL_CAPTURE_PASS_PENDING_EXPORT' or
            receipt['status'] != 'COMPETITION_ARCHIVE_SHA_AND_RESTORED_READ_PASS'):
        raise RuntimeError('Capture or raw archive is not verified')
    record = {
        'run_id': 'SF-012-ATTEMPT-02',
        'status': 'FAILED_PUBLIC_EXPORT_BOUNDARY',
        'at_utc': datetime.now(timezone.utc).isoformat(),
        'faults_injected': 1,
        'faults_recovered': True,
        'capture_complete': True,
        'public_bundle_created': False,
        'private_gt_created': False,
        'reason': 'Frozen exporter rejected one OpenSearch log timestamp 202427 ns before T0',
        'source_log_index': 'otel-logs-2026-10-18',
        'source_log_id': 'KXSTTaEBeYeCCoAm-tv-',
        'source_log_timestamp': '2026-10-18T05:55:05.388100573Z',
        'source_capture_chunk': 'chunk-00',
        'capture_terminal_sha256': sha(RUN / 'terminal.json'),
        'raw_archive_manifest_sha256': sha(INDEX / 'source-manifest.json'),
        'raw_archive_receipt_sha256': sha(INDEX / 'w1-verification.json'),
        'frozen_exporter_sha256': sha(OPS / 'sf012-pod300-adapter-v2-20261018/scripts/sf003_export.py'),
        'prohibited_actions': ['IMPORT', 'GT_APPROVAL', 'RETROACTIVE_EXPORTER_CHANGE'],
    }
    with OUTPUT.open('x') as stream:
        json.dump(record, stream, indent=2)
        stream.write('\n')
    ledger = json.loads(LEDGER.read_text())
    case = next(row for row in ledger['official_cases'] if row['case_id'] == 'SF-012')
    if case['status'] != 'NOT_STARTED' or ledger['completed_ready_for_import'] != 7:
        raise RuntimeError('Unexpected official case state')
    old = dict(case)
    case.clear()
    case.update({'case_id': 'SF-012', 'status': record['status'],
                 'run_id': record['run_id'], 'faults': ['POD_MEMORY_32M_300'],
                 'fault_count': 1, 'failure_record_sha256': sha(OUTPUT),
                 'formal_manifest_sha256': sha(OPS / 'sf012-pod300-adapter-v2-20261018/plans/SF-012-ATTEMPT-02/run-manifest.json'),
                 'capture_terminal_sha256': record['capture_terminal_sha256'],
                 'raw_archive_manifest_sha256': record['raw_archive_manifest_sha256'],
                 'raw_master': str(RUN),
                 'raw_worker1': '/data/rca-archives/ops-competition-2026/SF-012-ATTEMPT-02/raw',
                 'gt_status': 'NOT_CREATED_FAILED_EXPORT',
                 'server_import': 'PROHIBITED_FOR_FAILED_ATTEMPT',
                 'attempt_history': [old]})
    ledger['status_changes'].append({'case_id': 'SF-012', 'from_status': old['status'],
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
