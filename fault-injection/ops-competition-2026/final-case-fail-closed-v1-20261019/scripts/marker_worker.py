#!/usr/bin/env python3
"""Harmless systemd Marker using the exact formal live authorization core."""

import argparse
import hashlib
import json
import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

from handshake import atomic
from timer_lifecycle import arm, identity, inspect, revoke
from worker_auth import execute_once


ROOT = Path('/root/ops-competition-2026/final-case-fail-closed-v1-20261019')
RUNTIME = ROOT / 'marker-runtime'


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def context(args):
    if socket.gethostname() != 'k8s-worker2':
        raise RuntimeError('MARKER_WORKER2_ONLY')
    if (not args.run_id.startswith('SF013-AUTH-MARKER-') or
            args.action_id != args.run_id + '-MARK' or
            args.unit != 'ops-sf013-' + args.action_id.lower()):
        raise RuntimeError('MARKER_ATTEMPT_IDENTITY')
    root = (RUNTIME / args.run_id).resolve()
    if root.parent != RUNTIME:
        raise RuntimeError('MARKER_RUNTIME_PATH')
    registration = root / 'registration.json'
    if sha(registration) != args.manifest_sha:
        raise RuntimeError('MARKER_REGISTRATION_SHA')
    reg = json.loads(registration.read_text())
    if (reg['status'] != 'PREREGISTERED_HARMLESS_LIVE_AUTH_MARKER' or
            reg['run_id'] != args.run_id or
            reg['action_id'] != args.action_id or
            reg['unit'] != args.unit or
            reg['capability'] != 'MARKER_ONLY' or
            reg['worker_host'] != socket.gethostname() or
            reg['real_fault_adapter_allowed'] is not False):
        raise RuntimeError('MARKER_REGISTRATION_SCOPE')
    bound = identity(args.run_id, args.action_id, args.manifest_sha,
                     args.unit)
    return root, reg, bound


def fire(root, reg, bound):
    atomic(root / 'started.json', {**bound, 'at_utc': now()}, True)
    result = {'status': 'MARKER_FAIL_CLOSED', **bound, 'at_utc': now()}

    def marker(grant):
        if (root / 'REVOKED.json').exists():
            raise RuntimeError('MARKER_REVOKED_AT_ACTION')
        if reg['scenario'] == 'callback_error':
            raise RuntimeError('INJECTED_HARMLESS_MARKER_CALLBACK_ERROR')
        atomic(root / 'fired.json', {**bound, 'at_utc': now(),
                                    'pid': os.getpid(),
                                    'grant_nonce_sha256':
                                        hashlib.sha256(grant['nonce'].encode()).hexdigest()},
               True)
        return 'HARMLESS_MARKER_FIRED'

    try:
        binding = {**bound, 'capability': 'MARKER_ONLY',
                   'worker_host': reg['worker_host'],
                   'worker_boot_id': reg['worker_boot_id'],
                   'master_boot_id': reg['master_boot_id']}
        result['status'] = execute_once(binding, root, marker)
    except BaseException as exc:
        result['error'] = type(exc).__name__ + ': ' + str(exc)
    finally:
        result['completed_utc'] = now()
        atomic(root / 'fire-result.json', result, True)
        print(json.dumps(result), flush=True)
    if result['status'] != 'HARMLESS_MARKER_FIRED':
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('owner', 'arm', 'fire',
                                            'cancel', 'inspect'))
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--action-id', required=True)
    parser.add_argument('--unit', required=True)
    parser.add_argument('--manifest-sha', required=True)
    parser.add_argument('--controller-pid', type=int)
    args = parser.parse_args()
    root, reg, bound = context(args)
    if args.command == 'owner':
        if not args.controller_pid or args.controller_pid <= 0:
            raise RuntimeError('MARKER_CONTROLLER_PID_REQUIRED')
        atomic(root / 'arm-owner.json', {**bound,
            'role': 'MASTER_CONTROLLER_ONLY',
            'controller_pid': args.controller_pid,
            'planned_utc': reg['planned_utc'], 'issued_utc': now()}, True)
        result = {'status': 'MARKER_OWNER_REGISTERED'}
    elif args.command == 'arm':
        owner = json.loads((root / 'arm-owner.json').read_text())
        command = ['/usr/bin/python3', '-B', str(Path(__file__).resolve()),
                   'fire', '--run-id', args.run_id,
                   '--action-id', args.action_id, '--unit', args.unit,
                   '--manifest-sha', args.manifest_sha]
        if reg['scenario'] == 'worker_master_network_loss':
            command = ['/usr/bin/unshare', '--net', *command]
        result = arm(root, bound, reg['planned_utc'], command, owner)
    elif args.command == 'fire':
        fire(root, reg, bound)
        return
    elif args.command == 'cancel':
        result = revoke(root, bound)
    else:
        result = {'status': 'MARKER_INSPECTED', 'units': inspect(args.unit),
                  'armed': (root / 'armed.json').exists(),
                  'revoked': (root / 'REVOKED.json').exists(),
                  'started': (root / 'started.json').exists(),
                  'fired': (root / 'fired.json').exists(),
                  'fire_result': (json.loads((root / 'fire-result.json').read_text())
                                  if (root / 'fire-result.json').exists() else None)}
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
