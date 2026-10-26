#!/usr/bin/env python3
"""Append one Worker3 forced-command key while preserving Worker2 access."""

import argparse
import hashlib
import json
import os
import shutil
import socket
from datetime import datetime, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
ROOT = OPS / 'final-case-production-approval-20261019/worker3-auth'
BACKUP_ROOT = ROOT / 'private-master'
AUTH_KEYS = Path('/root/.ssh/authorized_keys')
W2_RECORD = OPS / 'final-case-fail-closed-v1-20261019/results/master-credential-provision.json'
FORCED = ('/usr/bin/python3 -B ' + str(ROOT / 'scripts/master_authorizer.py'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save_new(path, body, mode):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
    try:
        os.write(fd, body)
        os.fsync(fd)
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker-public-key', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if socket.gethostname() != 'k8s-master' or args.output.exists():
        raise RuntimeError('Master-only new Worker3 SSH record required')
    previous = json.loads(W2_RECORD.read_text())
    if (not AUTH_KEYS.is_file() or AUTH_KEYS.is_symlink() or
            sha(AUTH_KEYS) != previous['authorized_keys_after_sha256']):
        raise RuntimeError('Master Worker2 restricted-key baseline changed')
    key = args.worker_public_key.read_text().strip().split()
    if len(key) < 2 or key[0] != 'ssh-ed25519':
        raise RuntimeError('Worker3 key must be ED25519')
    BACKUP_ROOT.mkdir(parents=True, mode=0o700, exist_ok=False)
    backup = BACKUP_ROOT / 'authorized_keys.before-worker3'
    shutil.copyfile(AUTH_KEYS, backup)
    backup.chmod(0o600)
    if b'sf-final-live-auth-worker3' in backup.read_bytes():
        raise RuntimeError('Worker3 restricted key already present')
    line = ('from="192.168.53.55",restrict,command="' + FORCED +
            '" ' + key[0] + ' ' + key[1] +
            ' sf-final-live-auth-worker3\n')
    temp = AUTH_KEYS.with_name('authorized_keys.sf-final-worker3-new')
    old = backup.read_bytes()
    save_new(temp, old + (b'\n' if old and not old.endswith(b'\n') else b'') +
             line.encode(), 0o600)
    os.replace(temp, AUTH_KEYS)
    result = {
        'status': 'WORKER3_RESTRICTED_MASTER_SSH_AUTH_INSTALLED',
        'at_utc': datetime.now(timezone.utc).isoformat(),
        'authorized_keys_before_sha256': sha(backup),
        'authorized_keys_after_sha256': sha(AUTH_KEYS),
        'worker3_public_key_sha256': sha(args.worker_public_key),
        'worker2_record_sha256': sha(W2_RECORD),
        'forced_command': FORCED,
        'source_ip_restriction': '192.168.53.55',
        'sshd_config_changed': False,
        'private_key_exported': False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    args.output.chmod(0o600)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
