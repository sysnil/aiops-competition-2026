"""Close the pre-injection SF-008 attempt stopped by a sustained reserve forecast."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


RUN = Path('/data/stage5-v3/ops-competition-2026/runs/SF-008-ATTEMPT-07')
OUTPUT = RUN / 'attempt-failure.json'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def main():
    if OUTPUT.exists() or (RUN / 'public').exists():
        raise RuntimeError('Failure record or Public package already exists')
    control = RUN / 'control'
    capture_path = RUN / 'terminal.json'
    monitor_path = control / 'five-host-live.jsonl'
    capture = read(capture_path)
    monitor = [json.loads(line) for line in monitor_path.read_text().splitlines()]
    w3 = [row for row in monitor if row.get('snapshot') and row.get('decision', {})
          .get('pending_forecast_breaches', {}).get('worker3:data_free_bytes')]
    last = w3[-1] if w3 else None
    checks = {
        'three_consecutive_w3_reserve_breaches':
            [row['decision']['pending_forecast_breaches']['worker3:data_free_bytes']
             for row in w3[-3:]] == [1, 2, 3]
            and last['status'] == 'STOP_NEW_WORK'
            and last['decision']['reason'] ==
                'SUSTAINED_FORECAST_worker3:data_free_bytes',
        'actual_floor_not_crossed': last is not None and
            last['snapshot']['worker3']['data_free_bytes'] >= 5_000_000_000,
        'capture_stopped_before_fault': capture['status'] == 'SF008_FORMAL_CAPTURE_FAILED'
            and capture['public_created'] is False and
            not (control / 'pod-onset.json').exists(),
        'observer_cleaned': read(control / 'postcleanup-units.json')['status'] ==
            'COMPETITION_UNIT_RESIDUE_PASS',
        'five_host_residue': read(control / 'postcleanup-residue.json')['status'] ==
            'HOST_RESIDUE_PASS',
        'cluster_health': read(control / 'postcleanup-live-health.json')['status'] ==
            'FINAL_LIVE_ENVIRONMENT_PASS',
        'generic_capacity_gate_consistent': read(control / 'final-storage.json')
            ['one_case']['status'] == 'STOP_NEW_WORK',
    }
    if not all(checks.values()):
        raise RuntimeError('Pre-injection failure evidence incomplete: ' +
                           ', '.join(name for name, passed in checks.items() if not passed))
    artifacts = [capture_path, monitor_path, control / 'final-storage.json',
                 control / 'postcleanup-residue.json', control / 'postcleanup-units.json',
                 control / 'postcleanup-live-health.json']
    result = {
        'status': 'FAILED_BEFORE_INJECTION_W3_RESERVATION_FORECAST',
        'run_id': 'SF-008-ATTEMPT-07', 'case_id': 'SF-008',
        'closed_utc': datetime.now(timezone.utc).isoformat(),
        'faults_injected': 0, 'capture_complete': False,
        'public_bundle_created': False, 'private_gt_created': False,
        'ready_for_import': False, 'checks': checks,
        'worker3_snapshot_free_bytes_at_stop': last['snapshot']['worker3']['data_free_bytes'],
        'worker3_reserved_bytes_at_stop': last['forecast']['worker3']['data_free_bytes'],
        'five_gb_floor_bytes': 5_000_000_000,
        'archive_target_for_this_case': 'worker2',
        'evidence_sha256': {str(path.relative_to(RUN)): sha(path) for path in artifacts},
        'cause': 'Old generic budget reserved a full second archive on Worker3 despite Worker2 being the selected archive host',
    }
    with OUTPUT.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    OUTPUT.chmod(0o600)
    print(json.dumps({'status': result['status'], 'sha256': sha(OUTPUT)}))


if __name__ == '__main__':
    main()
