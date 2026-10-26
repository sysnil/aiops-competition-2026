"""Record SF-008 ATTEMPT-09 only after its independent gates pass."""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
DATA = Path('/data/stage5-v3/ops-competition-2026')
RUN = 'SF-008-ATTEMPT-09'
LEDGER = OPS / 'plans/SF-003-to-SF-012-production-v2-ledger.json'
INDEX = Path('/data/stage5-v3/ops-competition-archive-index') / RUN
PUBLIC = DATA / 'packages' / RUN / 'public'
PRIVATE = DATA / 'packages' / RUN / 'private'
FINAL = OPS / 'sf008-pod300-finalization-v4-20261017'
LOW = OPS / 'low-stable-v4-20261017/results'
OLD_SHA = '0348f24cfc7fa23c936f9571c7d3cd56a68273dc0d8f2b652b42fd40cb4aec4d'
EXPECTED = {
    OPS / 'sf008-pod300-adapter-v4-20261017/plans' / RUN / 'run-manifest.json':
        'c43462d225f256002a52a878caf67f8e4c0342d8a2f41994111703bca323b2df',
    PUBLIC / 'manifest.json': '5182f70f54aa264746db2527f7da5b0a36d40f0328e1ab6003963b8cc7a59418',
    FINAL / 'pod-admission.json': '4612df91b5797c24813aad20152beb6824d542f3102a788dbe6477422a0ee4fe',
    FINAL / 'public-verification.json': 'a28c84986f0cd512fb5fbb318eca0d0ca41277d610de94af1c51a2bcc355ff41',
    INDEX / 'raw-source-manifest.json': '4f9a33a2bb5e90c2d06e670c6cce62b6f4701d1fb97101cd181a01140fc97a67',
    INDEX / 'outputs-source-manifest.json': '8cf9f2aa5dd9c68e18babb165e16c4df08b5273889362986e3c36a1dd139518c',
    PRIVATE / 'anomaly-gt-draft.json': '4febc99c5a9e4efafb775a33cc837f2a6e64c3d05cbf5e2f320a5b82d7acedee',
}
SIX = {'metrics.parquet', 'logs.parquet', 'traces.parquet',
       'trace_index.parquet', 'topology.json', 'manifest.json'}


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def load(path):
    return json.loads(path.read_text())


def require(condition, reason):
    if not condition:
        raise RuntimeError(reason)


def main():
    prior = LEDGER.read_bytes()
    require(hashlib.sha256(prior).hexdigest() == OLD_SHA, 'ledger CAS mismatch')
    for path, expected in EXPECTED.items():
        require(digest(path) == expected, f'evidence SHA mismatch: {path}')
    require({p.name for p in PUBLIC.iterdir()} == SIX, 'Public whitelist mismatch')
    manifest = load(PUBLIC / 'manifest.json')
    require(manifest['case_id'] == 'SF-008', 'Public case ID mismatch')
    for name, meta in manifest['files'].items():
        path = PUBLIC / name
        require(name in SIX and path.is_file(), f'missing Public file: {name}')
        require(digest(path) == meta['sha256'], f'Public file SHA mismatch: {name}')
    require(load(DATA / 'runs' / RUN / 'terminal.json')['status'] ==
            'SF008_FORMAL_CAPTURE_PASS_PENDING_EXPORT', 'capture terminal failed')
    admission = load(FINAL / 'pod-admission.json')
    require(admission['status'] == 'SF008_POD_MEMORY_ADMISSION_PASS' and
            all(admission['checks'].values()), 'admission failed')
    require(load(FINAL / 'public-verification.json')['status'] ==
            'PUBLIC_FULL_READBACK_SCHEMA_IDENTITY_HASH_LEAK_PASS', 'Public verification failed')
    gt = load(PRIVATE / 'anomaly-gt-draft.json')
    require(gt['status'] == 'DRAFT_PENDING_HUMAN_REVIEW' and
            gt['injection_plan_or_gt_read'] is False, 'GT draft boundary failed')
    for name in ('raw-worker2-verification.json', 'outputs-worker2-verification.json'):
        require(load(INDEX / name)['status'] ==
                'COMPETITION_ARCHIVE_SHA_AND_RESTORED_READ_PASS', f'archive failed: {name}')
    require(load(FINAL / 'final-live-health.json')['status'] ==
            'FINAL_LIVE_ENVIRONMENT_PASS', 'live health failed')
    require(load(LOW / 'sf008-att09-final-residue.json')['status'] ==
            'HOST_RESIDUE_PASS', 'five-host residue failed')
    require(load(LOW / 'sf008-att09-final-units.json')['status'] ==
            'COMPETITION_UNIT_RESIDUE_PASS', 'competition unit residue failed')
    storage = load(LOW / 'sf008-att09-final-storage.json')
    require(storage['status'] == 'READ_ONLY_FIVE_GB_SNAPSHOT' and
            storage['one_case']['status'] == 'DYNAMIC_STORAGE_PASS' and
            storage['one_case']['floor_bytes'] == 5_000_000_000,
            'storage snapshot failed')

    ledger = json.loads(prior)
    rows = [row for row in ledger['official_cases'] if row['case_id'] == 'SF-008']
    require(len(rows) == 1 and rows[0]['status'] == 'FAILED_STORAGE_FORECAST' and
            rows[0]['run_id'] == 'SF-008-ATTEMPT-08', 'unexpected SF-008 state')
    row = rows[0]
    old = {key: value for key, value in row.items() if key != 'attempt_history'}
    history = row.setdefault('attempt_history', [])
    require(not any(item.get('run_id') == RUN for item in history), 'attempt already recorded')
    history.append(old)
    for key in list(row):
        if key != 'attempt_history':
            del row[key]
    row.update({
        'case_id': 'SF-008', 'status': 'READY_FOR_IMPORT', 'run_id': RUN,
        'faults': ['POD_MEMORY_32M_300'], 'fault_count': 1,
        'formal_manifest_sha256': EXPECTED[next(p for p in EXPECTED if p.name == 'run-manifest.json')],
        'public_manifest_sha256': EXPECTED[PUBLIC / 'manifest.json'],
        'admission_decision_sha256': EXPECTED[FINAL / 'pod-admission.json'],
        'public_verification_sha256': EXPECTED[FINAL / 'public-verification.json'],
        'raw_archive_manifest_sha256': EXPECTED[INDEX / 'raw-source-manifest.json'],
        'output_archive_manifest_sha256': EXPECTED[INDEX / 'outputs-source-manifest.json'],
        'raw_master': str(DATA / 'runs' / RUN),
        'package_master': str(DATA / 'packages' / RUN),
        'raw_and_package_worker2': '/data/rca-archives/ops-competition-2026/' + RUN,
        'admission': admission['status'],
        'gt_status': gt['status'], 'gt_candidates': gt['candidate_count'],
        'gt_draft_sha256': EXPECTED[PRIVATE / 'anomaly-gt-draft.json'],
        'server_import': 'PENDING_PUBLIC_IMPORT_NOT_PUBLISHED',
    })
    ledger['completed_ready_for_import'] = sum(
        item['status'] == 'READY_FOR_IMPORT' for item in ledger['official_cases'])
    ledger['remaining'] = len(ledger['official_cases']) - ledger['completed_ready_for_import']
    require((ledger['completed_ready_for_import'], ledger['remaining']) == (6, 4),
            'READY count mismatch')
    ledger['status_changes'].append({
        'case_id': 'SF-008', 'from_status': old['status'], 'to_status': row['status'],
        'run_id': RUN, 'at_utc': datetime.now(timezone.utc).isoformat(),
        'previous_ledger_sha256': OLD_SHA,
        'public_manifest_sha256': row['public_manifest_sha256'],
        'admission_decision_sha256': row['admission_decision_sha256'],
    })
    backup = LEDGER.with_name(LEDGER.stem + '.before-sf008-att09-' + OLD_SHA[:12] + '.json')
    with backup.open('xb') as stream:
        stream.write(prior)
        stream.flush()
        os.fsync(stream.fileno())
    temp = LEDGER.with_suffix('.sf008-att09.tmp')
    with temp.open('x') as stream:
        json.dump(ledger, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, LEDGER)
    print(json.dumps({'status': 'SF008_ATT09_READY_LEDGER_UPDATED',
                      'ready_count': 6, 'remaining': 4,
                      'ledger_sha256': digest(LEDGER), 'backup': str(backup)}))


if __name__ == '__main__':
    main()
