"""Close the stopped SF-007 attempt without fabricating a capture terminal."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


RUN = Path('/data/stage5-v3/ops-competition-2026/runs/SF-007-ATTEMPT-02')
OUTPUT = RUN / 'attempt-failure.json'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def main():
    if OUTPUT.exists() or (RUN / 'terminal.json').exists() or (RUN / 'public').exists():
        raise RuntimeError('Cannot overwrite or relabel an existing capture')
    control = RUN / 'control'
    workflow_path = control / 'fault-controller-terminal.json'
    injection_path = control / 'worker3-private/injection-result.json'
    onset_path = control / 'capture-failure-onset.json'
    monitor_path = control / 'five-host-live.jsonl'
    workflow, injection, onset = map(read, (workflow_path, injection_path, onset_path))
    monitor = [json.loads(line) for line in monitor_path.read_text().splitlines()]
    stopped = [row for row in monitor if row['status'] == 'STOP_NEW_WORK']
    checks = {
        'fault_controller_failed_closed': workflow['status'] == 'SF007_FORMAL_SEQUENCE_FAILED'
            and workflow.get('error') == 'SystemExit: 143',
        'actual_service_injection_recovered': injection['status'] ==
            'COMPETITION_BOUNDED_INJECTION_COMPLETED_NOT_YET_QUALIFIED'
            and injection['cleanup']['status'] == 'COMPETITION_CLEANUP_ACTIONS_VERIFIED'
            and len(injection['cleanup']['continued_pids']) == 2,
        'capture_received_stop': onset['error'] == 'SystemExit: 143',
        'storage_gate_stopped_work': len(stopped) == 1 and monitor[-1]['status'] ==
            'FIVE_HOST_STORAGE_HEARTBEAT_FAILED',
        'worker1_forecast_below_floor': len(stopped) == 1 and
            stopped[0]['snapshot']['worker1']['data_free_bytes'] -
            stopped[0]['forecast']['worker1']['data_free_bytes'] < 5_000_000_000,
        'final_health': read(control / 'final-live-health.json')['status'] ==
            'FINAL_LIVE_ENVIRONMENT_PASS',
        'five_host_residue': read(control / 'final-residue.json')['status'] ==
            'HOST_RESIDUE_PASS',
        'competition_units_clear': read(control / 'final-units.json')['status'] ==
            'COMPETITION_UNIT_RESIDUE_PASS',
    }
    if not all(checks.values()):
        raise RuntimeError('Failure closure evidence incomplete: ' +
                           ', '.join(name for name, passed in checks.items() if not passed))
    artifacts = [workflow_path, injection_path, onset_path, monitor_path,
                 control / 'worker3-private/private-samples.jsonl',
                 control / 'final-live-health.json', control / 'final-residue.json',
                 control / 'final-units.json', control / 'final-storage.json']
    result = {
        'status': 'FAILED_DYNAMIC_STORAGE_FORECAST_AFTER_RECOVERY',
        'run_id': 'SF-007-ATTEMPT-02', 'case_id': 'SF-007',
        'closed_utc': datetime.now(timezone.utc).isoformat(),
        'fault_injected': True, 'fault_recovered': True,
        'capture_complete': False, 'public_bundle_created': False,
        'private_gt_created': False, 'ready_for_import': False,
        'checks': checks,
        'worker1_snapshot_free_bytes_at_stop': stopped[0]['snapshot']['worker1']['data_free_bytes'],
        'worker1_forecast_spend_bytes_at_stop': stopped[0]['forecast']['worker1']['data_free_bytes'],
        'five_gb_floor_bytes': 5_000_000_000,
        'evidence_sha256': {str(path.relative_to(RUN)): sha(path) for path in artifacts},
        'no_normal_capture_terminal_fabricated': True,
    }
    with OUTPUT.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    OUTPUT.chmod(0o600)
    print(json.dumps({'status': result['status'],
                      'failed_checks': [name for name, passed in checks.items() if not passed],
                      'sha256': sha(OUTPUT)}))


if __name__ == '__main__':
    main()
