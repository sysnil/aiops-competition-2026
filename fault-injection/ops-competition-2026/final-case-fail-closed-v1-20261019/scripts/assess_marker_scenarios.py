#!/usr/bin/env python3
"""Read-only immutable assessment of real-systemd harmless auth scenarios."""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path('/root/ops-competition-2026/final-case-fail-closed-v1-20261019')
SCENARIOS = {
    'normal01': True,
    'denied01': False,
    'expired01': False,
    'revoked01': False,
    'w2net01': False,
    'guard02': False,
    'callback01': False,
    'identity01': False,
    'replay01': False,
    'm2w01': False,
    'interrupt01': False,
    'action01': False,
    'mboot01': False,
    'wboot01': False,
    'guardre01': True,
    'guardafter01': False,
    'controller01': False,
    'pairA01': False,
    'pairB01': True,
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError('Scenario summary cannot overwrite evidence')
    rows = []
    for name, expected_fire in SCENARIOS.items():
        path = ROOT / 'results' / (name + '.json')
        value = json.loads(path.read_text())
        worker = value['worker_inspection']
        states = worker['units'].values()
        checks = {
            'scenario_pass': value['status'] == 'LIVE_AUTH_MARKER_SCENARIO_PASS',
            'expected_marker': worker['fired'] == expected_fire,
            'entry_started_or_guard_cancelled': worker['started'] or
                value['scenario'] in ('controller_exit',
                                      'guard_restart_then_controller_exit'),
            'no_pending_timer_or_service': all(
                state['ActiveState'] not in ('active', 'activating', 'reloading')
                for state in states),
            'exact_attempt_cleanup': value['final_worker_cancel']['status'] ==
                'ATTEMPT_TIMER_REVOKED_AND_INACTIVE',
            'no_real_fault': value['real_faults_injected'] == 0,
        }
        if name == 'm2w01':
            checks['socket_restored'] = value['master_worker_socket_restored']
            checks['failed_guard_recorded'] = value['guard_failure_present']
        if name == 'interrupt01':
            checks['inflight_ssh_killed'] = value['inflight_ssh_client_killed']
        if name == 'guardre01':
            checks['guard_restarted'] = value['guard_restart_count'] >= 1
        if name == 'guardafter01':
            checks['guard_restarted_then_cancelled'] = (
                value['guard_restart_count'] >= 1 and
                value['guard_terminal_present'])
        rows.append({'id': name, 'scenario': value['scenario'],
                     'result_sha256': sha(path), 'checks': checks,
                     'pass': all(checks.values()),
                     'actual_fired': worker['fired'],
                     'worker_entry_started': worker['started'],
                     'master_grant_consumed': value['master_grant_consumed']})
    historical_guard = ROOT / 'results/guard01.json'
    guard_first = json.loads(historical_guard.read_text())
    result = {
        'status': 'FAIL_CLOSED_MARKER_MATRIX_PASS' if all(row['pass']
                   for row in rows) else 'FAIL_CLOSED_MARKER_MATRIX_BLOCKED',
        'at_utc': datetime.now(timezone.utc).isoformat(),
        'scenarios': rows,
        'passed': sum(row['pass'] for row in rows),
        'total': len(rows),
        'first_guard_crash_result_preserved': {
            'sha256': sha(historical_guard),
            'status': guard_first['status'],
            'marker_fired_after_auto_restart':
                guard_first['worker_inspection']['fired'],
            'interpretation': 'Guard crashed and automatically restarted; '
                              'it was active when authorization was granted.'},
        'real_faults_injected': 0,
        'formal_attempt_created': False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': result['status'],
                      'passed': result['passed'], 'total': result['total']}))
    if result['status'] != 'FAIL_CLOSED_MARKER_MATRIX_PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
