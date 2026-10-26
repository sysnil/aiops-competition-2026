#!/usr/bin/env python3
"""Create Worker2 SSH identity and pin authenticated Master host/signing keys."""

import argparse
import hashlib
import json
import os
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path('/root/ops-competition-2026/final-case-auth-secrets-20261019')


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
    parser.add_argument('--trusted-master-host-key', type=Path, required=True)
    parser.add_argument('--trusted-master-signing-key', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if socket.gethostname() != 'k8s-worker2' or args.output.exists():
        raise RuntimeError('Worker2-only new provisioning record required')
    parts = args.trusted_master_host_key.read_text().strip().split()
    if len(parts) < 2 or parts[0] != 'ssh-ed25519':
        raise RuntimeError('Authenticated Master host key is not ed25519')
    ROOT.mkdir(parents=True, mode=0o700, exist_ok=False)
    identity = ROOT / 'worker2-live-auth-ed25519'
    subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '',
                    '-C', 'sf-final-live-auth-worker2', '-f', str(identity)],
                   check=True, timeout=15)
    identity.chmod(0o600)
    (ROOT / 'worker2-live-auth-ed25519.pub').chmod(0o644)
    known = ROOT / 'master-known-hosts'
    save_new(known, ('192.168.53.52 ' + parts[0] + ' ' + parts[1] + '\n').encode(),
             0o600)
    if args.trusted_master_signing_key:
        signing = ROOT / 'master-signing-public.pem'
        save_new(signing, args.trusted_master_signing_key.read_bytes(), 0o644)
    result = {'status': 'WORKER2_AUTH_IDENTITY_AND_MASTER_HOST_PINNED',
              'at_utc': datetime.now(timezone.utc).isoformat(),
              'worker_public_key_sha256': sha(identity.with_suffix('.pub')),
              'master_known_hosts_sha256': sha(known),
              'trusted_master_host_key_sha256': sha(args.trusted_master_host_key),
              'master_signing_public_sha256':
                  sha(ROOT / 'master-signing-public.pem')
                  if args.trusted_master_signing_key else None,
              'private_key_exported': False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    args.output.chmod(0o600)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
