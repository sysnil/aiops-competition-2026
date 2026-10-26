"""CAS-update SF-006 only after full Pod, Public, archive and health acceptance."""

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
EXPECTED_LEDGER_SHA = '5c79ba626aabf7625b0e432f6e04cbea6c8ff16d4a91b538f850ecf0198345e1'
RUN_ID = 'SF-006-ATTEMPT-01'
PUBLIC = DATA / 'packages' / RUN_ID / 'public'
FINAL = OPS / 'sf006-finalization-v1-20261017'
EVIDENCE = {
    'formal_manifest_sha256': (OPS / 'sf006-real-adapter-v1-20261017/plans' / RUN_ID / 'run-manifest.json',
        '77d77907e3d2783b47cee1aa7691d0cc308a7f96e2e4dcebd2775171a1d70d45'),
    'public_manifest_sha256': (PUBLIC / 'manifest.json',
        '895d85aedfc019b2573a573ca848f36d236047f66d4c279710ca8df495cee66f'),
    'admission_decision_sha256': (FINAL / 'pod-admission.json',
        '55441c38632ae10635f875696cf0eebb1f2d52268dc977b68fe890aa8ec9b902'),
    'raw_archive_manifest_sha256': (INDEX / RUN_ID / 'raw-source-manifest.json',
        '31c9488aedd810de25de1c1649dda2a94a6d520d281da992249b9af7252ef93c'),
    'output_archive_manifest_sha256': (INDEX / RUN_ID / 'outputs-source-manifest.json',
        'd80202a9d9cd62537eca81689edc878a8c7f173fd880afd22aca5aa7e2150b98'),
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def accepted():
    for name, (path, digest) in EVIDENCE.items():
        if sha(path) != digest:
            raise RuntimeError('SF-006 evidence hash changed: ' + name)
    public = read(PUBLIC / 'manifest.json')
    if ({path.name for path in PUBLIC.iterdir()} != {
            'metrics.parquet', 'logs.parquet', 'traces.parquet',
            'trace_index.parquet', 'topology.json', 'manifest.json'} or
            public['case_id'] != 'SF-006'):
        raise RuntimeError('SF-006 Public file set incomplete')
    for name, entry in public['files'].items():
        if sha(PUBLIC / name) != entry['sha256']:
            raise RuntimeError('Public file SHA mismatch: ' + name)
    verify = read(FINAL / 'public-verification.json')
    admission = read(FINAL / 'pod-admission.json')
    gt = read(DATA / 'packages' / RUN_ID / 'private/anomaly-gt-draft.json')
    capture = read(DATA / 'runs' / RUN_ID / 'terminal.json')
    fault = read(DATA / 'runs' / RUN_ID / 'control/fault-controller-terminal.json')
    health = read(OPS / 'low-stable-v4-20261017/results/sf006-attempt01-final-live-health.json')
    residue = read(OPS / 'low-stable-v4-20261017/results/sf006-attempt01-final-residue.json')
    units = read(OPS / 'low-stable-v4-20261017/results/sf006-attempt01-final-units.json')
    receipts = [read(INDEX / RUN_ID / (kind + '-worker1-verification.json'))
                for kind in ('raw', 'outputs')]
    private = DATA / 'packages' / RUN_ID / 'private'
    if (verify['status'] != 'PUBLIC_FULL_READBACK_SCHEMA_IDENTITY_HASH_LEAK_PASS' or
            verify['public_manifest_sha256'] != sha(PUBLIC / 'manifest.json') or
            admission['status'] != 'SF006_POD_MEMORY_ADMISSION_PASS' or
            not all(admission['checks'].values()) or
            gt['status'] != 'DRAFT_PENDING_HUMAN_REVIEW' or
            gt['formal_scoring_gt'] is not False or
            capture['status'] != 'SF006_FORMAL_CAPTURE_PASS_PENDING_EXPORT' or
            capture['all_chunks_pass'] is not True or
            fault['status'] != 'SF006_FAULT_SEQUENCE_COMPLETE_PENDING_CAPTURE' or
            fault['stages'].get('pod_recovered') is not True or
            health['status'] != 'FINAL_LIVE_ENVIRONMENT_PASS' or
            residue['status'] != 'HOST_RESIDUE_PASS' or
            units['status'] != 'COMPETITION_UNIT_RESIDUE_PASS' or
            any(row['status'] != 'COMPETITION_ARCHIVE_SHA_AND_RESTORED_READ_PASS'
                for row in receipts) or
            not all((private / name).is_file() for name in
                    ('injection-plan.json', 'injection-record.json',
                     'private-samples.jsonl', 'injection-result.json'))):
        raise RuntimeError('SF-006 final acceptance evidence incomplete')
    return gt['candidate_count']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    prior = LEDGER.read_bytes()
    previous_sha = hashlib.sha256(prior).hexdigest()
    if previous_sha != EXPECTED_LEDGER_SHA:
        raise RuntimeError('Production ledger changed; no overwrite')
    candidate_count = accepted()
    ledger = json.loads(prior)
    matches = [row for row in ledger['official_cases'] if row['case_id'] == 'SF-006']
    if len(matches) != 1 or matches[0]['status'] != 'NOT_STARTED':
        raise RuntimeError('Original SF-006 status changed')
    case = matches[0]
    old_design = dict(case)
    case.clear()
    case.update({
        'case_id': 'SF-006', 'status': 'READY_FOR_IMPORT', 'run_id': RUN_ID,
        'faults': ['POD_MEMORY_32M_60'], 'fault_count': 1,
        **{name: digest for name, (_, digest) in EVIDENCE.items()},
        'raw_master': str(DATA / 'runs' / RUN_ID),
        'package_master': str(DATA / 'packages' / RUN_ID),
        'raw_and_package_worker1': str(Path('/data/rca-archives/ops-competition-2026') / RUN_ID),
        'gt_status': 'DRAFT_PENDING_HUMAN_REVIEW_NOT_FOR_SCORING',
        'gt_candidates': candidate_count,
        'server_import': 'PENDING_PUBLIC_IMPORT_NOT_PUBLISHED',
        'design_history': old_design,
    })
    ledger['completed_ready_for_import'] = sum(
        row['status'] == 'READY_FOR_IMPORT' for row in ledger['official_cases'])
    ledger['remaining'] = len(ledger['official_cases']) - ledger['completed_ready_for_import']
    if (ledger['completed_ready_for_import'], ledger['remaining']) != (5, 5):
        raise RuntimeError('Unexpected formal denominator after SF-006')
    ledger['status_changes'].append({
        'case_id': 'SF-006', 'from_status': 'NOT_STARTED',
        'to_status': 'READY_FOR_IMPORT', 'run_id': RUN_ID,
        'at_utc': datetime.now(timezone.utc).isoformat(),
        'previous_ledger_sha256': previous_sha,
        'evidence_public_manifest_sha256': EVIDENCE['public_manifest_sha256'][1],
        'evidence_admission_sha256': EVIDENCE['admission_decision_sha256'][1],
        'simplification': 'Original Pod+Service draft replaced prospectively by one qualified PodMemory60 fault',
    })
    if args.dry_run:
        print(json.dumps({'status': 'SF006_READY_LEDGER_DRY_RUN_PASS',
                          'ready_count': 5, 'remaining': 5,
                          'old_sha256': previous_sha,
                          'preserved_original_design': old_design}))
        return
    backup = LEDGER.with_name('SF-003-to-SF-012-production-v2-ledger.before-sf006-att01-' +
                              previous_sha[:12] + '.json')
    with backup.open('xb') as stream:
        stream.write(prior)
        stream.flush()
        os.fsync(stream.fileno())
    temp = LEDGER.with_suffix('.sf006-att01.tmp')
    with temp.open('x') as stream:
        json.dump(ledger, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, LEDGER)
    print(json.dumps({'status': 'SF006_READY_FOR_IMPORT_LEDGER_UPDATED',
                      'ready_count': 5, 'remaining': 5,
                      'old_sha256': previous_sha, 'new_sha256': sha(LEDGER),
                      'backup': str(backup)}))


if __name__ == '__main__':
    main()
