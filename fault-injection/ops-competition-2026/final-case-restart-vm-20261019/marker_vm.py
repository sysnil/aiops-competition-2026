#!/usr/bin/env python3
"""VM-only endpoint wiring; effect remains the unchanged harmless Marker."""

import os
import sys
from pathlib import Path

ROOT = Path('/home/liuzihan.guest/sf-vm')
SCRIPTS = ROOT / 'scripts'
SECRETS = ROOT / 'secrets'
sys.path.insert(0, str(SCRIPTS))

import entry_policy
import marker_cli
import worker_auth

entry_policy.ROOT = SCRIPTS


def credentials():
    return (SECRETS / 'worker-live-auth-ed25519',
            SECRETS / 'master-known-hosts',
            SECRETS / 'master-signing-public.pem')


def ssh_command():
    identity, known_hosts, _ = credentials()
    return ['ssh', '-F', '/dev/null', '-p', '2223', '-i', str(identity),
            '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes',
            '-o', 'PasswordAuthentication=no',
            '-o', 'KbdInteractiveAuthentication=no',
            '-o', 'StrictHostKeyChecking=yes',
            '-o', 'HostKeyAlgorithms=ssh-ed25519',
            '-o', 'UserKnownHostsFile=' + str(known_hosts),
            '-o', 'GlobalKnownHostsFile=/dev/null',
            '-o', 'ConnectTimeout=1', '-o', 'ConnectionAttempts=1',
            '-o', 'ControlMaster=no', '-o', 'ProxyCommand=none',
            '-T', 'liuzihan@127.0.0.1', 'final-case-auth-v1']


worker_auth.credentials = credentials
worker_auth.ssh_command = ssh_command
worker_auth.SECRETS = {'k8s-worker2': (SECRETS, 'worker-live-auth-ed25519')}

if __name__ == '__main__':
    if os.uname().nodename != 'k8s-worker2':
        raise SystemExit('VM_WORKER_IDENTITY_CHANGED')
    marker_cli.main()
