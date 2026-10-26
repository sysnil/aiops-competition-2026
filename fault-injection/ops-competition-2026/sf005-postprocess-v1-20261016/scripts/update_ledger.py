#!/usr/bin/env python3
"""Append SF-005's verified new attempt to the mutable production ledger."""

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile


OPS = Path('/root/ops-competition-2026')
DATA = Path('/data/stage5-v3/ops-competition-2026')
RUN_ID = 'SF-005-ATTEMPT-04'
LEDGER = OPS / 'plans/SF-003-to-SF-012-production-v2-ledger.json'
RUN = DATA / 'runs' / RUN_ID
PACKAGE = DATA / 'packages' / RUN_ID
INDEX = Path('/data/stage5-v3/ops-competition-archive-index') / RUN_ID
REAL = OPS / 'sf005-real-adapter-v4-20261016'
EXPECTED_LEDGER_SHA = '03fb9cb15f6c16cd9a7388895e53f1bffbbaf1cd033b439a69916d164ecb95cf'


def sha(path):
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024**2), b''):
            checksum.update(block)
    return checksum.hexdigest()


def read(path):
    return json.loads(path.read_text())


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


if __name__ == '__main__':
    require(sha(LEDGER) == EXPECTED_LEDGER_SHA, 'Ledger changed before update')
    ledger = read(LEDGER)
    cases = ledger['official_cases']
    rows = [row for row in cases if row['case_id'] == 'SF-005']
    require(len(rows) == 1 and rows[0]['status'] == 'FAILED_CONTROL_COVERAGE',
            'SF-005 previous state differs')
    capture = read(RUN / 'terminal.json')
    fault = read(RUN / 'control/fault-controller-terminal.json')
    controls = read(RUN / 'controls/result-manifest.json')['streams']
    admission = read(PACKAGE / 'private/admission-decision.json')
    public = read(REAL.parent / 'sf005-postprocess-v1-20261016/results/public-verification.json')
    gt = read(PACKAGE / 'private/anomaly-gt-draft.json')
    raw_manifest = INDEX / 'source-manifest.json'
    output_manifest = INDEX / 'output-manifest.json'
    receipts = {kind: [read(INDEX / (host + suffix)) for host in ('w2', 'w3')]
                for kind, suffix in (('raw', '-verification.json'),
                                     ('outputs', '-output-verification.json'))}
    residue = read(REAL / 'final-attempt04-residue.json')
    storage = read(REAL / 'final-attempt04-storage.json')
    pre_failed = read(DATA / 'runs/SF-005-ATTEMPT-03/control/fault-controller-terminal.json')
    require(capture['status'] == 'SF005_FORMAL_CAPTURE_PASS_PENDING_EXPORT', 'Capture incomplete')
    require(fault['status'] == 'SF005_FAULT_SEQUENCE_COMPLETE_PENDING_CAPTURE' and
            len(fault['faults']) == 1 and fault['stages'].get('service_recovered') is True,
            'Real fault or recovery missing')
    require(all(controls[name]['planned_requests'] == expected and
                controls[name]['successful_requests'] == expected and
                controls[name]['missed_arrivals'] == 0
                for name,expected in (('image_asset',1800),('cart_view',3600))),
            'Fixed controls incomplete')
    require(admission['status'] == 'SF005_SERVICE_ADMISSION_PASS' and
            all(admission['checks'].values()), 'Formal admission failed')
    require(public['status'] == 'PUBLIC_PACKAGE_VERIFIED' and
            public['private_leak_scan'] == 'PASS' and public['file_count'] == 6,
            'Public package failed independent verification')
    require(gt['status'] == 'DRAFT_PENDING_HUMAN_REVIEW' and
            gt['formal_scoring_gt'] is False, 'Private GT review status changed')
    require(residue['status'] == 'HOST_RESIDUE_PASS' and
            storage['one_case']['status'] == 'DYNAMIC_STORAGE_PASS',
            'Final five-host safety or storage failed')
    require(pre_failed['status'] == 'SF005_FORMAL_SEQUENCE_FAILED' and
            'Permission denied' in pre_failed['error'] and
            not pre_failed.get('worker_cleanup_requested'),
            'ATTEMPT-03 failure history differs')
    for kind, manifest in (('raw', raw_manifest), ('outputs', output_manifest)):
        source = read(manifest)
        require(source['file_count'] > 0 and source['bytes'] > 0,
                kind + ' source manifest empty')
        for receipt in receipts[kind]:
            require(receipt['status'] == 'COMPETITION_ARCHIVE_SHA_AND_RESTORED_READ_PASS' and
                    receipt['source_manifest_sha256'] == sha(manifest) and
                    receipt['bytes'] == source['bytes'],
                    kind + ' worker SHA or readback failed')
    before = copy.deepcopy(rows[0])
    updated = {
        'case_id': 'SF-005', 'status': 'READY_FOR_IMPORT', 'run_id': RUN_ID,
        'faults': ['SERVICE_PAUSE_30'], 'fault_count': 1,
        'formal_manifest_sha256': sha(REAL / 'plans' / RUN_ID / 'run-manifest.json'),
        'public_manifest_sha256': public['manifest_sha256'],
        'raw_archive_manifest_sha256': sha(raw_manifest),
        'output_archive_manifest_sha256': sha(output_manifest),
        'admission_decision_sha256': sha(PACKAGE / 'private/admission-decision.json'),
        'admission': admission['status'],
        'raw_master': str(RUN), 'package_master': str(PACKAGE),
        'raw_and_package_worker2': '/data/rca-archives/ops-competition-2026/' + RUN_ID,
        'raw_and_package_worker3': '/data/rca-archives/ops-competition-2026/' + RUN_ID,
        'row_counts': public['row_counts'], 'trace_index_rows': public['trace_index_rows'],
        'window': {'start_utc': read(RUN / 'schedule.json')['start_utc'],
                   'end_utc': read(RUN / 'schedule.json')['end_utc']},
        'gt_status': 'DRAFT_PENDING_HUMAN_REVIEW',
        'gt_candidates': gt['candidate_count'],
        'gt_merged_events': len(gt['merged_events']),
        'server_import': 'NOT_EXECUTED',
        'attempt_history': [before, {
            'run_id': 'SF-005-ATTEMPT-03',
            'status': 'FAILED_BEFORE_INJECTION',
            'terminal_sha256': sha(DATA / 'runs/SF-005-ATTEMPT-03/control/fault-controller-terminal.json'),
            'reason': 'Master Worker3 SSH control socket absent; no fault timer armed',
        }],
    }
    index = cases.index(rows[0])
    cases[index] = updated
    ledger['completed_ready_for_import'] = sum(row['status'] == 'READY_FOR_IMPORT' for row in cases)
    ledger['remaining'] = len(cases) - ledger['completed_ready_for_import']
    ledger.setdefault('status_changes', []).append({
        'case_id': 'SF-005', 'from_status': before['status'],
        'to_status': updated['status'], 'run_id': RUN_ID,
        'at_utc': datetime.now(timezone.utc).isoformat(),
        'previous_ledger_sha256': EXPECTED_LEDGER_SHA,
        'evidence_public_manifest_sha256': public['manifest_sha256'],
        'evidence_admission_sha256': updated['admission_decision_sha256'],
    })
    backup = OPS / 'plans/history' / ('SF-003-to-SF-012-v2-ledger-before-' + RUN_ID + '.json')
    backup.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with backup.open('xb') as stream:
        stream.write(LEDGER.read_bytes())
    backup.chmod(0o600)
    require(sha(backup) == EXPECTED_LEDGER_SHA and sha(LEDGER) == EXPECTED_LEDGER_SHA,
            'Ledger changed while creating backup')
    with tempfile.NamedTemporaryFile('w', dir=LEDGER.parent, delete=False,
                                     prefix='.sf005-ledger-', suffix='.json') as stream:
        json.dump(ledger, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
        pending = Path(stream.name)
    pending.chmod(0o600)
    require(sha(LEDGER) == EXPECTED_LEDGER_SHA, 'Ledger changed before atomic replace')
    os.replace(pending, LEDGER)
    print(json.dumps({'status': 'LEDGER_UPDATED',
                      'ready': ledger['completed_ready_for_import'],
                      'remaining': ledger['remaining'],
                      'ledger_sha256': sha(LEDGER),
                      'backup_sha256': sha(backup)}))
