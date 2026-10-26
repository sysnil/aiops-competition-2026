"""Commit SF-012 ATTEMPT-04 only after independent admission and dual archive."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile


OPS = Path('/root/ops-competition-2026')
RUN_ID = 'SF-012-ATTEMPT-04'
RUN = Path('/data/stage5-v3/ops-competition-2026/runs') / RUN_ID
PACKAGE = Path('/data/stage5-v3/ops-competition-2026/packages') / RUN_ID
INDEX = Path('/data/stage5-v3/ops-competition-archive-index') / RUN_ID
FINAL = OPS / 'sf012-finalization-v4-20261018'
LEDGER = OPS / 'plans/SF-003-to-SF-012-production-v2-ledger.json'
EXPECTED_LEDGER = '38806f92990e2b74b7ddcdfca8d687e2f3d572e843a36d05ff9533cddb384dca'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def main():
    backup = LEDGER.parent / 'ledger-before-SF-012-ATTEMPT-04.json'
    if sha(LEDGER) != EXPECTED_LEDGER or backup.exists():
        raise RuntimeError('Official ledger changed or Attempt already committed')
    capture = read(RUN / 'terminal.json')
    fault = read(RUN / 'control/fault-controller-terminal.json')
    public = read(PACKAGE / 'public/manifest.json')
    verified = read(FINAL / 'public-verification.json')
    admission = read(FINAL / 'admission.json')
    gt = read(PACKAGE / 'private/anomaly-gt-draft.json')
    health = read(FINAL / 'final-health.json')
    residue = read(OPS / 'low-stable-v4-20261017/results/sf012-att04-postarchive-residue.json')
    units = read(OPS / 'low-stable-v4-20261017/results/sf012-att04-postarchive-units.json')
    capacity = read(OPS / 'low-stable-v4-20261017/results/sf012-att04-postarchive-storage.json')
    raw = read(INDEX / 'w1-verification.json')
    outputs = read(INDEX / 'w1-output-verification.json')
    if (capture['status'] != 'SF012_FORMAL_CAPTURE_PASS_PENDING_EXPORT' or
            not capture['all_chunks_pass'] or
            fault['status'] != 'SF012_FAULT_SEQUENCE_COMPLETE_PENDING_CAPTURE' or
            not fault['stages']['pod_recovered'] or
            admission['status'] != 'SF012_POD_MEMORY_ADMISSION_PASS' or
            not all(admission['checks'].values()) or
            public['case_id'] != 'SF-012' or
            public['private_leak_scan'] != 'PASS' or
            verified['status'] != 'PUBLIC_FULL_READBACK_SCHEMA_IDENTITY_HASH_LEAK_PASS' or
            verified['public_manifest_sha256'] != sha(PACKAGE / 'public/manifest.json') or
            not verified['all_30_minutes_nonempty_each_modality'] or
            gt['status'] != 'DRAFT_PENDING_HUMAN_REVIEW' or
            gt['injection_plan_or_gt_read'] is not False or
            gt['source_public_manifest_sha256'] != sha(PACKAGE / 'public/manifest.json') or
            health['status'] != 'SF012_ATT04_FINAL_LIVE_ENVIRONMENT_PASS' or
            health['collector']['refused'] != {'grpc': 0.0, 'http': 0.0} or
            residue['status'] != 'HOST_RESIDUE_PASS' or
            units['status'] != 'COMPETITION_UNIT_RESIDUE_PASS' or
            capacity['one_case']['status'] != 'DYNAMIC_STORAGE_PASS' or
            any(item['status'] != 'COMPETITION_ARCHIVE_SHA_AND_RESTORED_READ_PASS'
                for item in (raw, outputs))):
        raise RuntimeError('A required SF-012 admission, safety, or archive gate failed')
    ledger = read(LEDGER)
    case = next(row for row in ledger['official_cases'] if row['case_id'] == 'SF-012')
    if (ledger['completed_ready_for_import'] != 7 or ledger['remaining'] != 3 or
            case['status'] != 'FAILED_BEFORE_INJECTION_STALE_IDENTITY' or
            case['run_id'] != 'SF-012-ATTEMPT-03'):
        raise RuntimeError('Unexpected official case state')
    old = dict(case)
    history = old.pop('attempt_history', [])
    case.clear()
    case.update({'case_id': 'SF-012', 'status': 'READY_FOR_IMPORT',
                 'run_id': RUN_ID, 'faults': ['POD_MEMORY_32M_300'],
                 'fault_count': 1, 'formal_manifest_sha256': sha(OPS / 'sf012-pod300-adapter-v4-20261018/plans' / RUN_ID / 'run-manifest.json'),
                 'public_manifest_sha256': sha(PACKAGE / 'public/manifest.json'),
                 'public_verification_sha256': sha(FINAL / 'public-verification.json'),
                 'admission_decision_sha256': sha(FINAL / 'admission.json'),
                 'raw_archive_manifest_sha256': sha(INDEX / 'source-manifest.json'),
                 'output_archive_manifest_sha256': sha(INDEX / 'output-manifest.json'),
                 'raw_master': str(RUN), 'package_master': str(PACKAGE),
                 'raw_and_package_worker1': '/data/rca-archives/ops-competition-2026/' + RUN_ID,
                 'row_counts': public['row_counts'],
                 'trace_index_rows': public['trace_index_rows'],
                 'gt_status': gt['status'],
                 'gt_candidates': gt['candidate_count'],
                 'gt_merged_events': gt['merged_event_count'],
                 'gt_draft_sha256': sha(PACKAGE / 'private/anomaly-gt-draft.json'),
                 'server_import': 'PENDING_PUBLIC_IMPORT_NOT_PUBLISHED',
                 'attempt_history': history + [old]})
    ledger['completed_ready_for_import'] = 8
    ledger['remaining'] = 2
    ledger['status_changes'].append({'case_id': 'SF-012',
        'from_status': old['status'], 'to_status': 'READY_FOR_IMPORT',
        'run_id': RUN_ID, 'at_utc': datetime.now(timezone.utc).isoformat(),
        'previous_ledger_sha256': EXPECTED_LEDGER,
        'public_manifest_sha256': case['public_manifest_sha256'],
        'admission_decision_sha256': case['admission_decision_sha256']})
    backup.write_bytes(LEDGER.read_bytes())
    with tempfile.NamedTemporaryFile('w', dir=LEDGER.parent, delete=False) as stream:
        json.dump(ledger, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
        temporary = Path(stream.name)
    os.replace(temporary, LEDGER)
    print(json.dumps({'status': 'READY_FOR_IMPORT',
                      'ready': ledger['completed_ready_for_import'],
                      'remaining': ledger['remaining'],
                      'ledger_sha256': sha(LEDGER),
                      'public_manifest_sha256': case['public_manifest_sha256']}))


if __name__ == '__main__':
    main()
