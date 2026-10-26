#!/usr/bin/env python3
"""Opt-in exact rollback for the dedicated Worker2 forced-command SSH key."""

import argparse
import hashlib
import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path


SECRET = Path('/root/ops-competition-2026/final-case-auth-secrets-20261019')
AUTH_KEYS = Path('/root/.ssh/authorized_keys')
PROVISION = Path('/root/ops-competition-2026/final-case-fail-closed-v1-20261019/results/master-credential-provision.json')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--execute-exact-rollback', action='store_true')
    args = parser.parse_args()
    if socket.gethostname() != 'k8s-master' or args.output.exists():
        raise RuntimeError('Master-only immutable rollback record required')
    receipt = json.loads(PROVISION.read_text())
    backup = SECRET / 'authorized_keys.before'
    if (sha(AUTH_KEYS) != receipt['authorized_keys_after_sha256'] or
            sha(backup) != receipt['authorized_keys_before_sha256']):
        raise RuntimeError('SSH key inventory changed; manual review required')
    result = {'status': 'ROLLBACK_DRY_CHECK_PASS',
              'at_utc': datetime.now(timezone.utc).isoformat(),
              'before_sha256': sha(AUTH_KEYS),
              'restore_sha256': sha(backup)}
    if args.execute_exact_rollback:
        temp = AUTH_KEYS.with_name('authorized_keys.sf-final-rollback')
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, backup.read_bytes())
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temp, AUTH_KEYS)
        if sha(AUTH_KEYS) != receipt['authorized_keys_before_sha256']:
            raise RuntimeError('SSH authorized_keys rollback SHA mismatch')
        result['status'] = 'EXACT_FORCED_COMMAND_KEY_ROLLED_BACK'
        result['after_sha256'] = sha(AUTH_KEYS)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
