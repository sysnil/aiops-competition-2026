"""CAS-record a fully admitted and archived SF-009 ATTEMPT-04."""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
DATA = Path('/data/stage5-v3/ops-competition-2026')
RUN_ID = 'SF-009-ATTEMPT-04'
RUN = DATA / 'runs' / RUN_ID
PACKAGE = DATA / 'packages' / RUN_ID
PUBLIC = PACKAGE / 'public'
INDEX = Path('/data/stage5-v3/ops-competition-archive-index') / RUN_ID
FINAL = OPS / 'sf009-finalization-v2-20261018'
RESULTS = OPS / 'low-stable-v4-20261017/results'
LEDGER = OPS / 'plans/SF-003-to-SF-012-production-v2-ledger.json'
PRIOR_SHA = '66a195276b926adf58a47e3e981c04926338eef98c96dac100d201daa94f0305'
EXPECTED = {
    OPS / 'sf009-real-adapter-v5-20261018/plans' / RUN_ID / 'run-manifest.json':
        'cbff5e273e8f03b8c09c2f9278c8b9572f0e94744cec65a3faca48fc5b674a50',
    PUBLIC / 'manifest.json': '85c2d7f401939d62ad2cf76d24e19c5cb5250ad76c31e775293520f33a6bfd37',
    FINAL / 'public-verification.json': 'bfa783b1ab2115e0696758c3a9762ad2d8ef3d38293c421f7149f275aaf77750',
    FINAL / 'admission.json': '3ddd6011eceac5c3a61d09d820ab283093513a4973acad585c7cdf85fd8d12ec',
    PACKAGE / 'private/anomaly-gt-draft.json': '094b5aee5464259c98b70cc1d803639c5b49c6f853393602ca68311e17bb97f9',
    INDEX / 'source-manifest.json': '2d79c79704acd96e7af5b5ed8de3f90077c2fa2ac34ff3f727f7e0da61d2f02c',
    INDEX / 'output-manifest.json': '9efafbebaa95944fd1163a53e7061dde2e82a72a7caee0098a00e01d53cab1b5',
    RESULTS / 'sf009-att04-postarchive-health.json': 'f3c058517790ac2789dbddc7387055c9170e4dbcc62c8f2dd30e221776703c37',
    RESULTS / 'sf009-att04-postarchive-residue.json': '223508e810f1238b1586121aceb5e387fe80def082565e8379d99836d29db55b',
    RESULTS / 'sf009-att04-postarchive-units.json': '9302d5a90bed87b602863fa4c27befbd60f1847f6e8b2bb3bb37c0292c9106d1',
    RESULTS / 'sf009-att04-postarchive-actual-floor.json': '8c6a904ed4dedc2cac48cd0f796cda7bea50065623e81fe1170dd8c7018af7ab',
    RESULTS / 'sf009-att04-postarchive-worker2-budget.json': 'ff050e382706b2748a1db4a552878d55b1fe26a4e77fb8a46004613a99dcb3c2',
}
PUBLIC_FILES = {'metrics.parquet', 'logs.parquet', 'traces.parquet',
                'trace_index.parquet', 'topology.json', 'manifest.json'}


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read(path):
    return json.loads(path.read_text())


def require(value, reason):
    if not value:
        raise RuntimeError(reason)


def main():
    prior = LEDGER.read_bytes()
    require(hashlib.sha256(prior).hexdigest() == PRIOR_SHA, 'ledger CAS mismatch')
    for path, expected in EXPECTED.items():
        require(sha(path) == expected, f'evidence SHA mismatch: {path}')
    require({file.name for file in PUBLIC.iterdir()} == PUBLIC_FILES,
            'Public file whitelist mismatch')
    manifest = read(PUBLIC / 'manifest.json')
    require(manifest['case_id'] == 'SF-009' and
            manifest['window']['run_id'] == RUN_ID and
            manifest['private_leak_scan'] == 'PASS', 'Public identity or leak check failed')
    for name, meta in manifest['files'].items():
        require(name in PUBLIC_FILES and sha(PUBLIC / name) == meta['sha256'],
                f'Public file mismatch: {name}')
    capture = read(RUN / 'terminal.json')
    fault = read(RUN / 'control/fault-controller-terminal.json')
    require(capture['status'] == 'SF009_FORMAL_CAPTURE_PASS_PENDING_EXPORT' and
            capture['all_chunks_pass'] is True and
            capture['public_window_proxy_matched'] == capture['completed_requests'] and
            fault['status'] == 'SF009_FAULT_SEQUENCE_COMPLETE_PENDING_CAPTURE' and
            fault['stages']['http_recovered'] is True and
            fault['stages']['service_recovered'] is True,
            'capture or fault sequence incomplete')
    admission = read(FINAL / 'admission.json')
    require(admission['status'] == 'SF009_REVERSE_ADMISSION_PASS' and
            all(admission['checks'].values()) and
            admission['public_manifest_sha256'] == EXPECTED[PUBLIC / 'manifest.json'],
            'frozen admission failed')
    require(read(FINAL / 'public-verification.json')['status'] ==
            'PUBLIC_FULL_READBACK_SCHEMA_IDENTITY_HASH_LEAK_PASS',
            'independent Public readback failed')
    gt = read(PACKAGE / 'private/anomaly-gt-draft.json')
    require(gt['status'] == 'DRAFT_PENDING_HUMAN_REVIEW' and
            gt['candidate_count'] == 16 and gt['merged_event_count'] == 6 and
            gt['injection_plan_or_gt_read'] is False and
            (PACKAGE / 'private/injection-plan.json').is_file() and
            (PACKAGE / 'private/injection-record.json').is_file(),
            'Private GT draft or injection boundary changed')
    for name in ('w2-verification.json', 'w2-output-verification.json'):
        require(read(INDEX / name)['status'] ==
                'COMPETITION_ARCHIVE_SHA_AND_RESTORED_READ_PASS',
                f'Worker2 archive unverified: {name}')
    require(read(RESULTS / 'sf009-att04-postarchive-health.json')['status'] ==
            'SF009_ATT04_FINAL_LIVE_ENVIRONMENT_PASS' and
            read(RESULTS / 'sf009-att04-postarchive-residue.json')['status'] ==
            'HOST_RESIDUE_PASS' and
            read(RESULTS / 'sf009-att04-postarchive-units.json')['status'] ==
            'COMPETITION_UNIT_RESIDUE_PASS', 'postarchive safety failed')
    actual = read(RESULTS / 'sf009-att04-postarchive-actual-floor.json')
    require(actual['one_case']['status'] == 'DYNAMIC_STORAGE_PASS' and
            all(value >= 5_000_000_000 for sample in actual['snapshot'].values()
                for key, value in sample.items() if key.endswith('_free_bytes')),
            'actual five GB floor failed')
    require(read(RESULTS / 'sf009-att04-postarchive-worker2-budget.json')['one_case']['status'] ==
            'STOP_NEW_WORK', 'next Worker2 archive budget was not recorded')

    ledger = json.loads(prior)
    rows = [row for row in ledger['official_cases'] if row['case_id'] == 'SF-009']
    require(len(rows) == 1 and rows[0]['run_id'] == 'SF-009-ATTEMPT-03' and
            rows[0]['status'] == 'FAILED_CAPTURE_AFTER_HTTP_MASTER_HEARTBEAT_LOSS',
            'previous SF-009 state changed')
    row = rows[0]
    history = row.setdefault('attempt_history', [])
    require(not any(item.get('run_id') == RUN_ID for item in history),
            'ATTEMPT-04 already recorded')
    for key in list(row):
        if key != 'attempt_history':
            del row[key]
    row.update({
        'case_id': 'SF-009', 'status': 'READY_FOR_IMPORT', 'run_id': RUN_ID,
        'faults': ['HTTP_HOME_DELAY_250MS_60', 'SERVICE_PAUSE_30'],
        'fault_count': 2,
        'formal_manifest_sha256': EXPECTED[OPS / 'sf009-real-adapter-v5-20261018/plans' /
                                            RUN_ID / 'run-manifest.json'],
        'public_manifest_sha256': EXPECTED[PUBLIC / 'manifest.json'],
        'public_verification_sha256': EXPECTED[FINAL / 'public-verification.json'],
        'admission_decision_sha256': EXPECTED[FINAL / 'admission.json'],
        'raw_archive_manifest_sha256': EXPECTED[INDEX / 'source-manifest.json'],
        'output_archive_manifest_sha256': EXPECTED[INDEX / 'output-manifest.json'],
        'raw_master': str(RUN), 'package_master': str(PACKAGE),
        'raw_and_package_worker2':
            '/data/rca-archives/ops-competition-2026/' + RUN_ID,
        'row_counts': manifest['row_counts'],
        'trace_index_rows': manifest['trace_index_rows'],
        'gt_status': gt['status'], 'gt_candidates': gt['candidate_count'],
        'gt_merged_events': gt['merged_event_count'],
        'gt_draft_sha256': EXPECTED[PACKAGE / 'private/anomaly-gt-draft.json'],
        'server_import': 'PENDING_PUBLIC_IMPORT_NOT_PUBLISHED',
        'next_worker2_archive_budget': 'STOP_NEW_WORK',
    })
    ledger['completed_ready_for_import'] = sum(
        item['status'] == 'READY_FOR_IMPORT' for item in ledger['official_cases'])
    ledger['remaining'] = len(ledger['official_cases']) - ledger['completed_ready_for_import']
    require((ledger['completed_ready_for_import'], ledger['remaining']) == (7, 3),
            'READY denominator mismatch')
    ledger['status_changes'].append({
        'case_id': 'SF-009',
        'from_status': 'FAILED_CAPTURE_AFTER_HTTP_MASTER_HEARTBEAT_LOSS',
        'to_status': 'READY_FOR_IMPORT', 'run_id': RUN_ID,
        'at_utc': datetime.now(timezone.utc).isoformat(),
        'previous_ledger_sha256': PRIOR_SHA,
        'public_manifest_sha256': row['public_manifest_sha256'],
        'admission_decision_sha256': row['admission_decision_sha256'],
    })
    backup = LEDGER.with_name(LEDGER.stem + '.before-sf009-att04-' + PRIOR_SHA[:12] + '.json')
    with backup.open('xb') as stream:
        stream.write(prior)
        stream.flush()
        os.fsync(stream.fileno())
    temporary = LEDGER.with_suffix('.sf009-att04.tmp')
    with temporary.open('x') as stream:
        json.dump(ledger, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, LEDGER)
    print(json.dumps({'status': 'SF009_ATT04_READY_LEDGER_UPDATED',
                      'ready_count': 7, 'remaining': 3,
                      'ledger_sha256': sha(LEDGER)}))


if __name__ == '__main__':
    main()
