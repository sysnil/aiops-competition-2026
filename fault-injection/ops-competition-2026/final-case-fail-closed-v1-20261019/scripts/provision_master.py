#!/usr/bin/env python3
"""Install one forced-command Worker key without changing sshd settings."""

import argparse
import hashlib
import json
import os
import shutil
import socket
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization


ROOT = Path('/root/ops-competition-2026/final-case-auth-secrets-20261019')
AUTH_KEYS = Path('/root/.ssh/authorized_keys')
FORCED = ('/usr/bin/python3 -B /root/ops-competition-2026/'
          'final-case-fail-closed-v1-20261019/scripts/master_authorizer.py')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save_new(path, value, mode):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        os.write(fd, value)
        os.fsync(fd)
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker-public-key', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if socket.gethostname() != 'k8s-master' or args.output.exists():
        raise RuntimeError('Master-only new provisioning record required')
    if not AUTH_KEYS.is_file() or AUTH_KEYS.is_symlink():
        raise RuntimeError('Existing authorized_keys identity changed')
    worker_line = args.worker_public_key.read_text().strip()
    parts = worker_line.split()
    if len(parts) < 2 or parts[0] != 'ssh-ed25519':
        raise RuntimeError('Worker key is not ed25519')
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=False)
    backup = ROOT / 'authorized_keys.before'
    shutil.copyfile(AUTH_KEYS, backup)
    backup.chmod(0o600)
    signing = Ed25519PrivateKey.generate()
    private = ROOT / 'master-signing.pem'
    public = ROOT / 'master-signing-public.pem'
    save_new(private, signing.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()), 0o600)
    save_new(public, signing.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo), 0o644)
    line = ('from="192.168.53.54",restrict,command="' + FORCED +
            '" ' + parts[0] + ' ' + parts[1] +
            ' sf-final-live-auth-worker2\n')
    old = backup.read_bytes()
    if b'sf-final-live-auth-worker2' in old:
        raise RuntimeError('Dedicated key already installed')
    temp = AUTH_KEYS.with_name('authorized_keys.sf-final-new')
    save_new(temp, old + (b'\n' if old and not old.endswith(b'\n') else b'') +
             line.encode(), 0o600)
    os.replace(temp, AUTH_KEYS)
    result = {
        'status': 'RESTRICTED_MASTER_SSH_AUTH_INSTALLED',
        'at_utc': datetime.now(timezone.utc).isoformat(),
        'authorized_keys_before_sha256': sha(backup),
        'authorized_keys_after_sha256': sha(AUTH_KEYS),
        'worker_public_key_sha256': sha(args.worker_public_key),
        'master_signing_public_sha256': sha(public),
        'forced_command': FORCED,
        'source_ip_restriction': '192.168.53.54',
        'private_key_exported': False,
        'sshd_config_changed': False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    args.output.chmod(0o600)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
