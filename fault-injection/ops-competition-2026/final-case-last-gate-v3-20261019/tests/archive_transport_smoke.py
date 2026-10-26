#!/usr/bin/env python3
"""Small, labelled transport/readback test on Worker2; no case data."""

import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from archive_rehearsal import remote, SOCKET, HOST


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


with tempfile.TemporaryDirectory() as temporary:
    source = Path(temporary)
    (source / 'SYNTHETIC-TRANSPORT-ONLY.json').write_text(json.dumps({
        'status': 'SYNTHETIC_ARCHIVE_TRANSPORT_TEST',
        'real_case': False, 'faults_injected': 0}) + '\n')
    token = str(int(time.time()))
    target = '/root/ops-competition-2026/rehearsal-archives/.transport-smoke-' + token
    remote(['mkdir', '-p', '-m', '700', '/root/ops-competition-2026/rehearsal-archives'])
    remote(['test', '!', '-e', target])
    subprocess.run(['rsync', '-a', '-e',
        'ssh -S ' + SOCKET + ' -o BatchMode=yes', str(source) + '/',
        'root@' + HOST + ':' + target + '/'], check=True,
        capture_output=True, timeout=20)
    remote_sha = remote(['sha256sum', target + '/SYNTHETIC-TRANSPORT-ONLY.json']).split()[0]
    result = {'status': 'SYNTHETIC_ARCHIVE_TRANSPORT_PASS'
              if remote_sha == sha(source / 'SYNTHETIC-TRANSPORT-ONLY.json') else
              'SYNTHETIC_ARCHIVE_TRANSPORT_FAIL',
              'target': target, 'source_sha256': sha(source / 'SYNTHETIC-TRANSPORT-ONLY.json'),
              'readback_sha256': remote_sha, 'real_case': False}
    print(json.dumps(result))
    if result['status'] != 'SYNTHETIC_ARCHIVE_TRANSPORT_PASS':
        raise SystemExit(1)
