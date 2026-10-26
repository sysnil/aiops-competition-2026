#!/usr/bin/env python3
"""Read-only check of Worker lease behavior without a Master network path."""

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path('/root/ops-competition-2026')
WORKER = ROOT / 'sf013-adapter-v3-20261019/scripts/fault_worker.py'
sys.path.insert(0, str(WORKER.parent))


def utc(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def child():
    from fault_worker import validate_lease

    network_probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    network_probe.settimeout(1)
    try:
        network_probe.connect(('192.168.53.52', 22))
        master_reachable = True
        network_error = None
    except OSError as error:
        master_reachable = False
        network_error = type(error).__name__ + ': ' + str(error)
    finally:
        network_probe.close()
    if master_reachable:
        raise RuntimeError('Master remained reachable inside isolated netns')
    at = time.time()
    manifest_sha = '0' * 64
    manifest = {'run_id': 'ISOLATED-LEASE-PROBE'}
    action = {'action_id': 'ISOLATED-LEASE-PROBE-MARK'}
    lease = {
        'run_id': manifest['run_id'], 'action_id': action['action_id'],
        'manifest_sha256': manifest_sha, 'permission': 'BOUND_QUALIFIED_FAULT',
        'issued_utc': utc(at - .1), 'expires_utc': utc(at + 3.5),
        'authorization_sha256': '1' * 64, 'safety_pass': True,
        'clock': {'measured_at_master_utc': utc(at - .2),
                  'worker_minus_master_seconds': 0,
                  'uncertainty_seconds': .1}}
    validate_lease(manifest, manifest_sha, action, lease, at)
    rejected = {}
    for name, check_at, revoked in (
            ('revoked', at, True), ('expired', at + 4, False)):
        try:
            validate_lease(manifest, manifest_sha, action, lease,
                           check_at, revoked)
            rejected[name] = False
        except RuntimeError:
            rejected[name] = True
    if not all(rejected.values()):
        raise RuntimeError('Expected revoked and expired denials missing')
    print(json.dumps({
        'network_namespace': os.readlink('/proc/self/ns/net'),
        'isolated_network_interfaces': socket.if_nameindex(),
        'master_tcp_ssh_reachable': master_reachable,
        'master_network_error': network_error,
        'valid_local_lease_accepted_without_master_network': True,
        'revoked_and_expired_denied': rejected,
        'scope': 'validate_lease only; no Timer, adapter, or injection'}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--child', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.child:
        child()
        return
    if not args.output or args.output.exists():
        raise RuntimeError('New immutable result path required')
    command = ['unshare', '--net', sys.executable, '-B', __file__, '--child']
    result = subprocess.run(command, capture_output=True, text=True,
                            timeout=15)
    record = {
        'at_utc': utc(time.time()),
        'worker_source_sha256': hashlib.sha256(WORKER.read_bytes()).hexdigest(),
        'isolation_command': 'unshare --net (no host reboot)',
        'exit_code': result.returncode,
        'stderr': result.stderr[-1000:],
        'status': 'ISOLATED_LEASE_PROBE_UNAVAILABLE'}
    if result.returncode == 0:
        record['probe'] = json.loads(result.stdout.strip().splitlines()[-1])
        record['status'] = 'VALID_LOCAL_LEASE_ACCEPTED_WHILE_MASTER_UNREACHABLE'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(record, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': record['status'], 'output': str(args.output)}))


if __name__ == '__main__':
    main()
