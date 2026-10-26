"""CAS-close the second valid-capture, invalid-admission SF-011 attempt."""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
DATA = Path('/data/stage5-v3/ops-competition-2026')
RUN_ID = 'SF-011-ATTEMPT-02'
RUN = DATA / 'runs' / RUN_ID
PACKAGE = DATA / 'packages' / RUN_ID
INDEX = Path('/data/stage5-v3/ops-competition-archive-index') / RUN_ID
FINAL = OPS / 'sf011-finalization-v2-20261018'
RESULTS = OPS / 'low-stable-v4-20261017/results'
LEDGER = OPS / 'plans/SF-003-to-SF-012-production-v2-ledger.json'
PRIOR_SHA = 'b5e83033efa81610685133bac9e384fa04187d61659c25bcf92f2c80235046b1'
FAILURE_SHA = 'a84409d6561f8a8b103b8eb2e7f8e202c62c05d043b6ce26c1f672e417c3f8d9'


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def read(path):
    return json.loads(path.read_text())


def require(value, reason):
    if not value:
        raise RuntimeError(reason)


def main():
    prior = LEDGER.read_bytes()
    require(hashlib.sha256(prior).hexdigest() == PRIOR_SHA, 'ledger CAS mismatch')
    failure = read(FINAL / 'attempt-failure.json')
    require(sha(FINAL / 'attempt-failure.json') == FAILURE_SHA and
            failure['status'] == 'FAILED_ADMISSION' and
            failure['ready_for_import'] is False and
            failure['failed_checks'] == ['service_cart_latency'] and
            failure['capture_complete_1800_seconds'] is True and
            failure['private_gt_created'] is False,
            'failure record changed')
    paths = {
        RUN / 'terminal.json': 'capture_terminal_sha256',
        RUN / 'control/fault-controller-terminal.json': 'fault_terminal_sha256',
        RUN / 'control/five-host-live.jsonl': 'five_host_monitor_sha256',
        PACKAGE / 'public/manifest.json': 'public_manifest_sha256',
        FINAL / 'public-verification.json': 'public_readback_sha256',
        FINAL / 'admission.json': 'admission_sha256',
        INDEX / 'source-manifest.json': 'raw_archive_manifest_sha256',
        INDEX / 'rejected-output-source-manifest.json':
            'rejected_output_archive_manifest_sha256',
        RESULTS / 'sf011-att02-postarchive-health.json': 'final_live_health_sha256',
        RESULTS / 'sf011-att02-postarchive-residue.json': 'final_five_host_residue_sha256',
        RESULTS / 'sf011-att02-postarchive-units.json': 'final_competition_units_sha256',
        RESULTS / 'sf011-att02-postarchive-actual-floor.json': 'actual_five_gb_floor_sha256',
        RESULTS / 'sf011-att02-postarchive-next-case-capacity.json':
            'next_case_capacity_block_sha256',
    }
    for path, key in paths.items():
        require(sha(path) == failure['evidence'][key], f'evidence changed: {path}')
    require(read(RUN / 'terminal.json')['status'] ==
            'SF011_FORMAL_CAPTURE_PASS_PENDING_EXPORT' and
            read(RUN / 'control/fault-controller-terminal.json')['status'] ==
            'SF011_FAULT_SEQUENCE_COMPLETE_PENDING_CAPTURE',
            'fault or capture incomplete')
    admission = read(FINAL / 'admission.json')
    require(admission['status'] == 'SF011_FORWARD_ADMISSION_FAILED' and
            [key for key, ok in admission['checks'].items() if not ok] ==
            ['service_cart_latency'], 'frozen admission verdict changed')
    require(read(FINAL / 'public-verification.json')['status'] ==
            'PUBLIC_FULL_READBACK_SCHEMA_IDENTITY_HASH_LEAK_PASS' and
            not (PACKAGE / 'private/anomaly-gt-draft.json').exists(),
            'Public boundary or GT state changed')
    for name in ('w2-verification.json', 'rejected-output-worker2-verification.json'):
        require(read(INDEX / name)['status'] ==
                'COMPETITION_ARCHIVE_SHA_AND_RESTORED_READ_PASS',
                f'Worker2 archive failed: {name}')
    for name, status in (
            ('sf011-att02-postarchive-health.json',
             'SF011_ATT02_FINAL_LIVE_ENVIRONMENT_PASS'),
            ('sf011-att02-postarchive-residue.json', 'HOST_RESIDUE_PASS'),
            ('sf011-att02-postarchive-units.json', 'COMPETITION_UNIT_RESIDUE_PASS')):
        require(read(RESULTS / name)['status'] == status,
                f'post-archive recovery failed: {name}')
    actual = read(RESULTS / 'sf011-att02-postarchive-actual-floor.json')
    require(actual['one_case']['status'] == 'DYNAMIC_STORAGE_PASS' and
            all(value >= 5_000_000_000 for sample in actual['snapshot'].values()
                for key, value in sample.items() if key.endswith('_free_bytes')),
            'actual five GB floor failed')
    blocked = read(RESULTS / 'sf011-att02-postarchive-next-case-capacity.json')
    require(blocked['one_case']['status'] == 'STOP_NEW_WORK' and
            blocked['one_case']['reason'] ==
            'PREDICTED_FIVE_GB_FLOOR_worker2_data_free_bytes',
            'next-case budget unexpectedly changed')

    ledger = json.loads(prior)
    rows = [item for item in ledger['official_cases'] if item['case_id'] == 'SF-011']
    require(len(rows) == 1 and rows[0]['run_id'] == 'SF-011-ATTEMPT-01' and
            rows[0]['status'] == 'FAILED_ADMISSION', 'prior attempt changed')
    row = rows[0]
    old = {key: value for key, value in row.items() if key != 'attempt_history'}
    history = row.setdefault('attempt_history', [])
    require(not any(item.get('run_id') == RUN_ID for item in history),
            'ATTEMPT-02 already recorded')
    history.extend((old, {
        'run_id': RUN_ID, 'status': 'FAILED_ADMISSION',
        'faults_injected': 2, 'faults_recovered': True,
        'capture_complete': True, 'public_structural_quality_pass': True,
        'failed_checks': failure['failed_checks'],
        'private_gt_created': False,
        'failure_record_sha256': FAILURE_SHA,
        'admission_decision_sha256': failure['evidence']['admission_sha256'],
        'raw_archive_manifest_sha256': failure['evidence']['raw_archive_manifest_sha256'],
        'rejected_output_archive_manifest_sha256':
            failure['evidence']['rejected_output_archive_manifest_sha256'],
    }))
    for key in list(row):
        if key != 'attempt_history':
            del row[key]
    row.update({
        'case_id': 'SF-011', 'status': 'FAILED_ADMISSION', 'run_id': RUN_ID,
        'faults': ['SERVICE_PAUSE_30', 'HTTP_HOME_DELAY_250MS_60'],
        'fault_count': 2, 'failed_checks': failure['failed_checks'],
        'formal_manifest_sha256': failure['evidence']['formal_manifest_sha256'],
        'public_manifest_sha256': failure['evidence']['public_manifest_sha256'],
        'public_verification_sha256': failure['evidence']['public_readback_sha256'],
        'admission_decision_sha256': failure['evidence']['admission_sha256'],
        'failure_record_sha256': FAILURE_SHA,
        'raw_archive_manifest_sha256': failure['evidence']['raw_archive_manifest_sha256'],
        'rejected_output_archive_manifest_sha256':
            failure['evidence']['rejected_output_archive_manifest_sha256'],
        'raw_master': str(RUN), 'rejected_package_master': str(PACKAGE),
        'raw_and_rejected_package_worker2':
            '/data/rca-archives/ops-competition-2026/' + RUN_ID,
        'gt_status': 'NOT_CREATED_FAILED_ADMISSION',
        'server_import': 'PROHIBITED_FOR_FAILED_ATTEMPT',
        'next_case_storage': 'STOP_NEW_WORK_PENDING_VERIFIED_MIGRATION',
    })
    ledger['completed_ready_for_import'] = sum(
        item['status'] == 'READY_FOR_IMPORT' for item in ledger['official_cases'])
    ledger['remaining'] = len(ledger['official_cases']) - ledger['completed_ready_for_import']
    require((ledger['completed_ready_for_import'], ledger['remaining']) == (6, 4),
            'failed attempt changed READY count')
    ledger['status_changes'].append({
        'case_id': 'SF-011', 'from_status': old['status'],
        'to_status': 'FAILED_ADMISSION', 'run_id': RUN_ID,
        'at_utc': datetime.now(timezone.utc).isoformat(),
        'previous_ledger_sha256': PRIOR_SHA,
        'failure_record_sha256': FAILURE_SHA,
        'admission_decision_sha256': failure['evidence']['admission_sha256'],
        'raw_archive_manifest_sha256': failure['evidence']['raw_archive_manifest_sha256'],
    })
    backup = LEDGER.with_name(LEDGER.stem + '.before-sf011-att02-' + PRIOR_SHA[:12] + '.json')
    with backup.open('xb') as stream:
        stream.write(prior)
        stream.flush()
        os.fsync(stream.fileno())
    temporary = LEDGER.with_suffix('.sf011-att02.tmp')
    with temporary.open('x') as stream:
        json.dump(ledger, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, LEDGER)
    print(json.dumps({'status': 'SF011_ATT02_FAILURE_LEDGER_UPDATED',
                      'ready_count': 6, 'remaining': 4,
                      'ledger_sha256': sha(LEDGER)}))


if __name__ == '__main__':
    main()
