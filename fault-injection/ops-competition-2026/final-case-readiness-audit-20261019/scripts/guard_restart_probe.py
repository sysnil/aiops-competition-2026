#!/usr/bin/env python3
"""Harmless Worker Marker: verify Guard restarts and revokes after controller loss."""

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
SAFETY = OPS / 'final-case-timer-safety-v1-20261019'
sys.path.insert(0, str(SAFETY / 'scripts'))
from marker_controller import remote


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def start(unit, script, registration, digest, guard=False):
    props = (['--property=Restart=on-failure', '--property=RestartSec=1s']
             if guard else [])
    subprocess.run(['systemd-run', '--unit=' + unit, '--collect',
                    '--property=RuntimeMaxSec=45', *props,
                    '/usr/bin/python3', '-B', str(script),
                    '--registration', str(registration),
                    '--registration-sha', digest],
                   check=True, capture_output=True, text=True, timeout=15)


def poll(test, timeout, label):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        value = test()
        if value:
            return value
        time.sleep(.1)
    raise RuntimeError('Timed out: ' + label)


def show(unit, property_name):
    result = subprocess.run(['systemctl', 'show', unit + '.service',
                             '-p', property_name, '--value'],
                            check=True, capture_output=True, text=True, timeout=8)
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError('Probe output already exists')
    token = '20261019-guardrestart01'
    run_id = 'SF013-TIMER-MARKER-' + token
    root = SAFETY / 'markers' / run_id
    root.mkdir(parents=True, mode=0o700, exist_ok=False)
    planned = datetime.now(timezone.utc) + timedelta(seconds=20)
    controller = 'ops-sf013-marker-control-' + token
    guard = 'ops-sf013-marker-guard-' + token
    registration = root / 'registration.json'
    payload = {
        'status': 'PREREGISTERED_HARMLESS_MARKER', 'run_id': run_id,
        'action_id': run_id + '-MARK',
        'timer_unit': 'ops-sf013-' + (run_id + '-MARK').lower(),
        'controller_unit': controller, 'guard_unit': guard,
        'planned_utc': planned.isoformat(), 'scenario': 'sigkill',
        'real_fault_adapter_allowed': False}
    with registration.open('x') as stream:
        json.dump(payload, stream, indent=2)
        stream.write('\n')
    payload['registration_sha256'] = sha(registration)
    result = {'status': 'GUARD_RESTART_PROBE_FAILED', 'run_id': run_id,
              'planned_utc': planned.isoformat(), 'real_faults_injected': 0,
              'code_sha256': {str(SAFETY / 'scripts' / name):
                              sha(SAFETY / 'scripts' / name) for name in
                              ('marker_worker.py', 'marker_controller.py',
                               'marker_guard.py')}}
    try:
        start(guard, SAFETY / 'scripts/marker_guard.py', registration,
              payload['registration_sha256'], guard=True)
        start(controller, SAFETY / 'scripts/marker_controller.py',
              registration, payload['registration_sha256'])
        poll(lambda: remote(payload, 'inspect')['armed'], 8,
             'Worker Marker armed')
        before = show(guard, 'NRestarts')
        subprocess.run(['systemctl', 'kill', '--signal=SIGKILL',
                        guard + '.service'], check=True,
                       capture_output=True, text=True, timeout=8)
        restart = poll(lambda: int(show(guard, 'NRestarts')) > int(before),
                       8, 'Guard systemd restart')
        poll(lambda: show(guard, 'ActiveState') == 'active', 5,
             'Guard active after restart')
        restart_count = int(show(guard, 'NRestarts'))
        while time.time() < planned.timestamp() - 6:
            time.sleep(.1)
        subprocess.run(['systemctl', 'kill', '--signal=SIGKILL',
                        controller + '.service'], check=True,
                       capture_output=True, text=True, timeout=8)
        poll(lambda: (root / 'guard-terminal.json').is_file(),
             8, 'Guard cancel receipt')
        while time.time() < planned.timestamp() + 3:
            time.sleep(.1)
        worker = remote(payload, 'inspect')
        guard_terminal = json.loads((root / 'guard-terminal.json').read_text())
        result.update(
            guard_restart_count=restart_count,
            restart_observed=bool(restart), worker_inspection=worker,
            guard_terminal=guard_terminal,
            status=('GUARD_RESTART_AND_ORPHAN_CANCEL_PASS'
                    if not worker['fired'] and worker['revoked'] and
                    guard_terminal['status'] ==
                    'GUARD_ABORTED_ORPHANED_CONTROLLER' else
                    'GUARD_RESTART_PROBE_FAILED'))
    except BaseException as exc:
        result['error'] = type(exc).__name__ + ': ' + str(exc)
    finally:
        try:
            result['final_worker_cancel'] = remote(payload, 'cancel')
        except BaseException as exc:
            result['cleanup_error'] = type(exc).__name__ + ': ' + str(exc)
            result['status'] = 'GUARD_RESTART_CLEANUP_UNVERIFIED'
        for unit in (controller, guard):
            subprocess.run(['systemctl', 'stop', unit + '.service'],
                           capture_output=True, text=True, timeout=12)
        result['at_utc'] = datetime.now(timezone.utc).isoformat()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x') as stream:
            json.dump(result, stream, indent=2)
            stream.write('\n')
        print(json.dumps({'status': result['status'],
                          'error': result.get('error')}), flush=True)
    if result['status'] != 'GUARD_RESTART_AND_ORPHAN_CANCEL_PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
