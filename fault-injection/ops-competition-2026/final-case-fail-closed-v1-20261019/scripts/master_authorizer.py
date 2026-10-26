#!/usr/bin/env python3
"""Forced SSH command: one fresh authorization decision per bound action."""

import fcntl
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from auth_protocol import (boot_id, digest, load_private, sign_grant,
                           stamp, validate_request)


ROOT = Path('/root/ops-competition-2026/final-case-fail-closed-v1-20261019')
RUNTIME = ROOT / 'runtime'
SIGNING_KEY = Path('/root/ops-competition-2026/final-case-auth-secrets-20261019/master-signing.pem')
EXPECTED_ORIGINAL_COMMAND = 'final-case-auth-v1'
EXPECTED_CLIENT_IP = '192.168.53.54'


def unit_state(unit):
    if (not unit.startswith('ops-sf013-') or len(unit) > 180 or
            '/' in unit or ' ' in unit):
        raise ValueError('AUTH_UNIT_SCOPE')
    output = subprocess.check_output(
        ['systemctl', 'show', unit + '.service', '-p', 'ActiveState',
         '-p', 'MainPID', '-p', 'Result'], text=True, timeout=2)
    value = dict(row.split('=', 1) for row in output.splitlines() if '=' in row)
    if set(value) != {'ActiveState', 'MainPID', 'Result'}:
        raise ValueError('AUTH_UNIT_STATE_INCOMPLETE')
    return value


def read_json(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('AUTH_STATE_MISSING_OR_SYMLINK')
    return json.loads(path.read_text())


def authorize(req, at=None, state_root=RUNTIME, units=unit_state,
              current_boot=boot_id, private_key=None):
    at = time.time() if at is None else at
    validate_request(req, at)
    directory = state_root / req['run_id'] / req['action_id']
    if (directory.is_symlink() or not directory.is_dir() or
            not directory.resolve().is_relative_to(state_root.resolve())):
        raise ValueError('AUTH_ATTEMPT_STATE_DIRECTORY')
    lock = directory / 'decision.lock'
    with lock.open('a+') as guard:
        os.chmod(lock, 0o600)
        fcntl.flock(guard.fileno(), fcntl.LOCK_EX)
        state_path = directory / 'state.json'
        state = read_json(state_path)
        permit = read_json(directory / 'permit.json')
        if (directory / 'REVOKED.json').exists():
            raise ValueError('AUTH_ATTEMPT_REVOKED')
        if (directory / 'consumed.json').exists():
            raise ValueError('AUTH_ACTION_ALREADY_CONSUMED')
        for field in ('run_id', 'action_id', 'manifest_sha256',
                      'capability', 'worker_host', 'worker_boot_id'):
            if state.get(field) != req[field]:
                raise ValueError('AUTH_STATE_IDENTITY_' + field)
        if (state.get('status') != 'REGISTERED' or
                state.get('master_boot_id') != current_boot() or
                state.get('worker_host') != 'k8s-worker2' or
                permit.get('status') != 'PERMITTED' or
                permit.get('state_sha256') != digest(state) or
                permit.get('manifest_sha256') != req['manifest_sha256'] or
                permit.get('run_id') != req['run_id'] or
                permit.get('action_id') != req['action_id'] or
                not stamp(permit['issued_utc']) <= at <= stamp(permit['expires_utc'])):
            raise ValueError('AUTH_CURRENT_PERMISSION_INVALID')
        if not -.2 <= at - stamp(state['planned_utc']) <= 1.25:
            raise ValueError('AUTH_OUTSIDE_PLANNED_TRIGGER_WINDOW')
        marker_delay = permit.get('marker_test_delay_seconds', 0)
        if marker_delay:
            if (req['capability'] != 'MARKER_ONLY' or
                    not isinstance(marker_delay, (int, float)) or
                    not 0 < marker_delay <= 1.5):
                raise ValueError('AUTH_TEST_DELAY_SCOPE')
            time.sleep(marker_delay)
            at = time.time()
            if ((directory / 'REVOKED.json').exists() or
                    at > stamp(permit['expires_utc'])):
                raise ValueError('AUTH_REVOKED_OR_EXPIRED_DURING_CHECK')
        controller = units(state['controller_unit'])
        guard_unit = units(state['guard_unit'])
        if (controller['ActiveState'] != 'active' or
                controller['MainPID'] != str(state['controller_pid']) or
                int(controller['MainPID']) <= 0 or
                guard_unit['ActiveState'] != 'active' or
                int(guard_unit['MainPID']) <= 0):
            raise ValueError('AUTH_CONTROLLER_OR_GUARD_NOT_LIVE')
        signing = private_key or load_private(SIGNING_KEY)
        response = sign_grant(signing, req, current_boot(), at)
        receipt = {'status': 'ONE_SHOT_GRANT_CONSUMED',
                   'run_id': req['run_id'], 'action_id': req['action_id'],
                   'manifest_sha256': req['manifest_sha256'],
                   'nonce_sha256': digest(req['nonce']),
                   'request_sha256': digest(req),
                   'response_sha256': digest(response),
                   'at_utc': response['payload']['issued_utc']}
        path = directory / 'consumed.json'
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, json.dumps(receipt, sort_keys=True).encode() + b'\n')
            os.fsync(fd)
        finally:
            os.close(fd)
        return response


def main():
    if socket.gethostname() != 'k8s-master':
        raise RuntimeError('AUTH_MASTER_HOST_REQUIRED')
    if (os.environ.get('SSH_ORIGINAL_COMMAND') != EXPECTED_ORIGINAL_COMMAND or
            os.environ.get('SSH_CONNECTION', '').split()[:1] !=
            [EXPECTED_CLIENT_IP]):
        raise RuntimeError('AUTH_FORCED_SSH_IDENTITY')
    body = sys.stdin.buffer.read(8193)
    if len(body) > 8192:
        raise RuntimeError('AUTH_REQUEST_TOO_LARGE')
    try:
        response = authorize(json.loads(body))
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(json.dumps({'status': 'DENIED', 'reason': str(error)}), flush=True)
        raise SystemExit(1)
    print(json.dumps(response, sort_keys=True), flush=True)


if __name__ == '__main__':
    main()
