"""Worker-only live authorization: fresh SSH request immediately before action."""

import json
import os
import socket
import stat
import subprocess
import time
from pathlib import Path

from auth_protocol import (boot_id, canonical, load_public, request,
                           stamp, verify_grant)


SECRET = Path('/root/ops-competition-2026/final-case-auth-secrets-w3-20261019')
IDENTITY = SECRET / 'worker3-live-auth-ed25519'
KNOWN_HOSTS = SECRET / 'master-known-hosts'
MASTER_PUBLIC = SECRET / 'master-signing-public.pem'
MASTER = '192.168.53.52'


def ssh_command():
    for path in (IDENTITY, KNOWN_HOSTS, MASTER_PUBLIC):
        if path.is_symlink() or not path.is_file():
            raise RuntimeError('LIVE_AUTH_KEY_OR_HOST_IDENTITY_MISSING')
    if stat.S_IMODE(IDENTITY.stat().st_mode) != 0o600:
        raise RuntimeError('LIVE_AUTH_PRIVATE_KEY_PERMISSIONS')
    return ['ssh', '-F', '/dev/null', '-i', str(IDENTITY),
            '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes',
            '-o', 'PasswordAuthentication=no', '-o', 'KbdInteractiveAuthentication=no',
            '-o', 'StrictHostKeyChecking=yes',
            '-o', 'HostKeyAlgorithms=ssh-ed25519',
            '-o', 'UserKnownHostsFile=' + str(KNOWN_HOSTS),
            '-o', 'GlobalKnownHostsFile=/dev/null',
            '-o', 'ConnectTimeout=1', '-o', 'ConnectionAttempts=1',
            '-o', 'ControlMaster=no', '-o', 'ProxyCommand=none',
            '-T', 'root@' + MASTER, 'final-case-auth-v1']


def live_grant(binding, runtime):
    if socket.gethostname() != 'k8s-worker3':
        raise RuntimeError('LIVE_AUTH_WRONG_WORKER')
    runtime = Path(runtime)
    if (runtime / 'REVOKED.json').exists():
        raise RuntimeError('LIVE_AUTH_LOCAL_REVOKED')
    if (runtime / 'grant-consumed.json').exists():
        raise RuntimeError('LIVE_AUTH_ALREADY_USED')
    if (binding.get('worker_boot_id') != boot_id() or
            binding.get('worker_host') != socket.gethostname()):
        raise RuntimeError('LIVE_AUTH_WORKER_BOOT_OR_HOST_CHANGED')
    req = request(binding['run_id'], binding['action_id'],
                  binding['manifest_sha256'], binding['capability'],
                  binding['worker_host'], binding['worker_boot_id'])
    started = time.monotonic()
    result = subprocess.run(ssh_command(), input=canonical(req) + b'\n',
                            capture_output=True, timeout=1.8)
    if result.returncode or time.monotonic() - started > 1.8:
        raise RuntimeError('LIVE_AUTH_MASTER_UNREACHABLE_OR_DENIED')
    if len(result.stdout) > 8192 or not result.stdout.strip():
        raise RuntimeError('LIVE_AUTH_RESPONSE_SIZE_OR_EMPTY')
    response = json.loads(result.stdout)
    payload = verify_grant(load_public(MASTER_PUBLIC), response, req,
                           binding['master_boot_id'],
                           consumed=runtime / 'grant-consumed.json')
    return payload


def execute_once(binding, runtime, action):
    runtime = Path(runtime)
    grant = live_grant(binding, runtime)
    if ((runtime / 'REVOKED.json').exists() or
            time.time() >= stamp(grant['expires_utc'])):
        raise RuntimeError('LIVE_AUTH_REVOKED_OR_EXPIRED_AT_EXECUTION')
    return action(grant)
