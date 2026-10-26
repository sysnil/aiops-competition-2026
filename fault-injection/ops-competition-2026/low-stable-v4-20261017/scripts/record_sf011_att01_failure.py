"""CAS-record the completed but unadmitted SF-011 attempt."""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
DATA = Path('/data/stage5-v3/ops-competition-2026')
RUN_ID = 'SF-011-ATTEMPT-01'
RUN = DATA / 'runs' / RUN_ID
PACKAGE = DATA / 'packages' / RUN_ID
INDEX = Path('/data/stage5-v3/ops-competition-archive-index') / RUN_ID
FINAL = OPS / 'sf011-finalization-v1-20261018'
RESULTS = OPS / 'low-stable-v4-20261017/results'
LEDGER = OPS / 'plans/SF-003-to-SF-012-production-v2-ledger.json'
PRIOR_SHA = '7854666b95dc3bc53cd9b14ccdb1215a420181b2d1da00d2d0bad27dc0951fcc'
FAILURE_SHA = '092708767a8b08a74dffc91ecd838c990b5891d79fc9bbd9612ac0c458a8a30d'


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
    record = read(FINAL / 'attempt-failure.json')
    require(sha(FINAL / 'attempt-failure.json') == FAILURE_SHA and
            record['status'] == 'FAILED_ADMISSION' and
            record['ready_for_import'] is False and
            record['public_admission_pass'] is False and
            record['capture_complete_1800_seconds'] is True and
            record['private_gt_created'] is False,
            'failure record changed')
    checks = {
        RUN / 'terminal.json': 'capture_terminal_sha256',
        RUN / 'control/fault-controller-terminal.json': 'fault_terminal_sha256',
        RUN / 'control/five-host-live.jsonl': 'five_host_monitor_sha256',
        PACKAGE / 'public/manifest.json': 'public_manifest_sha256',
        FINAL / 'public-verification.json': 'public_readback_sha256',
        FINAL / 'admission.json': 'admission_sha256',
        FINAL / 'admission-diagnostic.json': 'admission_diagnostic_sha256',
        INDEX / 'source-manifest.json': 'raw_archive_manifest_sha256',
        INDEX / 'rejected-output-source-manifest.json':
            'rejected_output_archive_manifest_sha256',
        RESULTS / 'sf011-att01-postarchive-health.json': 'final_live_health_sha256',
        RESULTS / 'sf011-att01-postarchive-residue.json': 'final_five_host_residue_sha256',
        RESULTS / 'sf011-att01-postarchive-units.json': 'final_competition_units_sha256',
        RESULTS / 'sf011-att01-postarchive-capacity.json': 'final_capacity_sha256',
    }
    for path, key in checks.items():
        require(sha(path) == record['evidence'][key], f'evidence changed: {path}')
    require(read(RUN / 'terminal.json')['status'] ==
            'SF011_FORMAL_CAPTURE_PASS_PENDING_EXPORT' and
            read(RUN / 'control/fault-controller-terminal.json')['status'] ==
            'SF011_FAULT_SEQUENCE_COMPLETE_PENDING_CAPTURE',
            'capture or fault terminal changed')
    admission = read(FINAL / 'admission.json')
    require(admission['status'] == 'SF011_FORWARD_ADMISSION_FAILED' and
            sorted(key for key, value in admission['checks'].items() if not value) ==
            sorted(record['failed_checks']), 'frozen admission verdict changed')
    require(read(FINAL / 'public-verification.json')['status'] ==
            'PUBLIC_FULL_READBACK_SCHEMA_IDENTITY_HASH_LEAK_PASS' and
            not (PACKAGE / 'private/anomaly-gt-draft.json').exists(),
            'Public boundary or GT state changed')
    for name in ('w2-verification.json', 'rejected-output-worker2-verification.json'):
        require(read(INDEX / name)['status'] ==
                'COMPETITION_ARCHIVE_SHA_AND_RESTORED_READ_PASS',
                f'Worker2 archive failed: {name}')
    for path, status in (
            (RESULTS / 'sf011-att01-postarchive-health.json',
             'SF011_ATT01_FINAL_LIVE_ENVIRONMENT_PASS'),
            (RESULTS / 'sf011-att01-postarchive-residue.json', 'HOST_RESIDUE_PASS'),
            (RESULTS / 'sf011-att01-postarchive-units.json',
             'COMPETITION_UNIT_RESIDUE_PASS')):
        require(read(path)['status'] == status, f'post-archive safety failed: {path}')
    require(read(RESULTS / 'sf011-att01-postarchive-capacity.json')['one_case']['status'] ==
            'DYNAMIC_STORAGE_PASS', 'five GB capacity failed')

    ledger = json.loads(prior)
    rows = [item for item in ledger['official_cases'] if item['case_id'] == 'SF-011']
    require(len(rows) == 1 and rows[0]['status'] == 'NOT_STARTED',
            'SF-011 was already resolved')
    row = rows[0]
    draft = dict(row)
    row.clear()
    row.update({
        'case_id': 'SF-011', 'status': 'FAILED_ADMISSION',
        'run_id': RUN_ID,
        'faults': ['SERVICE_PAUSE_30', 'HTTP_HOME_DELAY_250MS_60'],
        'fault_count': 2, 'failed_checks': record['failed_checks'],
        'formal_manifest_sha256': record['evidence']['formal_manifest_sha256'],
        'public_manifest_sha256': record['evidence']['public_manifest_sha256'],
        'public_verification_sha256': record['evidence']['public_readback_sha256'],
        'admission_decision_sha256': record['evidence']['admission_sha256'],
        'failure_record_sha256': FAILURE_SHA,
        'raw_archive_manifest_sha256': record['evidence']['raw_archive_manifest_sha256'],
        'rejected_output_archive_manifest_sha256':
            record['evidence']['rejected_output_archive_manifest_sha256'],
        'raw_master': str(RUN), 'rejected_package_master': str(PACKAGE),
        'raw_and_rejected_package_worker2':
            '/data/rca-archives/ops-competition-2026/' + RUN_ID,
        'gt_status': 'NOT_CREATED_FAILED_ADMISSION',
        'server_import': 'PROHIBITED_FOR_FAILED_ATTEMPT',
        'attempt_history': [draft],
    })
    ledger['completed_ready_for_import'] = sum(
        item['status'] == 'READY_FOR_IMPORT' for item in ledger['official_cases'])
    ledger['remaining'] = len(ledger['official_cases']) - ledger['completed_ready_for_import']
    require((ledger['completed_ready_for_import'], ledger['remaining']) == (6, 4),
            'failure changed READY count')
    ledger['status_changes'].append({
        'case_id': 'SF-011', 'from_status': 'NOT_STARTED',
        'to_status': 'FAILED_ADMISSION', 'run_id': RUN_ID,
        'at_utc': datetime.now(timezone.utc).isoformat(),
        'previous_ledger_sha256': PRIOR_SHA,
        'failure_record_sha256': FAILURE_SHA,
        'admission_decision_sha256': record['evidence']['admission_sha256'],
        'raw_archive_manifest_sha256': record['evidence']['raw_archive_manifest_sha256'],
        'rejected_output_archive_manifest_sha256':
            record['evidence']['rejected_output_archive_manifest_sha256'],
    })
    backup = LEDGER.with_name(LEDGER.stem + '.before-sf011-att01-' + PRIOR_SHA[:12] + '.json')
    with backup.open('xb') as stream:
        stream.write(prior)
        stream.flush()
        os.fsync(stream.fileno())
    temporary = LEDGER.with_suffix('.sf011-att01.tmp')
    with temporary.open('x') as stream:
        json.dump(ledger, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, LEDGER)
    print(json.dumps({'status': 'SF011_ATT01_FAILURE_LEDGER_UPDATED',
                      'ready_count': 6, 'remaining': 4,
                      'ledger_sha256': sha(LEDGER)}))


if __name__ == '__main__':
    main()
