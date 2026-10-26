#!/usr/bin/env python3
"""Master controller for harmless Worker2 timer lifecycle scenarios."""

import argparse
import hashlib
import json
import os
import shlex
import signal
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path('/root/ops-competition-2026/final-case-timer-safety-v1-20261019')
WORKER = ROOT / 'scripts/marker_worker.py'
SOCKET = '/run/ops-sf013-timer/w2.sock'


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def remote(reg, operation, *extra):
    argv = ['python3', '-B', str(WORKER), operation,
            '--run-id', reg['run_id'], '--action-id', reg['action_id'],
            '--unit', reg['timer_unit'], '--manifest-sha', reg['registration_sha256'],
            *extra]
    result = subprocess.run(['ssh', '-S', SOCKET, '-o', 'BatchMode=yes',
        'root@192.168.53.54', shlex.join(argv)], capture_output=True,
        text=True, timeout=25)
    if result.returncode:
        raise RuntimeError('MARKER_WORKER_' + operation.upper() + ': ' + result.stderr[-700:])
    return json.loads(result.stdout.splitlines()[-1])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--registration', type=Path, required=True)
    parser.add_argument('--registration-sha', required=True)
    parser.add_argument('--restart-reuse', action='store_true')
    args = parser.parse_args()
    if sha(args.registration) != args.registration_sha:
        raise RuntimeError('MARKER_REGISTRATION_HASH')
    reg = json.loads(args.registration.read_text())
    if (reg['run_id'] != args.registration.parent.name or
            reg['controller_unit'] != 'ops-sf013-marker-control-' +
            reg['run_id'].split('MARKER-', 1)[1].lower()):
        raise RuntimeError('MARKER_CONTROLLER_SCOPE')
    reg['registration_sha256'] = args.registration_sha
    scenario = 'restart_reuse' if args.restart_reuse else reg['scenario']
    root = args.registration.parent
    terminal = root / ('controller-restart-terminal.json' if scenario == 'restart_reuse'
                       else 'controller-terminal.json')
    if terminal.exists():
        raise RuntimeError('MARKER_CONTROLLER_TERMINAL_EXISTS')
    if scenario != 'restart_reuse':
        (root / 'controller-started.json').write_text(json.dumps(
            {'run_id': reg['run_id'], 'pid': os.getpid(), 'at_utc': now()}) + '\n')
    result = {'status': 'MARKER_CONTROLLER_ABORT', 'run_id': reg['run_id'],
              'scenario': scenario, 'worker_cleanup_verified': False}
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda value, frame:
                      (_ for _ in ()).throw(SystemExit(128 + value)))
    try:
        if scenario == 'identity_failure':
            raise RuntimeError('FROZEN_180_SECOND_IDENTITY_GATE')
        if scenario == 'capture_failure':
            unit = reg['controller_unit'] + '-capture'
            subprocess.run(['systemd-run', '--unit=' + unit, '--collect',
                '/usr/bin/false'], check=True, capture_output=True, timeout=15)
            time.sleep(0.3)
            active = subprocess.run(['systemctl', 'is-active', unit + '.service'],
                                    capture_output=True, text=True, timeout=5)
            if active.returncode != 0:
                raise RuntimeError('HARMLESS_CAPTURE_UNIT_FAILED')
        if scenario == 'capacity_failure':
            raise RuntimeError('INJECTED_FIVE_GB_FORECAST_STOP')
        if scenario != 'restart_reuse':
            remote(reg, 'owner', '--planned-utc', reg['planned_utc'],
                   '--controller-pid', str(os.getpid()))
        arm_args = ['--planned-utc', reg['planned_utc']]
        if scenario == 'arm_partial_failure':
            arm_args.append('--inject-error-after-install')
        remote(reg, 'arm', *arm_args)
        if scenario == 'duplicate_arm':
            remote(reg, 'arm', *arm_args)
        if scenario in ('normal', 'sigterm', 'sigkill', 'restart_first', 'cancel_then_wait'):
            expiry = datetime.fromisoformat(reg['planned_utc']) + timedelta(seconds=3)
            remote(reg, 'authorize', '--expires-utc', expiry.isoformat())
        if scenario in ('sigterm', 'sigkill', 'restart_first', 'cancel_then_wait'):
            while True:
                time.sleep(0.25)
        if scenario == 'normal':
            end = datetime.fromisoformat(reg['planned_utc']).timestamp() + 2
            while time.time() < end:
                time.sleep(0.1)
            observed = remote(reg, 'inspect')
            if observed['fired'] is not True:
                raise RuntimeError('NORMAL_HARMLESS_MARKER_DID_NOT_FIRE')
            result['status'] = 'NORMAL_HARMLESS_MARKER_EXECUTED'
        elif scenario == 'restart_reuse':
            raise RuntimeError('RESTART_REARM_UNEXPECTEDLY_ALLOWED')
    except BaseException as exc:
        result['error'] = type(exc).__name__ + ': ' + str(exc)
    finally:
        try:
            receipt = remote(reg, 'cancel')
            result['worker_cleanup_verified'] = (
                receipt.get('status') == 'ATTEMPT_TIMER_REVOKED_AND_INACTIVE')
            result['worker_cleanup'] = receipt
        except BaseException as exc:
            result['cleanup_error'] = type(exc).__name__ + ': ' + str(exc)
        result['completed_utc'] = now()
        with terminal.open('x') as stream:
            json.dump(result, stream, indent=2)
            stream.write('\n')
        print(json.dumps(result), flush=True)
    if not result['worker_cleanup_verified']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
