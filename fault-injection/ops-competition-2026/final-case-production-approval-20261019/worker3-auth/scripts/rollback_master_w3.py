#!/usr/bin/env python3
"""Opt-in exact rollback of Worker3 key, retaining Worker2's restricted key."""

import argparse
import hashlib
import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path('/root/ops-competition-2026/final-case-production-approval-20261019/worker3-auth')
AUTH = Path('/root/.ssh/authorized_keys')
RECEIPT = ROOT / 'results/master-worker3-credential-provision.json'
BACKUP = ROOT / 'private-master/authorized_keys.before-worker3'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--execute-exact-rollback', action='store_true')
    args = parser.parse_args()
    if socket.gethostname() != 'k8s-master' or args.output.exists():
        raise RuntimeError('Master-only immutable Worker3 rollback record required')
    proof = json.loads(RECEIPT.read_text())
    if (sha(AUTH) != proof['authorized_keys_after_sha256'] or
            sha(BACKUP) != proof['authorized_keys_before_sha256']):
        raise RuntimeError('SSH identity changed; manual review required')
    result = {'status': 'WORKER3_SSH_ROLLBACK_DRY_CHECK_PASS',
              'at_utc': datetime.now(timezone.utc).isoformat(),
              'current_sha256': sha(AUTH), 'restore_sha256': sha(BACKUP),
              'worker2_rule_preserved_in_backup':
                  b'sf-final-live-auth-worker2' in BACKUP.read_bytes(),
              'worker3_rule_removed': False}
    if not result['worker2_rule_preserved_in_backup']:
        raise RuntimeError('Worker2 restricted rule absent from rollback image')
    if args.execute_exact_rollback:
        temp = AUTH.with_name('authorized_keys.sf-final-worker3-rollback')
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, BACKUP.read_bytes())
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temp, AUTH)
        if sha(AUTH) != proof['authorized_keys_before_sha256']:
            raise RuntimeError('Worker3 rollback SHA verification failed')
        result['status'] = 'WORKER3_SSH_KEY_EXACTLY_ROLLED_BACK'
        result['worker3_rule_removed'] = True
        result['after_sha256'] = sha(AUTH)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': result['status'],
                      'worker2_rule_preserved_in_backup':
                      result['worker2_rule_preserved_in_backup']}))


if __name__ == '__main__':
    main()
