"""Commit SF-011 ATTEMPT-04 only after the frozen HTTP admission passes."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile


OPS = Path('/root/ops-competition-2026')
RUN_ID = 'SF-011-ATTEMPT-05'
RUN = Path('/data/stage5-v3/ops-competition-2026/runs') / RUN_ID
PACKAGE = Path('/data/stage5-v3/ops-competition-2026/packages') / RUN_ID
INDEX = Path('/data/stage5-v3/ops-competition-archive-index') / RUN_ID
FINAL = OPS / 'sf011-http-finalization-v5-20261018'
LEDGER = OPS / 'plans/SF-003-to-SF-012-production-v2-ledger.json'
EXPECTED_LEDGER = 'ee8b439931e121e4909c9fab8de5c1f40f3afb8d9ec3ea7a4fc126b2198ceea8'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def main():
    backup = LEDGER.parent / 'ledger-before-SF-011-ATTEMPT-05.json'
    if sha(LEDGER) != EXPECTED_LEDGER or backup.exists():
        raise RuntimeError('Official ledger changed or Attempt already committed')
    capture = read(RUN / 'terminal.json')
    fault = read(RUN / 'control/fault-controller-terminal.json')
    public = read(PACKAGE / 'public/manifest.json')
    verified = read(FINAL / 'public-verification.json')
    admission = read(FINAL / 'http-admission.json')
    gt = read(PACKAGE / 'private/anomaly-gt-draft.json')
    injection = read(PACKAGE / 'private/injection-record.json')
    health = read(FINAL / 'final-health.json')
    residue = read(OPS / 'low-stable-v4-20261017/results/sf011-att05-postarchive-residue.json')
    units = read(OPS / 'low-stable-v4-20261017/results/sf011-att05-postarchive-units.json')
    capacity = read(OPS / 'low-stable-v4-20261017/results/sf011-att05-postarchive-storage.json')
    raw = read(INDEX / 'raw-worker3-verification.json')
    outputs = read(INDEX / 'outputs-worker3-verification.json')
    if (capture['status'] != 'SF011_FORMAL_CAPTURE_PASS_PENDING_EXPORT' or
            not capture['all_chunks_pass'] or
            fault['status'] != 'SF011_FAULT_SEQUENCE_COMPLETE_PENDING_CAPTURE' or
            fault['faults'] or not fault['stages']['http_recovered'] or
            admission['status'] != 'SF011_HTTP_HOME_ADMISSION_PASS' or
            not all(admission['checks'].values()) or
            public['case_id'] != 'SF-011' or public['private_leak_scan'] != 'PASS' or
            verified['status'] != 'PUBLIC_FULL_READBACK_SCHEMA_IDENTITY_HASH_LEAK_PASS' or
            verified['public_manifest_sha256'] != sha(PACKAGE / 'public/manifest.json') or
            not verified['all_30_minutes_nonempty_each_modality'] or
            gt['status'] != 'DRAFT_PENDING_HUMAN_REVIEW' or
            gt['injection_plan_or_gt_read'] is not False or
            gt['source_public_manifest_sha256'] != sha(PACKAGE / 'public/manifest.json') or
            injection['ready_for_import'] is not True or
            health['status'] != 'SF011_ATT05_FINAL_LIVE_ENVIRONMENT_PASS' or
            health['collector']['refused'] != {'grpc': 0.0, 'http': 0.0} or
            residue['status'] != 'HOST_RESIDUE_PASS' or
            units['status'] != 'COMPETITION_UNIT_RESIDUE_PASS' or
            capacity['one_case']['status'] != 'DYNAMIC_STORAGE_PASS' or
            any(item['status'] != 'COMPETITION_ARCHIVE_SHA_AND_RESTORED_READ_PASS'
                for item in (raw, outputs))):
        raise RuntimeError('A required SF-011 admission, safety, or archive gate failed')
    ledger = read(LEDGER)
    case = next(row for row in ledger['official_cases'] if row['case_id'] == 'SF-011')
    if (ledger['completed_ready_for_import'] != 9 or ledger['remaining'] != 1 or
            case['status'] != 'FAILED_STORAGE_FORECAST_AFTER_HTTP_RECOVERY' or
            case['run_id'] != 'SF-011-ATTEMPT-04'):
        raise RuntimeError('Unexpected official case state')
    old = dict(case)
    history = old.pop('attempt_history', [])
    case.clear()
    case.update({'case_id': 'SF-011', 'status': 'READY_FOR_IMPORT',
                 'run_id': RUN_ID, 'faults': ['HTTP_HOME_DELAY_250MS_60'],
                 'fault_count': 1, 'formal_manifest_sha256': sha(OPS / 'sf011-http-adapter-v5-20261018/plans' / RUN_ID / 'run-manifest.json'),
                 'public_manifest_sha256': sha(PACKAGE / 'public/manifest.json'),
                 'public_verification_sha256': sha(FINAL / 'public-verification.json'),
                 'admission_decision_sha256': sha(FINAL / 'http-admission.json'),
                 'raw_archive_manifest_sha256': sha(INDEX / 'raw-source-manifest.json'),
                 'output_archive_manifest_sha256': sha(INDEX / 'outputs-source-manifest.json'),
                 'raw_master': str(RUN), 'package_master': str(PACKAGE),
                 'raw_and_package_worker3': '/data/rca-archives/ops-competition-2026/' + RUN_ID,
                 'row_counts': public['row_counts'],
                 'trace_index_rows': public['trace_index_rows'],
                 'gt_status': gt['status'],
                 'gt_candidates': gt['candidate_count'],
                 'gt_merged_events': gt['merged_event_count'],
                 'gt_draft_sha256': sha(PACKAGE / 'private/anomaly-gt-draft.json'),
                 'server_import': 'PENDING_PUBLIC_IMPORT_NOT_PUBLISHED',
                 'attempt_history': history + [old]})
    ledger['completed_ready_for_import'] = 10
    ledger['remaining'] = 0
    ledger['status_changes'].append({'case_id': 'SF-011',
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
