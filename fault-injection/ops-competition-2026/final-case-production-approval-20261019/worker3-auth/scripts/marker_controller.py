#!/usr/bin/env python3
"""Master-owned controller for preregistered harmless live-auth Markers."""

import argparse
import hashlib
import json
import os
import shlex
import signal
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from auth_protocol import boot_id, digest, now_utc, stamp
from handshake import atomic


ROOT = Path('/root/ops-competition-2026/final-case-production-approval-20261019/worker3-auth')
SOCKET = '/run/ops-sf014-auth/w3.sock'
WORKER_SCRIPT = ROOT / 'scripts/marker_worker.py'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def remote(reg, operation, *extra):
    args = ['python3', '-B', str(WORKER_SCRIPT), operation,
            '--run-id', reg['run_id'], '--action-id', reg['action_id'],
            '--unit', reg['unit'], '--manifest-sha', reg['manifest_sha256'],
            *extra]
    response = subprocess.run(
        ['ssh', '-S', SOCKET, '-o', 'BatchMode=yes',
         'root@192.168.53.55', shlex.join(args)],
        capture_output=True, text=True, timeout=8)
    if response.returncode:
        raise RuntimeError('WORKER_' + operation.upper() + '_UNAVAILABLE: ' +
                           response.stderr[-350:])
    return json.loads(response.stdout.splitlines()[-1])


def register_auth_state(reg, root):
    directory = ROOT / 'runtime' / reg['run_id'] / reg['action_id']
    directory.mkdir(parents=True, mode=0o700, exist_ok=False)
    state = {'status': 'REGISTERED', 'run_id': reg['run_id'],
             'action_id': (reg['action_id'] + '-OTHER' if
                           reg['scenario'] == 'action_mismatch' else
                           reg['action_id']),
             'manifest_sha256': ('b' * 64 if reg['scenario'] == 'identity_mismatch'
                                 else reg['manifest_sha256']),
             'capability': 'MARKER_ONLY',
             'worker_host': reg['worker_host'],
             'worker_boot_id': reg['worker_boot_id'],
             'master_boot_id': ('33333333-3333-3333-3333-333333333333'
                                if reg['scenario'] == 'master_boot_mismatch' else
                                reg['master_boot_id']),
             'planned_utc': reg['planned_utc'],
             'controller_unit': reg['controller_unit'],
             'controller_pid': os.getpid(),
             'guard_unit': reg['guard_unit']}
    atomic(directory / 'state.json', state, True)
    if reg['scenario'] != 'master_denied':
        issued = time.time()
        expires = (stamp(reg['planned_utc']) - 1 if
                   reg['scenario'] == 'expired_permission' else
                   stamp(reg['planned_utc']) + 2)
        permit = {'status': 'PERMITTED',
                  'run_id': reg['run_id'], 'action_id': reg['action_id'],
                  'manifest_sha256': reg['manifest_sha256'],
                  'state_sha256': digest(state),
                  'issued_utc': now_utc(issued),
                  'expires_utc': now_utc(expires)}
        if reg['scenario'] == 'auth_interrupted':
            permit['marker_test_delay_seconds'] = 1.5
        atomic(directory / 'permit.json', permit, True)
    if reg['scenario'] == 'revoked':
        atomic(directory / 'REVOKED.json',
               {'status': 'REVOKED', 'at_utc': now_utc()}, True)
    if reg['scenario'] == 'replay_preconsumed':
        atomic(directory / 'consumed.json',
               {'status': 'TEST_ONLY_PRIOR_GRANT_CONSUMED',
                'at_utc': now_utc()}, True)
    return directory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--registration', type=Path, required=True)
    parser.add_argument('--registration-sha', required=True)
    args = parser.parse_args()
    if socket.gethostname() != 'k8s-master' or sha(args.registration) != args.registration_sha:
        raise RuntimeError('MARKER_MASTER_AND_REGISTRATION_REQUIRED')
    reg = json.loads(args.registration.read_text())
    reg['manifest_sha256'] = args.registration_sha
    if (reg['status'] != 'PREREGISTERED_HARMLESS_LIVE_AUTH_MARKER' or
            reg['run_id'] != args.registration.parent.name or
            reg['capability'] != 'MARKER_ONLY' or
            reg['real_fault_adapter_allowed'] is not False or
            reg['master_boot_id'] != boot_id()):
        raise RuntimeError('MARKER_MASTER_SCOPE')
    root = args.registration.parent
    atomic(root / 'controller-started.json',
           {'run_id': reg['run_id'], 'pid': os.getpid(), 'at_utc': now_utc()}, True)
    result = {'status': 'MARKER_CONTROLLER_FAILED',
              'run_id': reg['run_id'], 'worker_cleanup_verified': False}
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda value, frame:
                      (_ for _ in ()).throw(SystemExit(128 + value)))
    try:
        remote(reg, 'owner', '--controller-pid', str(os.getpid()))
        remote(reg, 'arm')
        register_auth_state(reg, root)
        atomic(root / 'controller-ready.json',
               {'status': 'MARKER_CONTROLLER_READY', 'at_utc': now_utc(),
                'run_id': reg['run_id']}, True)
        while time.time() < stamp(reg['planned_utc']) + 3:
            time.sleep(.1)
        result['status'] = 'MARKER_CONTROLLER_COMPLETED'
    except BaseException as exc:
        result['error'] = type(exc).__name__ + ': ' + str(exc)
    finally:
        try:
            receipt = remote(reg, 'cancel')
            result['worker_cleanup_verified'] = (
                receipt['status'] == 'ATTEMPT_TIMER_REVOKED_AND_INACTIVE')
            result['worker_cleanup'] = receipt
        except BaseException as exc:
            result['cleanup_error'] = type(exc).__name__ + ': ' + str(exc)
        auth_dir = ROOT / 'runtime' / reg['run_id'] / reg['action_id']
        if auth_dir.is_dir() and not (auth_dir / 'REVOKED.json').exists():
            atomic(auth_dir / 'REVOKED.json',
                   {'status': 'REVOKED', 'at_utc': now_utc()}, True)
        result['completed_utc'] = now_utc()
        atomic(root / 'controller-terminal.json', result, True)
        print(json.dumps(result), flush=True)
    if not result['worker_cleanup_verified']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
