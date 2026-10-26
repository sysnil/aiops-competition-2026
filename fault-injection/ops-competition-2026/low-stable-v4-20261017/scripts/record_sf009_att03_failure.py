"""CAS-close the VPN-interrupted SF-009 attempt after archive verification."""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
DATA = Path('/data/stage5-v3/ops-competition-2026')
RUN = DATA / 'runs/SF-009-ATTEMPT-03'
INDEX = Path('/data/stage5-v3/ops-competition-archive-index/SF-009-ATTEMPT-03')
LEDGER = OPS / 'plans/SF-003-to-SF-012-production-v2-ledger.json'
PRIOR_SHA = '3f441a59759d8fbf4dc717446a09ff9e908d4f552e81a46c6c6c764bb65a50b4'
FAILURE_SHA = '4c8bace920eb7179b5576152d30ac3afb6c3ba9434bdb016f14d785d0e8443aa'
ARCHIVE_SHA = '72ce5f94ad5418ee85c3b0e02615e96a6ff161cd845cced6b44cfd620e2791ac'


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def load(path):
    return json.loads(path.read_text())


def require(value, reason):
    if not value:
        raise RuntimeError(reason)


def main():
    prior = LEDGER.read_bytes()
    require(hashlib.sha256(prior).hexdigest() == PRIOR_SHA, 'ledger CAS mismatch')
    failure = load(RUN / 'attempt-failure.json')
    terminal = load(RUN / 'terminal.json')
    archive = load(INDEX / 'worker2-verification.json')
    worker = RUN / 'control/service-worker-runtime'
    require(sha(RUN / 'attempt-failure.json') == FAILURE_SHA, 'failure evidence changed')
    require(sha(INDEX / 'source-manifest.json') == ARCHIVE_SHA, 'archive manifest changed')
    require(archive['status'] == 'COMPETITION_ARCHIVE_SHA_AND_RESTORED_READ_PASS' and
            archive['source_manifest_sha256'] == ARCHIVE_SHA,
            'Worker2 archive unverified')
    require(failure['status'] == 'FAILED_CAPTURE_AFTER_HTTP_MASTER_HEARTBEAT_LOSS' and
            failure['http_reqdelay_250ms_requests_applied'] == 102 and
            failure['service_pause_injected'] is False and
            failure['capture_complete'] is False and
            failure['public_bundle_created'] is False and
            failure['private_gt_created'] is False,
            'Attempt failure classification changed')
    require(terminal['status'] == 'SF009_FORMAL_CAPTURE_FAILED' and
            not (DATA / 'packages/SF-009-ATTEMPT-03').exists() and
            not (worker / 'actual-start.json').exists() and
            sha(worker / 'cancelled.json') == failure['worker3_cancellation_sha256'] and
            sha(worker / 'REVOKED.json') == failure['worker3_revocation_sha256'],
            'Service fault or Public boundary not closed')
    result_dir = OPS / 'low-stable-v4-20261017/results'
    for name, digest, status in (
            ('sf009-att03-final-health.json', 'final_live_health_sha256',
             'SF009_ATT03_FINAL_LIVE_ENVIRONMENT_PASS'),
            ('sf009-att03-postrelocation-residue.json', 'final_five_host_residue_sha256',
             'HOST_RESIDUE_PASS'),
            ('sf009-att03-postrelocation-units.json', 'final_competition_units_sha256',
             'COMPETITION_UNIT_RESIDUE_PASS'),
            ('sf009-att03-postrelocation-capacity.json', 'final_capacity_sha256',
             'READ_ONLY_FIVE_GB_SNAPSHOT')):
        path = result_dir / name
        require(sha(path) == failure[digest] and load(path)['status'] == status,
                f'final gate invalid: {name}')
    require(load(result_dir / 'sf009-att03-postrelocation-capacity.json')['one_case']['status'] ==
            'DYNAMIC_STORAGE_PASS', 'five GB budget not restored')
    require(sha(RUN / 'control/five-host-live.jsonl') ==
            failure['five_host_heartbeat_log_sha256'], 'heartbeat log changed')

    ledger = json.loads(prior)
    rows = [row for row in ledger['official_cases'] if row['case_id'] == 'SF-009']
    require(len(rows) == 1 and rows[0]['run_id'] == 'SF-009-ATTEMPT-02' and
            rows[0]['status'] == 'FAILED_BEFORE_INJECTION_MASTER_TO_WORKER3_AUTH',
            'unexpected prior SF-009 state')
    row = rows[0]
    history = row.setdefault('attempt_history', [])
    require(not any(item.get('run_id') == 'SF-009-ATTEMPT-03' for item in history),
            'ATTEMPT-03 already recorded')
    history.append({
        'run_id': 'SF-009-ATTEMPT-03', 'status': failure['status'],
        'http_reqdelay_250ms_requests_applied': 102,
        'service_pause_injected': False, 'capture_complete': False,
        'public_bundle_created': False, 'private_gt_created': False,
        'formal_manifest_sha256': failure['formal_manifest_sha256'],
        'failure_record_sha256': FAILURE_SHA,
        'raw_archive_manifest_sha256': ARCHIVE_SHA,
    })
    row.update({
        'status': failure['status'], 'run_id': 'SF-009-ATTEMPT-03',
        'faults': ['HTTP_HOME_DELAY_250MS_60', 'SERVICE_PAUSE_30'],
        'fault_count': 2, 'failed_gate': 'VPN_HEARTBEAT_LOSS_BEFORE_SERVICE_FAULT',
        'formal_manifest_sha256': failure['formal_manifest_sha256'],
        'failure_record_sha256': FAILURE_SHA,
        'capture_terminal_sha256': failure['capture_terminal_sha256'],
        'raw_archive_manifest_sha256': ARCHIVE_SHA,
        'raw_master': str(RUN),
        'raw_worker2': '/data/rca-archives/ops-competition-2026/SF-009-ATTEMPT-03/raw',
        'public_package': 'NOT_CREATED',
        'gt_status': 'NOT_CREATED_FAILED_CAPTURE',
        'server_import': 'PROHIBITED_FOR_FAILED_ATTEMPT',
    })
    for key in ('public_manifest_sha256', 'output_archive_manifest_sha256',
                'admission_decision_sha256', 'package_master', 'raw_and_package_worker2',
                'raw_and_package_worker3', 'row_counts', 'trace_index_rows',
                'failed_gates'):
        row.pop(key, None)
    ledger['completed_ready_for_import'] = sum(
        case['status'] == 'READY_FOR_IMPORT' for case in ledger['official_cases'])
    ledger['remaining'] = len(ledger['official_cases']) - ledger['completed_ready_for_import']
    require((ledger['completed_ready_for_import'], ledger['remaining']) == (6, 4),
            'READY count changed on failure')
    ledger['status_changes'].append({
        'case_id': 'SF-009', 'from_status': 'FAILED_BEFORE_INJECTION_MASTER_TO_WORKER3_AUTH',
        'to_status': failure['status'], 'run_id': 'SF-009-ATTEMPT-03',
        'at_utc': datetime.now(timezone.utc).isoformat(),
        'previous_ledger_sha256': PRIOR_SHA,
        'failure_record_sha256': FAILURE_SHA,
        'raw_archive_manifest_sha256': ARCHIVE_SHA,
    })
    backup = LEDGER.with_name(LEDGER.stem + '.before-sf009-att03-' + PRIOR_SHA[:12] + '.json')
    with backup.open('xb') as stream:
        stream.write(prior)
        stream.flush()
        os.fsync(stream.fileno())
    tmp = LEDGER.with_suffix('.sf009-att03.tmp')
    with tmp.open('x') as stream:
        json.dump(ledger, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, LEDGER)
    print(json.dumps({'status': 'SF009_ATT03_FAILURE_LEDGER_UPDATED',
                      'ready_count': 6, 'remaining': 4,
                      'ledger_sha256': sha(LEDGER)}))


if __name__ == '__main__':
    main()
