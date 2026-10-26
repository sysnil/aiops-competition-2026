"""CAS-update the production ledger after independent SF-004 acceptance."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
DATA = Path('/data/stage5-v3/ops-competition-2026')
INDEX = Path('/data/stage5-v3/ops-competition-archive-index')
LEDGER = OPS / 'plans/SF-003-to-SF-012-production-v2-ledger.json'
EXPECTED_LEDGER_SHA = 'bcaf9863423554d8052bba906221c940b52f18ad998935887cde4a2d79ebd82c'
RUN_ID = 'SF-004-ATTEMPT-05'
PUBLIC = DATA / 'packages' / RUN_ID / 'public'
EVIDENCE = {
    'formal_manifest_sha256': (OPS / 'sf004-real-adapter-v6-20261017/plans' / RUN_ID / 'run-manifest.json',
        'f8447655607abb8e99946d1487ba49f02ac2c41940f65cd8d7414446cd9f6df0'),
    'public_manifest_sha256': (PUBLIC / 'manifest.json',
        '7074bed09aaae6f5828f8e37e8c03aad0b6dc9caa0188f386867649c9047b242'),
    'admission_decision_sha256': (OPS / 'sf004-finalization-v6-20261017/http-admission.json',
        'e8bb046e2afdbacf3454ae7e9fc53146ef4c35528c6cc4de1fa1653bc6de1377'),
    'raw_archive_manifest_sha256': (INDEX / RUN_ID / 'raw-source-manifest.json',
        '93e826ecfa10f47594f1182550c548d009ae6cea2d77ab41af387b235f3d0841'),
    'output_archive_manifest_sha256': (INDEX / RUN_ID / 'outputs-source-manifest.json',
        'dc29dceee55b0aa09ac3cc11ba87794d986cf42ec47a09d8703f611f843abc66'),
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def accepted():
    for name, (path, expected) in EVIDENCE.items():
        if sha(path) != expected:
            raise RuntimeError('Accepted evidence changed: ' + name)
    public = read(PUBLIC / 'manifest.json')
    if ({path.name for path in PUBLIC.iterdir()} != {
            'metrics.parquet', 'logs.parquet', 'traces.parquet',
            'trace_index.parquet', 'topology.json', 'manifest.json'} or
            public['case_id'] != 'SF-004'):
        raise RuntimeError('Incomplete Public file set or wrong Case')
    for name, entry in public['files'].items():
        if sha(PUBLIC / name) != entry['sha256']:
            raise RuntimeError('Public file hash mismatch: ' + name)
    verify = read(OPS / 'sf004-finalization-v6-20261017/public-verification.json')
    admission = read(EVIDENCE['admission_decision_sha256'][0])
    gt = read(DATA / 'packages' / RUN_ID / 'private/anomaly-gt-draft.json')
    capture = read(DATA / 'runs' / RUN_ID / 'terminal.json')
    fault = read(DATA / 'runs' / RUN_ID / 'control/fault-controller-terminal.json')
    health = read(OPS / 'low-stable-v4-20261017/results/sf004-attempt05-final-live-health.json')
    residue = read(OPS / 'low-stable-v4-20261017/results/sf004-attempt05-final-residue.json')
    units = read(OPS / 'low-stable-v4-20261017/results/sf004-attempt05-final-units.json')
    receipts = [read(INDEX / RUN_ID /
                     (kind + '-worker3-verification.json')) for kind in ('raw', 'outputs')]
    if (verify['status'] != 'PUBLIC_FULL_READBACK_SCHEMA_IDENTITY_HASH_LEAK_PASS' or
            verify['public_manifest_sha256'] != sha(PUBLIC / 'manifest.json') or
            admission['status'] != 'SF004_HTTP_HOME_ADMISSION_PASS' or
            not all(admission['checks'].values()) or
            gt['status'] != 'DRAFT_PENDING_HUMAN_REVIEW' or
            gt['formal_scoring_gt'] is not False or
            capture['status'] != 'SF004_FORMAL_CAPTURE_PASS_PENDING_EXPORT' or
            capture['all_chunks_pass'] is not True or
            fault['status'] != 'SF004_FAULT_SEQUENCE_COMPLETE_PENDING_CAPTURE' or
            fault['stages'].get('http_recovered') is not True or
            health['status'] != 'FINAL_LIVE_ENVIRONMENT_PASS' or
            residue['status'] != 'HOST_RESIDUE_PASS' or
            units['status'] != 'COMPETITION_UNIT_RESIDUE_PASS' or
            any(row['status'] != 'COMPETITION_ARCHIVE_SHA_AND_RESTORED_READ_PASS'
                for row in receipts)):
        raise RuntimeError('SF-004 final acceptance evidence is incomplete')
    history = [
        {'run_id': 'SF-004-ATTEMPT-02', 'status': 'FAILED_BEFORE_INJECTION',
         'capture_terminal_sha256': '3b858c34fa5d897ede6a0dff6bf6bdf0e2dc7224b4bb27169b8fe35aadadaf45',
         'fault_injected': False},
        {'run_id': 'SF-004-ATTEMPT-03', 'status': 'FAILED_EXPORT_BOUNDARY',
         'failure_record_sha256': 'c2530e3f4d2bc99e7758eead3a014d7532d579a641f0054f162c96dcb0597429',
         'fault_injected': True},
        {'run_id': 'SF-004-ATTEMPT-04', 'status': 'FAILED_BEFORE_INJECTION_HEARTBEAT_TRANSPORT',
         'failure_record_sha256': '3b297563c1122f91cea6810fa9f94191db7999e8c1161d5c098e6a136fad4813',
         'fault_injected': False},
    ]
    paths = [DATA / 'runs/SF-004-ATTEMPT-02/terminal.json',
             OPS / 'low-stable-v4-20261017/results/sf004-attempt03-export-failure.json',
             OPS / 'low-stable-v4-20261017/results/sf004-attempt04-heartbeat-failure.json']
    for entry, path in zip(history, paths):
        expected = entry.get('capture_terminal_sha256', entry.get('failure_record_sha256'))
        if sha(path) != expected:
            raise RuntimeError('Prior attempt evidence changed: ' + entry['run_id'])
    return gt['candidate_count'], history


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    prior = LEDGER.read_bytes()
    previous_sha = hashlib.sha256(prior).hexdigest()
    if previous_sha != EXPECTED_LEDGER_SHA:
        raise RuntimeError('Ledger changed; refusing blind overwrite')
    candidate_count, failures = accepted()
    ledger = json.loads(prior)
    matches = [row for row in ledger['official_cases'] if row['case_id'] == 'SF-004']
    if len(matches) != 1 or matches[0]['status'] != 'FAILED_OBSERVABILITY_CONTROL':
        raise RuntimeError('Original SF-004 ledger state changed')
    case = matches[0]
    previous_case = dict(case)
    case.clear()
    case.update({
        'case_id': 'SF-004', 'status': 'READY_FOR_IMPORT', 'run_id': RUN_ID,
        'faults': ['HTTP_HOME_DELAY_250MS_60'], 'fault_count': 1,
        **{name: expected for name, (_, expected) in EVIDENCE.items()},
        'raw_master': str(DATA / 'runs' / RUN_ID),
        'package_master': str(DATA / 'packages' / RUN_ID),
        'raw_and_package_worker3': str(Path('/data/rca-archives/ops-competition-2026') / RUN_ID),
        'gt_status': 'DRAFT_PENDING_HUMAN_REVIEW_NOT_FOR_SCORING',
        'gt_candidates': candidate_count,
        'server_import': 'PENDING_PUBLIC_IMPORT_NOT_PUBLISHED',
        'attempt_history': [previous_case, *failures],
    })
    ledger['completed_ready_for_import'] = sum(
        row['status'] == 'READY_FOR_IMPORT' for row in ledger['official_cases'])
    ledger['remaining'] = len(ledger['official_cases']) - ledger['completed_ready_for_import']
    if ledger['completed_ready_for_import'] != 4 or ledger['remaining'] != 6:
        raise RuntimeError('Unexpected READY denominator after SF-004')
    ledger['status_changes'].append({
        'case_id': 'SF-004', 'from_status': previous_case['status'],
        'to_status': 'READY_FOR_IMPORT', 'run_id': RUN_ID,
        'at_utc': datetime.now(timezone.utc).isoformat(),
        'previous_ledger_sha256': previous_sha,
        'evidence_public_manifest_sha256': EVIDENCE['public_manifest_sha256'][1],
        'evidence_admission_sha256': EVIDENCE['admission_decision_sha256'][1],
    })
    if args.dry_run:
        print(json.dumps({'status': 'SF004_READY_LEDGER_DRY_RUN_PASS',
                          'run_id': RUN_ID, 'ready_count': 4, 'remaining': 6,
                          'old_sha256': previous_sha,
                          'history_attempts': [row['run_id'] for row in case['attempt_history']]}))
        return
    backup = LEDGER.with_name('SF-003-to-SF-012-production-v2-ledger.before-sf004-att05-' +
                              previous_sha[:12] + '.json')
    with backup.open('xb') as stream:
        stream.write(prior)
        stream.flush()
        os.fsync(stream.fileno())
    temp = LEDGER.with_suffix('.sf004-att05.tmp')
    with temp.open('x') as stream:
        json.dump(ledger, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, LEDGER)
    print(json.dumps({'status': 'SF004_READY_FOR_IMPORT_LEDGER_UPDATED',
                      'run_id': RUN_ID, 'ready_count': 4, 'remaining': 6,
                      'old_sha256': previous_sha, 'new_sha256': sha(LEDGER),
                      'backup': str(backup)}))


if __name__ == '__main__':
    main()
