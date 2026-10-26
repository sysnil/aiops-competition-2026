#!/usr/bin/env python3
"""Harmless systemd marker exercising the production timer lifecycle module."""

import argparse
import json
import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
ROOT = OPS / 'final-case-timer-safety-v1-20261019/markers'
sys.path.insert(0, str(OPS / 'sf013-adapter-v3-20261019/scripts'))
from handshake import atomic
from timer_lifecycle import arm, identity, inspect, revoke


def now():
    return datetime.now(timezone.utc).isoformat()


def context(args):
    if socket.gethostname() != 'k8s-worker2':
        raise RuntimeError('MARKER_WORKER2_ONLY')
    root = (ROOT / args.run_id).resolve()
    if (root.parent != ROOT or not args.run_id.startswith('SF013-TIMER-MARKER-') or
            args.action_id != args.run_id + '-MARK' or
            args.unit != 'ops-sf013-' + args.action_id.lower()):
        raise RuntimeError('MARKER_ATTEMPT_SCOPE')
    expected = identity(args.run_id, args.action_id, args.manifest_sha, args.unit)
    return root, expected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('owner', 'authorize', 'arm', 'fire', 'cancel', 'inspect'))
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--action-id', required=True)
    parser.add_argument('--unit', required=True)
    parser.add_argument('--manifest-sha', required=True)
    parser.add_argument('--planned-utc')
    parser.add_argument('--expires-utc')
    parser.add_argument('--controller-pid', type=int)
    parser.add_argument('--inject-error-after-install', action='store_true')
    args = parser.parse_args()
    root, expected = context(args)
    if args.command == 'owner':
        if not args.planned_utc or not args.controller_pid or args.controller_pid <= 0:
            raise RuntimeError('MARKER_OWNER_PROCESS_OR_TIME')
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        atomic(root / 'arm-owner.json', {**expected,
            'role': 'MASTER_CONTROLLER_ONLY', 'controller_pid': args.controller_pid,
            'planned_utc': args.planned_utc, 'issued_utc': now()}, True)
        result = {'status': 'HARMLESS_MARKER_OWNER_REGISTERED'}
    elif args.command == 'authorize':
        if not args.expires_utc:
            raise RuntimeError('MARKER_AUTHORIZATION_EXPIRY_REQUIRED')
        atomic(root / 'authorization.json', {**expected,
            'expires_utc': args.expires_utc, 'at_utc': now()}, True)
        result = {'status': 'HARMLESS_MARKER_AUTHORIZED'}
    elif args.command == 'arm':
        if not args.planned_utc:
            raise RuntimeError('MARKER_PLANNED_TIME_REQUIRED')
        owner = json.loads((root / 'arm-owner.json').read_text())
        fire = ['/usr/bin/python3', '-B', str(Path(__file__).resolve()), 'fire',
                '--run-id', args.run_id, '--action-id', args.action_id,
                '--unit', args.unit, '--manifest-sha', args.manifest_sha]
        callback = ((lambda: (_ for _ in ()).throw(RuntimeError('INJECTED_POST_INSTALL_FAILURE')))
                    if args.inject_error_after_install else None)
        result = arm(root, expected, args.planned_utc, fire, owner,
                     post_install_check=callback)
    elif args.command == 'fire':
        if (root / 'REVOKED.json').exists():
            result = {'status': 'MARKER_SUPPRESSED_REVOKED'}
        else:
            proof = root / 'authorization.json'
            authorized = json.loads(proof.read_text()) if proof.is_file() else {}
            if (any(authorized.get(key) != value for key, value in expected.items()) or
                    authorized.get('expires_utc', '') < now()):
                result = {'status': 'MARKER_SUPPRESSED_NO_VALID_AUTHORIZATION'}
            else:
                atomic(root / 'fired.json', {**expected, 'at_utc': now(),
                                             'pid': os.getpid()}, True)
                result = {'status': 'HARMLESS_MARKER_FIRED'}
            atomic(root / 'fire-result.json', {**expected, **result,
                                                'at_utc': now()}, True)
    elif args.command == 'cancel':
        result = revoke(root, expected)
    else:
        result = {'status': 'MARKER_TIMER_INSPECTED', 'units': inspect(args.unit),
                  'armed': (root / 'armed.json').exists(),
                  'revoked': (root / 'REVOKED.json').exists(),
                  'fired': (root / 'fired.json').exists()}
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
