"""Master-side signed grant; v4 Ed25519/nonce protocol with serialized revoke."""

import json
import hashlib
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from action_decision import create_once, decision_lock, read_bound
import auth_protocol

RUNTIME = Path('/root/ops-competition-2026/final-case-unified-execution-v2-20261019/runtime')
SIGNING_KEY = Path('/root/ops-competition-2026/final-case-auth-secrets-20261019/master-signing.pem')
WORKERS = {'k8s-worker2': '192.168.53.54', 'k8s-worker3': '192.168.53.55'}
CASES = {'SF-013': {'k8s-worker2'}, 'SF-014': {'k8s-worker3'},
         'SF-016': {'k8s-worker2'}, 'SF-017': {'k8s-worker3'}}


def protocol(worker):
    if worker not in WORKERS:
        raise ValueError('AUTH_UNKNOWN_WORKER')
    return auth_protocol


def unit_state(unit):
    if not unit.startswith('ops-sf0') or len(unit) > 180 or '/' in unit or ' ' in unit:
        raise ValueError('AUTH_UNIT_SCOPE')
    output = subprocess.check_output(
        ['systemctl', 'show', unit + '.service', '-p', 'ActiveState',
         '-p', 'MainPID', '-p', 'Result'], text=True, timeout=2)
    value = dict(row.split('=', 1) for row in output.splitlines() if '=' in row)
    if set(value) != {'ActiveState', 'MainPID', 'Result'}:
        raise ValueError('AUTH_UNIT_STATE_INCOMPLETE')
    return value


def authorize(req, *, state_root=RUNTIME, units=unit_state,
              current_boot=None, private_key=None, clock=time.time):
    worker = req.get('worker_host')
    if worker not in WORKERS:
        raise ValueError('AUTH_UNKNOWN_WORKER')
    api = protocol(worker)
    now = clock()
    api.validate_request(req, now)
    case = req['run_id'].split('-ATTEMPT-', 1)[0]
    if case not in CASES or worker not in CASES[case]:
        raise ValueError('AUTH_CASE_WORKER_SCOPE')
    directory = Path(state_root) / req['run_id'] / req['action_id']
    if (directory.is_symlink() or not directory.is_dir() or
            not directory.resolve().is_relative_to(Path(state_root).resolve())):
        raise ValueError('AUTH_ATTEMPT_STATE_DIRECTORY')
    with decision_lock(directory):
        state = read_bound(directory / 'state.json')
        permit = read_bound(directory / 'permit.json')
        manifest = Path(state.get('manifest_path', ''))
        if (manifest.is_symlink() or not manifest.is_file() or
                hashlib.sha256(manifest.read_bytes()).hexdigest() !=
                req['manifest_sha256']):
            raise ValueError('AUTH_MANIFEST_FILE_HASH')
        if ((directory / 'REVOKED.json').exists() or
                (directory / 'REVOCATION_REQUESTED.json').exists()):
            raise ValueError('AUTH_ATTEMPT_REVOKED')
        if (directory / 'consumed.json').exists():
            raise ValueError('AUTH_ACTION_ALREADY_CONSUMED')
        for field in ('run_id', 'action_id', 'manifest_sha256',
                      'capability', 'worker_host', 'worker_boot_id'):
            if state.get(field) != req[field]:
                raise ValueError('AUTH_STATE_IDENTITY_' + field)
        boot = (current_boot or api.boot_id)()
        if (state.get('status') != 'REGISTERED' or
                state.get('master_boot_id') != boot or
                permit.get('status') != 'PERMITTED' or
                permit.get('state_sha256') != api.digest(state) or
                any(permit.get(k) != req[k] for k in
                    ('run_id', 'action_id', 'manifest_sha256')) or
                not api.stamp(permit['issued_utc']) <= now <= api.stamp(permit['expires_utc']) or
                not -.2 <= now - api.stamp(state['planned_utc']) <= 1.25):
            raise ValueError('AUTH_CURRENT_PERMISSION_INVALID')
        controller = units(state['controller_unit'])
        guard = units(state['guard_unit'])
        if (controller['ActiveState'] != 'active' or
                controller['MainPID'] != str(state['controller_pid']) or
                int(controller['MainPID']) <= 0 or
                guard['ActiveState'] != 'active' or
                int(guard['MainPID']) <= 0):
            raise ValueError('AUTH_CONTROLLER_OR_GUARD_NOT_LIVE')
        # These checks are inside the same lock used by revoke and permit.
        now = clock()
        if ((directory / 'REVOKED.json').exists() or
                (directory / 'REVOCATION_REQUESTED.json').exists() or
                (current_boot or api.boot_id)() != boot):
            raise ValueError('AUTH_REVOKED_OR_BOOT_CHANGED_BEFORE_SIGN')
        if (now > api.stamp(permit['expires_utc']) or
                not -.2 <= now - api.stamp(state['planned_utc']) <= 1.25 or
                read_bound(directory / 'state.json') != state or
                read_bound(directory / 'permit.json') != permit or
                hashlib.sha256(manifest.read_bytes()).hexdigest() !=
                req['manifest_sha256']):
            raise ValueError('AUTH_STATE_CHANGED_OR_EXPIRED_BEFORE_SIGN')
        controller = units(state['controller_unit'])
        guard = units(state['guard_unit'])
        if (controller['ActiveState'] != 'active' or
                controller['MainPID'] != str(state['controller_pid']) or
                int(controller['MainPID']) <= 0 or
                guard['ActiveState'] != 'active' or
                int(guard['MainPID']) <= 0 or
                (directory / 'REVOCATION_REQUESTED.json').exists()):
            raise ValueError('AUTH_CONTROLLER_GUARD_OR_REVOKE_CHANGED')
        signing = private_key or api.load_private(SIGNING_KEY)
        response = api.sign_grant(signing, req, boot, now)
        create_once(directory / 'consumed.json', {
            'status': 'ONE_SHOT_GRANT_CONSUMED',
            'run_id': req['run_id'], 'action_id': req['action_id'],
            'manifest_sha256': req['manifest_sha256'],
            'nonce_sha256': api.digest(req['nonce']),
            'request_sha256': api.digest(req),
            'response_sha256': api.digest(response),
            'at_utc': response['payload']['issued_utc']})
        return response


def main():
    if socket.gethostname() != 'k8s-master' or os.environ.get('SSH_ORIGINAL_COMMAND') != 'final-case-auth-v1':
        raise RuntimeError('AUTH_MASTER_OR_COMMAND')
    source = os.environ.get('SSH_CONNECTION', '').split()[:1]
    body = sys.stdin.buffer.read(8193)
    if len(body) > 8192:
        raise RuntimeError('AUTH_REQUEST_TOO_LARGE')
    try:
        req = json.loads(body)
        if source != [WORKERS.get(req.get('worker_host'))]:
            raise ValueError('AUTH_FORCED_SSH_SOURCE')
        response = authorize(req)
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(json.dumps({'status': 'DENIED', 'reason': str(error)}), flush=True)
        raise SystemExit(1)
    print(json.dumps(response, sort_keys=True), flush=True)


if __name__ == '__main__':
    main()
