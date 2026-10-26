#!/usr/bin/env python3
"""Audit Worker3's new harmless Timer scenarios without touching v4 evidence."""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path('/root/ops-competition-2026/final-case-production-approval-20261019/worker3-auth/results')
EXPECTED = {
    'w3normal01': True, 'w3deny01': False, 'w3rev01': False,
    'w3net01': False, 'w3m2w01': False, 'w3guard01': False,
    'w3pairA': False, 'w3pairB': True,
    'w3final-normal': True, 'w3final-denied': False,
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError('Immutable Worker3 scenario summary exists')
    rows = []
    for name, expected_fire in EXPECTED.items():
        path = ROOT / (name + '.json')
        data = json.loads(path.read_text())
        worker = data['worker_inspection']
        checks = {
            'scenario_pass': data['status'] == 'LIVE_AUTH_MARKER_SCENARIO_PASS',
            'entered_worker_timer': worker['started'],
            'expected_marker': worker['fired'] == expected_fire,
            'no_pending_unit': all(
                unit['ActiveState'] not in ('active', 'activating', 'reloading')
                for unit in worker['units'].values()),
            'exact_cleanup': data['final_worker_cancel']['status'] ==
                'ATTEMPT_TIMER_REVOKED_AND_INACTIVE',
            'no_real_fault': data['real_faults_injected'] == 0,
        }
        if name == 'w3m2w01':
            checks['control_socket_restored'] = data['master_worker_socket_restored']
            checks['guard_failure_recorded'] = data['guard_failure_present']
        rows.append({'id': name, 'scenario': data['scenario'],
                     'file_sha256': sha(path), 'checks': checks,
                     'pass': all(checks.values()),
                     'marker_fired': worker['fired']})
    result = {'status': 'WORKER3_MARKER_EXTENSION_PASS' if all(
        row['pass'] for row in rows) else 'WORKER3_MARKER_EXTENSION_BLOCKED',
        'at_utc': datetime.now(timezone.utc).isoformat(),
        'passed': sum(row['pass'] for row in rows), 'total': len(rows),
        'rows': rows, 'real_faults_injected': 0,
        'formal_service_pause_adapter_wired': False,
        'production_permission': False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': result['status'],
                      'passed': result['passed'], 'total': result['total']}))
    if result['status'] != 'WORKER3_MARKER_EXTENSION_PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
