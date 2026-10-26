#!/usr/bin/env python3
"""Harmless bound Marker used only for the v5 authorization integration test."""

import argparse
import hashlib
import json
import socket
from datetime import datetime, timezone
from pathlib import Path

from action_decision import create_once, read_bound
from worker_entry import execute


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--registration', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    args = parser.parse_args()
    if args.registration.is_symlink() or not args.registration.is_file():
        raise ValueError('MARKER_REGISTRATION_MISSING')
    if hashlib.sha256(args.registration.read_bytes()).hexdigest() != args.sha256:
        raise ValueError('MARKER_REGISTRATION_HASH')
    action = read_bound(args.registration)
    if (action.get('effect') != 'MARKER' or
            action.get('capability') != 'MARKER_ONLY' or
            action.get('worker') != socket.gethostname() or
            action.get('entrypoint') != str(Path(__file__).resolve())):
        raise ValueError('MARKER_ONLY_SCOPE')
    runtime = args.registration.parent

    def marker(grant):
        create_once(runtime / 'fired.json', {
            'status': 'HARMLESS_MARKER_FIRED',
            'run_id': action['run_id'], 'action_id': action['action_id'],
            'manifest_sha256': action['manifest_sha256'],
            'nonce_sha256': hashlib.sha256(grant['nonce'].encode()).hexdigest(),
            'at_utc': datetime.now(timezone.utc).isoformat()})
        return 'HARMLESS_MARKER_FIRED'

    result = execute(action, runtime, marker, marker=True)
    print(json.dumps({'status': result, 'action_id': action['action_id']}))


if __name__ == '__main__':
    main()
