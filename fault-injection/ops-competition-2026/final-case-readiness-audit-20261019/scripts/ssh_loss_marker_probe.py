#!/usr/bin/env python3
"""Harmless Marker under a real Master-to-Worker SSH control-socket outage."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
SAFETY = OPS / 'final-case-timer-safety-v1-20261019'
sys.path.insert(0, str(SAFETY / 'scripts'))
from marker_controller import remote

SOCKET = Path('/run/ops-sf013-timer/w2.sock')
ISOLATED = Path('/run/ops-sf013-timer/w2.sock.readiness-isolated')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def start(unit, script, registration, digest, guard=False):
    props = (['--property=Restart=on-failure', '--property=RestartSec=1s']
             if guard else [])
    subprocess.run(['systemd-run', '--unit=' + unit, '--collect',
                    '--property=RuntimeMaxSec=50', *props,
                    '/usr/bin/python3', '-B', str(script),
                    '--registration', str(registration),
                    '--registration-sha', digest],
                   check=True, capture_output=True, text=True, timeout=15)


def poll(test, seconds, label):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = test()
        if value:
            return value
        time.sleep(.1)
    raise RuntimeError('Timed out: ' + label)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if (args.output.exists() or not SOCKET.is_socket() or
            ISOLATED.exists()):
        raise RuntimeError('Fresh output and authenticated control socket required')
    token = '20261019-sshloss01'
    run_id = 'SF013-TIMER-MARKER-' + token
    root = SAFETY / 'markers' / run_id
    root.mkdir(parents=True, mode=0o700, exist_ok=False)
    planned = datetime.now(timezone.utc) + timedelta(seconds=24)
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
    result = {'status': 'SSH_LOSS_MARKER_PROBE_INCOMPLETE',
              'run_id': run_id, 'planned_utc': planned.isoformat(),
              'real_faults_injected': 0, 'socket_restored': False}
    isolated = False
    try:
        start(guard, SAFETY / 'scripts/marker_guard.py', registration,
              payload['registration_sha256'], guard=True)
        start(controller, SAFETY / 'scripts/marker_controller.py',
              registration, payload['registration_sha256'])
        poll(lambda: remote(payload, 'inspect')['armed'], 8,
             'Worker harmless timer armed')
        os.rename(SOCKET, ISOLATED)
        isolated = True
        attempted = subprocess.run(
            ['ssh', '-S', str(SOCKET), '-o', 'BatchMode=yes',
             '-o', 'ConnectTimeout=2', 'root@192.168.53.54', 'true'],
            capture_output=True, text=True, timeout=5)
        result['control_ssh_denied'] = attempted.returncode != 0
        result['control_ssh_error'] = attempted.stderr[-400:]
        if attempted.returncode == 0:
            raise RuntimeError('Control SSH still reachable during isolation')
        while time.time() < planned.timestamp() - 7:
            time.sleep(.1)
        subprocess.run(['systemctl', 'kill', '--signal=SIGKILL',
                        controller + '.service'], check=True,
                       capture_output=True, text=True, timeout=8)
        while time.time() < planned.timestamp() + 3:
            time.sleep(.1)
        result['guard_failure_recorded'] = (root / 'guard-terminal.json').is_file()
        result['guard_unit_state_at_trigger'] = subprocess.run(
            ['systemctl', 'show', guard + '.service', '-p', 'ActiveState',
             '-p', 'Result'], capture_output=True, text=True, timeout=8).stdout
    except BaseException as exc:
        result['error'] = type(exc).__name__ + ': ' + str(exc)
    finally:
        if isolated:
            try:
                os.rename(ISOLATED, SOCKET)
                result['socket_restored'] = SOCKET.is_socket()
            except OSError as exc:
                result['socket_restore_error'] = str(exc)
        if SOCKET.is_socket():
            try:
                worker = remote(payload, 'inspect')
                result['worker_inspection'] = worker
                result['final_worker_cancel'] = remote(payload, 'cancel')
                result['status'] = ('MASTER_SSH_LOSS_HARMLESS_MARKER_FIRED'
                    if not result.get('error') and result.get('control_ssh_denied') and worker['fired']
                    else 'MASTER_SSH_LOSS_HARMLESS_MARKER_SUPPRESSED'
                    if not result.get('error') and result.get('control_ssh_denied') and not worker['fired']
                    else 'SSH_LOSS_MARKER_PROBE_INCOMPLETE')
            except BaseException as exc:
                result['cleanup_error'] = type(exc).__name__ + ': ' + str(exc)
                result['status'] = 'SSH_LOSS_MARKER_CLEANUP_UNVERIFIED'
        for unit in (controller, guard):
            subprocess.run(['systemctl', 'stop', unit + '.service'],
                           capture_output=True, text=True, timeout=12)
        result['at_utc'] = datetime.now(timezone.utc).isoformat()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x') as stream:
            json.dump(result, stream, indent=2)
            stream.write('\n')
        print(json.dumps({'status': result['status'],
                          'socket_restored': result['socket_restored'],
                          'error': result.get('error')}), flush=True)
    if result['status'] == 'SSH_LOSS_MARKER_CLEANUP_UNVERIFIED':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
