"""Shared Worker2/Worker3 effect gate; real adapters require separate qualification."""

from pathlib import Path

from action_decision import read_bound
from entry_policy import validate_action
import worker_auth


def worker_auth_module(worker):
    if worker not in ('k8s-worker2', 'k8s-worker3'):
        raise ValueError('WORKER_GATE_HOST_SCOPE')
    return worker_auth


def execute(action, runtime, effect, *, auth_module=None, marker=False):
    validate_action(action, marker=marker)
    runtime = Path(runtime)
    binding = read_bound(runtime / 'auth-binding.json')
    for key in ('run_id', 'action_id', 'manifest_sha256', 'capability', 'worker_host'):
        expected = action['worker'] if key == 'worker_host' else action[key]
        if binding.get(key) != expected:
            raise ValueError('WORKER_BINDING_' + key)
    auth = auth_module or worker_auth_module(action['worker'])

    def checked_effect(grant):
        if (runtime / 'REVOKED.json').exists():
            raise ValueError('WORKER_LOCAL_REVOKED_BEFORE_EFFECT')
        if marker and action['effect'] != 'MARKER':
            raise ValueError('WORKER_MARKER_ONLY')
        return effect(grant)

    return auth.execute_once(binding, runtime, checked_effect)
