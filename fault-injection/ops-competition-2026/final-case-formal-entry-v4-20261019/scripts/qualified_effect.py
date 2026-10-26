"""Only qualified fault adapter binding; no legacy fault_worker.run route."""

import importlib.util
import json
import time
from pathlib import Path

from control_contract import qualification_scope, sha


ADAPTER = Path('/root/ops-competition-2026/sf013-adapter-v4-20261019/scripts/qualified_adapter.py')


def adapter_for(action):
    qualification = action.get('qualification', {})
    receipt = json.loads(Path(qualification['receipt_path']).read_text())
    if (qualification.get('status') != 'QUALIFIED_FOR_THIS_VERSION' or
            receipt.get('status') != 'QUALIFIED_FOR_THIS_VERSION' or
            receipt.get('run_id') != action['run_id'] or
            receipt.get('action_id') != action['action_id'] or
            receipt.get('scope_sha256') != qualification_scope(action) or
            receipt.get('adapter_sha256') != sha(ADAPTER) or
            sha(qualification['receipt_path']) != qualification['receipt_sha256']):
        raise ValueError('EFFECT_ADAPTER_QUALIFICATION_MISMATCH')
    spec = importlib.util.spec_from_file_location('qualified_final_effect', ADAPTER)
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    adapter.IDS[action['action_id']] = (
        'POD' if action['kind'] == 'POD' else 'SERVICE', action['duration_seconds'])
    return adapter


def run(action, runtime, grant, *, loader=adapter_for, clock=time.time):
    if action['kind'] not in ('POD', 'SERVICE'):
        raise ValueError('EFFECT_NOT_WORKER_MECHANISM')
    adapter = loader(action)
    root = Path(runtime)

    def before_effect():
        if ((root / 'REVOKED.json').exists() or
                clock() >= adapter_time(grant['expires_utc'])):
            raise RuntimeError('EFFECT_GRANT_EXPIRED_OR_REVOKED')

    result = adapter.run(Path(action['qualified_registration']),
                         root / 'authorization.json',
                         Path(action['injection_output']),
                         before_effect=before_effect)
    if (result.get('status') !=
            'COMPETITION_BOUNDED_INJECTION_COMPLETED_NOT_YET_QUALIFIED' or
            result.get('cleanup', {}).get('status') !=
            'COMPETITION_CLEANUP_ACTIONS_VERIFIED'):
        raise RuntimeError('EFFECT_OR_RECOVERY_FAILED')
    return result


def adapter_time(value):
    from datetime import datetime
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def cleanup(action, *, loader=adapter_for):
    adapter = loader(action)
    state = adapter.RUNTIME / (action['action_id'] + '.json')
    if state.exists():
        return adapter.cleanup(state)
    return {'status': 'NO_QUALIFIED_EFFECT_STATE'}
