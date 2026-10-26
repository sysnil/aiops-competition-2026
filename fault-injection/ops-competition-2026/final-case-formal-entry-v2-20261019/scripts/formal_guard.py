#!/usr/bin/env python3
"""Independent Master Guard revoking every exact registered Action on exit."""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

SHARED = Path('/root/ops-competition-2026/final-case-unified-execution-v1-20261019/scripts')
sys.path.insert(0, str(SHARED))
from action_decision import create_once
from control_contract import ROOT, load_manifest
from formal_controller import SystemIO, stamp


def controller_active(unit):
    result = subprocess.run(['systemctl', 'is-active', unit + '.service'],
                            text=True, capture_output=True, timeout=3)
    return result.returncode == 0 and result.stdout.strip() == 'active'


def guard(manifest, io, *, active=controller_active, clock=time.time,
          sleep=time.sleep, startup_grace=15):
    run = manifest['run_id']
    root = ROOT / 'runtime' / run / 'master'
    terminal = root / 'controller-terminal.json'
    started = clock()
    seen = False
    deadline = (stamp(manifest['window_end_utc']) if 'window_end_utc' in manifest
                else max(stamp(a['planned_start_utc']) + a['duration_seconds']
                         for a in manifest['actions'])) + 60
    while clock() < deadline:
        live = active(manifest['controller_unit'])
        seen |= live
        if terminal.is_file():
            break
        if not live and (seen or clock() - started > startup_grace):
            break
        sleep(.25)
    results = {}
    for action in reversed(manifest['actions']):
        auth_state = io_state(action)
        if auth_state.exists():
            try:
                results[action['action_id']] = io.revoke(action, 'INDEPENDENT_GUARD_TERMINAL')
            except BaseException as error:
                results[action['action_id']] = {
                    'status': 'CLEANUP_UNVERIFIED',
                    'error': type(error).__name__ + ':' + str(error)}
    status = ('GUARD_CLEANUP_VERIFIED' if all(
        row.get('status') != 'CLEANUP_UNVERIFIED' for row in results.values())
        else 'GUARD_CLEANUP_UNVERIFIED')
    if manifest['mode'] == 'FORMAL_CANDIDATE':
        subprocess.run(['systemctl', 'stop', manifest['monitor_unit'] + '.service'],
                       capture_output=True, timeout=20)
    return {'status': status, 'run_id': run, 'controller_terminal': terminal.is_file(),
            'actions': results}


def io_state(action):
    return (Path('/root/ops-competition-2026/final-case-unified-execution-v1-20261019/runtime') /
            action['run_id'] / action['action_id'] / 'state.json')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--manifest-sha', required=True)
    args = parser.parse_args()
    manifest = load_manifest(args.manifest, args.manifest_sha)
    io = SystemIO(manifest, args.manifest, args.manifest_sha)
    result = guard(manifest, io)
    output = ROOT / 'runtime' / manifest['run_id'] / 'master' / 'guard-terminal.json'
    create_once(output, result)
    print(json.dumps(result, sort_keys=True))
    if result['status'] != 'GUARD_CLEANUP_VERIFIED':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
