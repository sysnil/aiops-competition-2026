"""DRAFT allowlist for new-case actions; historical lease entrypoints are excluded."""

import re
from pathlib import Path


CASE_ACTIONS = {
    'SF-013': {'POD': ('k8s-worker2', 'PodMemoryStress', 300)},
    'SF-014': {'SERVICE': ('k8s-worker3', 'ServicePause', 300)},
    'SF-015': {'HTTP': ('k8s-master', 'HTTPReqDelay', 300)},
    'SF-016': {'POD': ('k8s-worker2', 'PodMemoryStress', 300)},
    'SF-017': {'HTTP': ('k8s-master', 'HTTPReqDelay', 240),
               'SERVICE': ('k8s-worker3', 'ServicePause', 240)},
}
LEGACY_ENTRY_NAMES = {'sf007-real-adapter-v7-20261018',
                      'sf011-http-adapter-v5-20261018',
                      'sf012-pod300-adapter-v4-20261018'}
ROOT = Path('/root/ops-competition-2026/final-case-unified-execution-v2-20261019/scripts')
HEX64 = re.compile(r'^[0-9a-f]{64}$')


def validate_action(action, *, marker=False):
    run_id = action.get('run_id', '')
    case = run_id.split('-ATTEMPT-', 1)[0]
    if case not in CASE_ACTIONS or '-ATTEMPT-' not in run_id:
        raise ValueError('UNREGISTERED_CASE_OR_ATTEMPT')
    action_id = action.get('action_id')
    if action_id != run_id + '-' + action.get('kind', ''):
        raise ValueError('ACTION_ATTEMPT_IDENTITY')
    expected = CASE_ACTIONS[case].get(action['kind'])
    if expected != (action.get('worker'), action.get('mechanism'),
                    action.get('duration_seconds')):
        raise ValueError('ACTION_HOST_MECHANISM_BOUNDS')
    if not HEX64.fullmatch(action.get('manifest_sha256', '')):
        raise ValueError('ACTION_MANIFEST_HASH')
    entry = Path(action.get('entrypoint', ''))
    if any(part in LEGACY_ENTRY_NAMES for part in entry.parts):
        raise ValueError('LEGACY_LOCAL_LEASE_ENTRY_FORBIDDEN')
    expected_entry = ROOT / ('marker_cli.py' if marker else
                              'http_gate.py' if action['kind'] == 'HTTP' else
                              'worker_entry.py')
    if entry != expected_entry:
        raise ValueError('UNREGISTERED_EXECUTION_ENTRY')
    if marker:
        if action.get('capability') != 'MARKER_ONLY' or action.get('effect') != 'MARKER':
            raise ValueError('MARKER_EFFECT_SCOPE')
    elif action.get('capability') != 'BOUND_QUALIFIED_FAULT':
        raise ValueError('FORMAL_EFFECT_SCOPE')
    return action
