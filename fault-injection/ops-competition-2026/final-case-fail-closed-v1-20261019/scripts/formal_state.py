"""Master-owned live permission state for a future formally registered action."""

import os
import time
from pathlib import Path

from auth_protocol import boot_id, digest, now_utc, stamp
from handshake import atomic


ROOT = Path('/root/ops-competition-2026/final-case-fail-closed-v1-20261019/runtime')


def register(manifest, manifest_sha, plan, action, worker_boot_id):
    if (manifest['run_id'] != action['run_id'] or
            action['worker'] != 'k8s-worker2' or
            plan['controller_unit'] + '-guard' != plan['timer_guard_unit']):
        raise RuntimeError('FORMAL_AUTH_ATTEMPT_SCOPE')
    directory = ROOT / manifest['run_id'] / action['action_id']
    directory.mkdir(parents=True, mode=0o700, exist_ok=False)
    state = {'status': 'REGISTERED',
             'run_id': manifest['run_id'],
             'action_id': action['action_id'],
             'manifest_sha256': manifest_sha,
             'capability': 'BOUND_QUALIFIED_FAULT',
             'worker_host': 'k8s-worker2',
             'worker_boot_id': worker_boot_id,
             'master_boot_id': boot_id(),
             'planned_utc': action['planned_utc'],
             'controller_unit': plan['controller_unit'],
             'controller_pid': os.getpid(),
             'guard_unit': plan['timer_guard_unit']}
    atomic(directory / 'state.json', state, True)
    return directory, state


def permit(directory, state, manifest_sha, preflight_sha):
    if (state['capability'] != 'BOUND_QUALIFIED_FAULT' or
            state['manifest_sha256'] != manifest_sha or
            not preflight_sha or
            time.time() >= stamp(state['planned_utc'])):
        raise RuntimeError('FORMAL_AUTH_PREFLIGHT_OR_TIME')
    value = {'status': 'PERMITTED', 'run_id': state['run_id'],
             'action_id': state['action_id'],
             'manifest_sha256': manifest_sha,
             'state_sha256': digest(state),
             'preflight_sha256': preflight_sha,
             'issued_utc': now_utc(),
             'expires_utc': now_utc(stamp(state['planned_utc']) + 2)}
    atomic(Path(directory) / 'permit.json', value, True)
    return value


def revoke(directory, reason):
    directory = Path(directory)
    if not directory.is_dir():
        return False
    path = directory / 'REVOKED.json'
    if path.exists():
        return True
    atomic(path, {'status': 'REVOKED', 'reason': reason,
                  'at_utc': now_utc()}, True)
    return True
