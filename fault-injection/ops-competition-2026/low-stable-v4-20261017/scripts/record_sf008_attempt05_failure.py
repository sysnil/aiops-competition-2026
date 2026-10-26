"""CAS-record the completed but admission-invalid SF-008 attempt."""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
DATA = Path('/data/stage5-v3/ops-competition-2026')
RUN_ID = 'SF-008-ATTEMPT-05'
INDEX = Path('/data/stage5-v3/ops-competition-archive-index') / RUN_ID
LEDGER = OPS / 'plans/SF-003-to-SF-012-production-v2-ledger.json'
OUTPUT = OPS / 'low-stable-v4-20261017/results/sf008-attempt05-admission-failure.json'
EXPECTED_LEDGER_SHA = 'ed20e8afa7c7812206c9e9f04dc927515f300509d0de30313d90d5ff3be73536'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def main():
    prior = LEDGER.read_bytes()
    if hashlib.sha256(prior).hexdigest() != EXPECTED_LEDGER_SHA or OUTPUT.exists():
        raise RuntimeError('Ledger changed or failure record already exists')
    raw = DATA / 'runs' / RUN_ID
    package = DATA / 'packages' / RUN_ID
    admission_path = OPS / 'sf008-finalization-v5-20261017/pod-http-admission.json'
    verification_path = OPS / 'sf008-finalization-v5-20261017/public-verification.json'
    admission = read(admission_path)
    verification = read(verification_path)
    raw_archive = read(INDEX / 'raw-worker1-verification.json')
    output_archive = read(INDEX / 'outputs-worker1-verification.json')
    health = read(OPS / 'sf008-finalization-v5-20261017/final-live-health.json')
    residue = read(OPS / 'low-stable-v4-20261017/results/sf008-attempt05-final-residue.json')
    units = read(OPS / 'low-stable-v4-20261017/results/sf008-attempt05-final-units.json')
    storage = read(OPS / 'low-stable-v4-20261017/results/sf008-attempt05-final-storage.json')
    if (read(raw / 'terminal.json')['status'] != 'SF008_FORMAL_CAPTURE_PASS_PENDING_EXPORT' or
            admission['status'] != 'SF008_POD_HTTP_ADMISSION_FAILED' or
            {name for name, passed in admission['checks'].items() if not passed} !=
            {'pod_direct_memory_signal', 'image_latency'} or
            verification['status'] != 'PUBLIC_PACKAGE_VERIFIED' or
            verification['manifest_sha256'] != admission['public_manifest_sha256'] or
            any(row['status'] != 'COMPETITION_ARCHIVE_SHA_AND_RESTORED_READ_PASS'
                for row in (raw_archive, output_archive)) or
            health['status'] != 'FINAL_LIVE_ENVIRONMENT_PASS' or
            residue['status'] != 'HOST_RESIDUE_PASS' or
            units['status'] != 'COMPETITION_UNIT_RESIDUE_PASS' or
            storage['one_case']['status'] != 'DYNAMIC_STORAGE_PASS' or
            (package / 'private/anomaly-gt-draft.json').exists()):
        raise RuntimeError('Failed attempt cannot be closed from incomplete evidence')
    evidence = {
        'status': 'FAILED_ADMISSION', 'case_id': 'SF-008', 'run_id': RUN_ID,
        'ready_for_import': False,
        'failed_checks': ['pod_direct_memory_signal', 'image_latency'],
        'capture_terminal_sha256': sha(raw / 'terminal.json'),
        'admission_sha256': sha(admission_path),
        'public_manifest_sha256': sha(package / 'public/manifest.json'),
        'raw_archive_manifest_sha256': sha(INDEX / 'raw-source-manifest.json'),
        'output_archive_manifest_sha256': sha(INDEX / 'outputs-source-manifest.json'),
        'raw_master': str(raw), 'public_master': str(package / 'public'),
        'raw_worker1': '/data/rca-archives/ops-competition-2026/' + RUN_ID + '/raw',
        'outputs_worker1': '/data/rca-archives/ops-competition-2026/' + RUN_ID + '/outputs',
        'gt_status': 'NOT_CREATED_FAILED_ADMISSION',
        'server_import': 'PROHIBITED_FOR_FAILED_ATTEMPT',
        'final_health_sha256': sha(OPS / 'sf008-finalization-v5-20261017/final-live-health.json'),
        'final_residue_sha256': sha(OPS / 'low-stable-v4-20261017/results/sf008-attempt05-final-residue.json'),
        'final_units_sha256': sha(OPS / 'low-stable-v4-20261017/results/sf008-attempt05-final-units.json'),
    }
    ledger = json.loads(prior)
    matches = [row for row in ledger['official_cases'] if row['case_id'] == 'SF-008']
    if len(matches) != 1 or matches[0]['status'] != 'FAILED_CAPTURE_INFRASTRUCTURE':
        raise RuntimeError('SF-008 historical state changed')
    case = matches[0]
    case.setdefault('attempt_history', []).append(dict(evidence))
    case.update({
        'status': 'FAILED_ADMISSION', 'run_id': RUN_ID,
        'failed_gate': 'POD_DIRECT_MEMORY_SIGNAL_AND_IMAGE_CONTROL_P95',
        'capture_terminal_sha256': evidence['capture_terminal_sha256'],
        'admission_sha256': evidence['admission_sha256'],
        'public_manifest_sha256': evidence['public_manifest_sha256'],
        'raw_archive_manifest_sha256': evidence['raw_archive_manifest_sha256'],
        'output_archive_manifest_sha256': evidence['output_archive_manifest_sha256'],
        'raw_master': evidence['raw_master'], 'raw_worker1': evidence['raw_worker1'],
        'public_package': evidence['public_master'],
        'gt_status': evidence['gt_status'], 'server_import': evidence['server_import'],
    })
    ledger['completed_ready_for_import'] = sum(
        row['status'] == 'READY_FOR_IMPORT' for row in ledger['official_cases'])
    ledger['remaining'] = len(ledger['official_cases']) - ledger['completed_ready_for_import']
    if (ledger['completed_ready_for_import'], ledger['remaining']) != (5, 5):
        raise RuntimeError('Failed SF-008 changed READY count')
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open('x') as stream:
        json.dump(evidence, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    ledger['status_changes'].append({
        'case_id': 'SF-008', 'from_status': 'FAILED_CAPTURE_INFRASTRUCTURE',
        'to_status': 'FAILED_ADMISSION', 'run_id': RUN_ID,
        'at_utc': datetime.now(timezone.utc).isoformat(),
        'previous_ledger_sha256': EXPECTED_LEDGER_SHA,
        'failure_record_sha256': sha(OUTPUT),
    })
    backup = LEDGER.with_name('SF-003-to-SF-012-production-v2-ledger.before-sf008-att05-' +
                              EXPECTED_LEDGER_SHA[:12] + '.json')
    with backup.open('xb') as stream:
        stream.write(prior)
        stream.flush()
        os.fsync(stream.fileno())
    temp = LEDGER.with_suffix('.sf008-att05.tmp')
    with temp.open('x') as stream:
        json.dump(ledger, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, LEDGER)
    print(json.dumps({'status': 'SF008_FAILED_ADMISSION_LEDGER_UPDATED',
                      'ready_count': 5, 'remaining': 5,
                      'failure_sha256': sha(OUTPUT), 'ledger_sha256': sha(LEDGER)}))


if __name__ == '__main__':
    main()
