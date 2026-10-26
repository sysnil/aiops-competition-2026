"""Close the SF-007 attempt stopped by conflicting storage gate semantics."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


RUN = Path('/data/stage5-v3/ops-competition-2026/runs/SF-007-ATTEMPT-04')
OUTPUT = RUN / 'attempt-failure.json'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def main():
    if OUTPUT.exists() or (RUN / 'public').exists():
        raise RuntimeError('Failure closure already exists or Public was created')
    control = RUN / 'control'
    capture_path = RUN / 'terminal.json'
    onset_path = control / 'capture-failure-onset.json'
    workflow_path = control / 'fault-workflow.jsonl'
    injection_path = control / 'worker3-private/injection-result.json'
    monitor_path = control / 'five-host-live.jsonl'
    capture, onset, injection = map(read, (capture_path, onset_path, injection_path))
    workflow = [json.loads(line) for line in workflow_path.read_text().splitlines()]
    monitor = [json.loads(line) for line in monitor_path.read_text().splitlines()]
    pending = [row for row in monitor if row.get('status') == 'DYNAMIC_STORAGE_PASS'
               and row.get('decision', {}).get('pending_forecast_breaches', {}).get(
                   'worker1:data_free_bytes') == 1]
    checks = {
        'capture_stopped_incomplete': capture['status'] == 'SF007_FORMAL_CAPTURE_FAILED'
            and capture['error'] == 'SystemExit: 143' and onset['error'] == 'SystemExit: 143',
        'controller_rejected_v2_pending_breach': any(
            row.get('phase') == 'ABORTING' and row.get('error') ==
            'RuntimeError: Five-host disk forecast breaches the floor' for row in workflow),
        'v2_monitor_reported_pending_pass': len(pending) == 1,
        'fault_recovered': injection['status'] ==
            'COMPETITION_BOUNDED_INJECTION_COMPLETED_NOT_YET_QUALIFIED'
            and injection['cleanup']['status'] == 'COMPETITION_CLEANUP_ACTIONS_VERIFIED'
            and len(injection['cleanup']['continued_pids']) == 2,
        'final_health': read(control / 'final-live-health.json')['status'] ==
            'FINAL_LIVE_ENVIRONMENT_PASS',
        'five_host_residue': read(control / 'final-residue.json')['status'] ==
            'HOST_RESIDUE_PASS',
        'competition_units_clear': read(control / 'final-units.json')['status'] ==
            'COMPETITION_UNIT_RESIDUE_PASS',
    }
    if not all(checks.values()):
        raise RuntimeError('Failure evidence incomplete: ' +
                           ', '.join(name for name, passed in checks.items() if not passed))
    artifacts = [capture_path, onset_path, workflow_path, injection_path, monitor_path,
                 control / 'worker3-private/private-samples.jsonl',
                 control / 'final-live-health.json', control / 'final-residue.json',
                 control / 'final-units.json', control / 'final-storage.json']
    result = {
        'status': 'FAILED_STORAGE_GATE_SEMANTICS_MISMATCH_AFTER_RECOVERY',
        'run_id': 'SF-007-ATTEMPT-04', 'case_id': 'SF-007',
        'closed_utc': datetime.now(timezone.utc).isoformat(),
        'fault_injected': True, 'fault_recovered': True,
        'capture_complete': False, 'public_bundle_created': False,
        'private_gt_created': False, 'ready_for_import': False,
        'checks': checks,
        'pending_worker1_snapshot_free_bytes': pending[0]['snapshot']['worker1']['data_free_bytes'],
        'pending_worker1_forecast_spend_bytes': pending[0]['forecast']['worker1']['data_free_bytes'],
        'five_gb_floor_bytes': 5_000_000_000,
        'evidence_sha256': {str(path.relative_to(RUN)): sha(path) for path in artifacts},
        'cause': 'Mac v2 heartbeat and Master preflight used incompatible forecast semantics',
    }
    with OUTPUT.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    OUTPUT.chmod(0o600)
    print(json.dumps({'status': result['status'], 'sha256': sha(OUTPUT)}))


if __name__ == '__main__':
    main()
