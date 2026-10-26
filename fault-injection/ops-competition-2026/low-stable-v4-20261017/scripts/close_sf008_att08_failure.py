"""Close the SF-008 attempt stopped by sustained Worker1 storage forecasts."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


RUN = Path('/data/stage5-v3/ops-competition-2026/runs/SF-008-ATTEMPT-08')
OUTPUT = RUN / 'attempt-failure.json'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def main():
    if OUTPUT.exists() or (RUN / 'terminal.json').exists() or (RUN / 'public').exists():
        raise RuntimeError('Normal terminal or Public bundle already exists')
    control = RUN / 'control'
    onset_path = control / 'capture-failure-onset.json'
    fault_path = control / 'fault-controller-terminal.json'
    injection_path = control / 'worker2-private/injection-result.json'
    monitor_path = control / 'five-host-live.jsonl'
    onset, fault, injection = map(read, (onset_path, fault_path, injection_path))
    monitor = [json.loads(line) for line in monitor_path.read_text().splitlines()]
    pending = [row for row in monitor if row.get('decision', {})
               .get('pending_forecast_breaches', {}).get('worker1:data_free_bytes')]
    last = pending[-1] if pending else None
    checks = {
        'worker1_sustained_forecast':
            [row['decision']['pending_forecast_breaches']['worker1:data_free_bytes']
             for row in pending[-3:]] == [1, 2, 3]
            and last['status'] == 'STOP_NEW_WORK'
            and last['decision']['reason'] ==
                'SUSTAINED_FORECAST_worker1:data_free_bytes',
        'actual_floor_not_crossed': last is not None and
            last['snapshot']['worker1']['data_free_bytes'] >= 5_000_000_000,
        'capture_received_safe_stop': onset['error'] == 'SystemExit: 143',
        'controller_received_safe_stop': fault['status'] ==
            'SF008_FORMAL_SEQUENCE_FAILED' and fault['error'] == 'SystemExit: 143',
        'fault_bounded_and_recovered': injection['status'] ==
            'COMPETITION_BOUNDED_INJECTION_COMPLETED_NOT_YET_QUALIFIED'
            and abs(injection['duration_seconds'] - 300) <= 2
            and injection['cleanup']['status'] == 'COMPETITION_CLEANUP_ACTIONS_VERIFIED',
        'final_health': read(control / 'final-live-health.json')['status'] ==
            'FINAL_LIVE_ENVIRONMENT_PASS',
        'five_host_residue': read(control / 'final-residue.json')['status'] ==
            'HOST_RESIDUE_PASS',
        'competition_units_clear': read(control / 'final-units.json')['status'] ==
            'COMPETITION_UNIT_RESIDUE_PASS',
    }
    if not all(checks.values()):
        raise RuntimeError('Failed-attempt evidence incomplete: ' +
                           ', '.join(name for name, passed in checks.items() if not passed))
    artifacts = [onset_path, fault_path, injection_path, monitor_path,
                 control / 'worker2-private/private-samples.jsonl',
                 control / 'final-live-health.json', control / 'final-residue.json',
                 control / 'final-units.json', control / 'final-storage.json']
    result = {
        'status': 'FAILED_SUSTAINED_WORKER1_STORAGE_FORECAST_AFTER_RECOVERY',
        'run_id': 'SF-008-ATTEMPT-08', 'case_id': 'SF-008',
        'closed_utc': datetime.now(timezone.utc).isoformat(),
        'fault_injected': True, 'fault_recovered': True,
        'capture_complete': False, 'public_bundle_created': False,
        'private_gt_created': False, 'ready_for_import': False,
        'checks': checks,
        'worker1_snapshot_free_bytes_at_stop': last['snapshot']['worker1']['data_free_bytes'],
        'worker1_forecast_spend_bytes_at_stop': last['forecast']['worker1']['data_free_bytes'],
        'five_gb_floor_bytes': 5_000_000_000,
        'evidence_sha256': {str(path.relative_to(RUN)): sha(path) for path in artifacts},
        'stop_reason': 'Three consecutive Worker1 forecasts crossed the protected 5 GB floor',
    }
    with OUTPUT.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    OUTPUT.chmod(0o600)
    print(json.dumps({'status': result['status'], 'sha256': sha(OUTPUT)}))


if __name__ == '__main__':
    main()
