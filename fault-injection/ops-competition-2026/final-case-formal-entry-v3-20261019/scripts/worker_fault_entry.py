#!/usr/bin/env python3
"""Sole new-case Worker Timer CLI for bound arm/fire/cancel operations."""

import argparse
import json
import re
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SHARED = Path('/root/ops-competition-2026/final-case-unified-execution-v1-20261019/scripts')
sys.path.insert(0, str(SHARED))

from action_decision import create_once, read_bound
from worker_entry import execute
from control_contract import ROOT, command_for_worker, load_manifest
from timer_lifecycle import arm as arm_timer, cancel as cancel_timer, inspect
import qualified_effect
from entry_policy import CASE_ACTIONS


def now():
    return datetime.now(timezone.utc).isoformat()


def bound(manifest_path, manifest_sha, action_id):
    manifest = load_manifest(manifest_path, manifest_sha)
    matches = [item for item in manifest['actions'] if item['action_id'] == action_id]
    if len(matches) != 1:
        raise ValueError('WORKER_ACTION_NOT_REGISTERED')
    action = matches[0]
    if action['worker'] != socket.gethostname() or action['worker'] == 'k8s-master':
        raise ValueError('WORKER_ACTION_WRONG_HOST')
    runtime = ROOT / 'runtime' / action['run_id'] / action['action_id']
    if action.get('runtime') != str(runtime):
        raise ValueError('WORKER_RUNTIME_SCOPE')
    return manifest, action, runtime


def emergency_cancel(action_id, manifest_sha):
    match = re.fullmatch(r'(SF-01[3-7]-ATTEMPT-(?:[0-9]{2}|MARKERTEST-[A-Z0-9]{8}))-(POD|SERVICE)',
                         action_id)
    if match is None or not re.fullmatch(r'[0-9a-f]{64}', manifest_sha):
        raise ValueError('EMERGENCY_CANCEL_IDENTITY')
    run_id, kind = match.groups()
    case = run_id.split('-ATTEMPT-', 1)[0]
    expected = CASE_ACTIONS.get(case, {}).get(kind)
    if expected is None or expected[0] != socket.gethostname():
        raise ValueError('EMERGENCY_CANCEL_WRONG_WORKER')
    runtime = ROOT / 'runtime' / run_id / action_id
    auth = read_bound(runtime / 'auth-binding.json')
    bound_cancel = read_bound(runtime / 'cancel-binding.json')
    identity = {'run_id': run_id, 'action_id': action_id,
                'manifest_sha256': manifest_sha}
    unit = 'ops-' + case.lower().replace('-', '') + '-' + action_id.lower()
    if (any(auth.get(key) != value or bound_cancel.get(key) != value
            for key, value in identity.items()) or
            auth.get('worker_host') != socket.gethostname() or
            bound_cancel.get('worker_host') != socket.gethostname() or
            bound_cancel.get('unit') != unit):
        raise ValueError('EMERGENCY_CANCEL_BINDING_MISMATCH')
    result = cancel_timer(runtime, {**identity, 'unit': unit}, manifest_sha)
    adapter_state = (Path('/root/ops-competition-2026/qualification/private-runtime') /
                     (action_id + '.json'))
    if adapter_state.exists():
        from control_contract import sha
        if bound_cancel.get('adapter_sha256') != sha(qualified_effect.ADAPTER):
            raise RuntimeError('EMERGENCY_ADAPTER_SOURCE_CHANGED')
        subprocess.run(['/usr/bin/python3', '-B', str(qualified_effect.ADAPTER),
            'cleanup', '--state', str(adapter_state)], check=True,
            capture_output=True, timeout=25)
        result['qualified_effect_cleanup'] = 'VERIFIED_BY_PINNED_ADAPTER'
    result['entry'] = 'EMERGENCY_CANCEL_NO_MANIFEST_PARSE'
    return result


def run_command(verb, manifest_path, manifest_sha, action_id):
    manifest, action, runtime = bound(manifest_path, manifest_sha, action_id)
    if verb == 'inspect':
        return {'status': 'BOUND_WORKER_INSPECT', 'units': inspect(action['unit'])}
    if verb == 'cancel':
        timer = cancel_timer(runtime, action, manifest_sha)
        if manifest['mode'] == 'FORMAL_CANDIDATE':
            timer['effect_cleanup'] = qualified_effect.cleanup(action)
        return timer
    if verb == 'arm':
        owner = read_bound(runtime / 'arm-owner.json')
        return arm_timer(runtime, action, manifest_sha,
                         command_for_worker(manifest_path, manifest_sha, action),
                         owner)
    if verb != 'fire':
        raise ValueError('WORKER_COMMAND_SCOPE')
    create_once(runtime / 'started.json', {'run_id': action['run_id'],
        'action_id': action['action_id'], 'manifest_sha256': manifest_sha,
        'at_utc': now()})
    result = {'status': 'FAILED_CLOSED', 'run_id': action['run_id'],
              'action_id': action['action_id']}
    try:
        if manifest['mode'] == 'HARMLESS_REHEARSAL':
            def marker(grant):
                create_once(runtime / 'fired.json', {'status': 'HARMLESS_MARKER',
                    'run_id': action['run_id'], 'action_id': action['action_id'],
                    'manifest_sha256': manifest_sha, 'at_utc': now()})
                return {'status': 'HARMLESS_MARKER'}
            result['effect'] = execute(action, runtime, marker, marker=True)
        else:
            result['effect'] = execute(action, runtime,
                lambda grant: qualified_effect.run(action, runtime, grant))
        result['status'] = 'ACTION_TERMINAL'
    except BaseException as error:
        result['error'] = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        result['completed_utc'] = now()
        create_once(runtime / 'terminal.json', result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('verb', choices=('arm', 'fire', 'cancel', 'inspect',
                                         'emergency-cancel'))
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--manifest-sha', required=True)
    parser.add_argument('--action-id', required=True)
    args = parser.parse_args()
    result = (emergency_cancel(args.action_id, args.manifest_sha)
              if args.verb == 'emergency-cancel' else
              run_command(args.verb, args.manifest, args.manifest_sha,
                          args.action_id))
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
