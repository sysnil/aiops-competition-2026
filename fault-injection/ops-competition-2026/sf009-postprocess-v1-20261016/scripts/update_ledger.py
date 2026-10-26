#!/usr/bin/env python3
"""Record SF-009 as a preserved failed admission attempt."""

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile


OPS = Path('/root/ops-competition-2026')
DATA = Path('/data/stage5-v3/ops-competition-2026')
RUN_ID = 'SF-009-ATTEMPT-01'
LEDGER = OPS / 'plans/SF-003-to-SF-012-production-v2-ledger.json'
RUN = DATA / 'runs' / RUN_ID
PACKAGE = DATA / 'packages' / RUN_ID
INDEX = Path('/data/stage5-v3/ops-competition-archive-index') / RUN_ID
REAL = OPS / 'sf009-real-adapter-v2-20261016'
POST = OPS / 'sf009-postprocess-v1-20261016'
EXPECTED_SHA = 'd0e948bd8b3d19ff1d4380b3d5d50eeb12dde0bf7dc4253ddba6b89cdeb5296e'


def sha(path):
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024**2), b''):
            checksum.update(block)
    return checksum.hexdigest()


def read(path):
    return json.loads(path.read_text())


def require(condition, reason):
    if not condition:
        raise RuntimeError(reason)


if __name__ == '__main__':
    require(sha(LEDGER) == EXPECTED_SHA, 'Ledger changed')
    ledger = read(LEDGER)
    matching = [row for row in ledger['official_cases'] if row['case_id'] == 'SF-009']
    require(len(matching) == 1 and
            matching[0]['status'] == 'PRE_FORMAL_BLOCKED_COLLECTOR_REFUSAL',
            'Previous SF-009 status differs')
    public = read(POST / 'results/public-verification.json')
    admission = read(PACKAGE / 'private/admission-decision.json')
    capture = read(RUN / 'terminal.json')
    fault = read(RUN / 'control/fault-controller-terminal.json')
    controls = read(RUN / 'controls/result-manifest.json')['streams']
    residue = read(REAL / 'final-attempt01-residue.json')
    storage = read(REAL / 'final-attempt01-storage.json')
    failed = sorted(key for key,value in admission['checks'].items() if not value)
    require(admission['status'] == 'SF009_REVERSE_ADMISSION_FAILED' and
            failed == ['service_caller_failures', 'service_image_latency'],
            'Admission failure reasons differ')
    require(public['status'] == 'PUBLIC_PACKAGE_VERIFIED' and
            public['private_leak_scan'] == 'PASS' and public['file_count'] == 6,
            'Public failed independent verification')
    require(capture['status'] == 'SF009_FORMAL_CAPTURE_PASS_PENDING_EXPORT' and
            fault['status'] == 'SF009_FAULT_SEQUENCE_COMPLETE_PENDING_CAPTURE' and
            len(fault['faults']) == 1 and fault['stages']['http_recovered'] is True and
            fault['stages']['service_recovered'] is True,
            'Execution or recovery differs')
    require(all(controls[name]['planned_requests'] == count and
                controls[name]['successful_requests'] == count and
                controls[name]['missed_arrivals'] == 0
                for name,count in (('image_asset',1800),('cart_view',3600))),
            'Normal controls incomplete')
    require(residue['status'] == 'HOST_RESIDUE_PASS' and
            storage['one_case']['status'] == 'DYNAMIC_STORAGE_PASS',
            'Final host safety or storage failed')
    require(not (PACKAGE / 'private/anomaly-gt-draft.json').exists(),
            'Failed attempt unexpectedly has GT draft')
    for kind, manifest in (('raw', INDEX / 'source-manifest.json'),
                           ('outputs', INDEX / 'output-manifest.json')):
        source = read(manifest)
        require(source['file_count'] > 0 and source['bytes'] > 0,
                kind + ' source manifest missing')
        suffix = '-verification.json' if kind == 'raw' else '-output-verification.json'
        for host in ('w2', 'w3'):
            receipt = read(INDEX / (host + suffix))
            require(receipt['status'] == 'COMPETITION_ARCHIVE_SHA_AND_RESTORED_READ_PASS' and
                    receipt['source_manifest_sha256'] == sha(manifest) and
                    receipt['bytes'] == source['bytes'],
                    kind + ' archive verification failed on ' + host)
    before = copy.deepcopy(matching[0])
    updated = {
        'case_id': 'SF-009', 'status': 'FAILED_ADMISSION', 'run_id': RUN_ID,
        'faults': ['HTTP_HOME_DELAY_250MS_60', 'SERVICE_PAUSE_30'],
        'fault_count': 2, 'failed_gates': failed,
        'formal_manifest_sha256': sha(REAL / 'plans' / RUN_ID / 'run-manifest.json'),
        'public_manifest_sha256': public['manifest_sha256'],
        'raw_archive_manifest_sha256': sha(INDEX / 'source-manifest.json'),
        'output_archive_manifest_sha256': sha(INDEX / 'output-manifest.json'),
        'admission_decision_sha256': sha(PACKAGE / 'private/admission-decision.json'),
        'raw_master': str(RUN), 'package_master': str(PACKAGE),
        'raw_and_package_worker2': '/data/rca-archives/ops-competition-2026/' + RUN_ID,
        'raw_and_package_worker3': '/data/rca-archives/ops-competition-2026/' + RUN_ID,
        'row_counts': public['row_counts'], 'trace_index_rows': public['trace_index_rows'],
        'gt_status': 'NOT_CREATED_FAILED_ADMISSION',
        'server_import': 'PROHIBITED_FOR_FAILED_ATTEMPT',
        'attempt_history': [before],
    }
    ledger['official_cases'][ledger['official_cases'].index(matching[0])] = updated
    ledger['completed_ready_for_import'] = sum(
        row['status'] == 'READY_FOR_IMPORT' for row in ledger['official_cases'])
    ledger['remaining'] = len(ledger['official_cases']) - ledger['completed_ready_for_import']
    ledger.setdefault('status_changes', []).append({
        'case_id': 'SF-009', 'from_status': before['status'],
        'to_status': updated['status'], 'run_id': RUN_ID,
        'at_utc': datetime.now(timezone.utc).isoformat(),
        'previous_ledger_sha256': EXPECTED_SHA,
        'admission_sha256': updated['admission_decision_sha256'],
    })
    backup = OPS / 'plans/history' / ('SF-003-to-SF-012-v2-ledger-before-' + RUN_ID + '.json')
    backup.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with backup.open('xb') as stream:
        stream.write(LEDGER.read_bytes())
    backup.chmod(0o600)
    require(sha(backup) == EXPECTED_SHA and sha(LEDGER) == EXPECTED_SHA,
            'Ledger changed while backing up')
    with tempfile.NamedTemporaryFile('w', dir=LEDGER.parent, delete=False,
                                     prefix='.sf009-ledger-', suffix='.json') as stream:
        json.dump(ledger, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
        pending = Path(stream.name)
    pending.chmod(0o600)
    require(sha(LEDGER) == EXPECTED_SHA, 'Ledger changed before atomic replace')
    os.replace(pending, LEDGER)
    print(json.dumps({'status': 'FAILED_ATTEMPT_RECORDED',
                      'ready': ledger['completed_ready_for_import'],
                      'remaining': ledger['remaining'],
                      'failed_gates': failed,
                      'ledger_sha256': sha(LEDGER)}))
