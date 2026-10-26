#!/usr/bin/env python3
"""Bounded, synthetic 7.35-GB Master-to-Worker archive qualification."""

import argparse
import hashlib
import json
import os
import shlex
import shutil
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
CODE = OPS / 'final-case-readiness-audit-20261019/scripts'
SOURCE = Path('/data/stage5-v3/ops-competition-2026/final-case-archive-fixture-20261019')
DEST = OPS / 'final-case-archive-qualification-full-20261019'
HOSTS = {
    'worker1': ('192.168.53.53', '/run/ops-sf013-archive/w1.sock'),
    'worker2': ('192.168.53.54', '/run/ops-sf013-archive/w2.sock'),
    'worker3': ('192.168.53.55', '/run/ops-sf013-archive/w3.sock'),
}
FILES = {'raw/telemetry.bin': 6_900_000_000,
         'public/export.bin': 450_324_113}
PAYLOAD = sum(FILES.values())
FLOOR = 5_000_000_000
BACKGROUND = 2_000_000_000


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def remote(host, args, timeout=25):
    address, control = HOSTS[host]
    result = subprocess.run(['ssh', '-S', control, '-o', 'BatchMode=yes',
        'root@' + address, shlex.join(list(map(str, args)))],
        capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(host + ': ' + result.stderr[-800:] + result.stdout[-400:])
    return result.stdout


def disk(host):
    code = '''import json,os,shutil
assert os.stat('/').st_dev != os.stat('/data').st_dev
print(json.dumps({'root':shutil.disk_usage('/').free,
                  'data':shutil.disk_usage('/data').free}))'''
    return json.loads(remote(host, ['python3', '-c', code]))


def check_allocation():
    if socket.gethostname() != 'k8s-master':
        raise RuntimeError('Master-only full-size source and transfer')
    if SOURCE.exists():
        raise RuntimeError('Synthetic source already exists; no overwrite')
    master = {'root': shutil.disk_usage('/').free,
              'data': shutil.disk_usage('/data/stage5-v3').free}
    if (master['root'] - BACKGROUND < FLOOR or
            master['data'] - PAYLOAD - BACKGROUND < FLOOR):
        raise RuntimeError('Master peak fixture allocation would cross five GB')
    workers = {host: disk(host) for host in HOSTS}
    for host, space in workers.items():
        if remote(host, ['test', '!', '-e', DEST]).strip():
            raise RuntimeError(host + ' unexpected destination test output')
        if (space['root'] - 2 * PAYLOAD - BACKGROUND < FLOOR or
                space['data'] - BACKGROUND < FLOOR):
            raise RuntimeError(host + ' archive+restore peak lacks safety margin')
    return {'master': master, 'workers': workers}


def generate_fixture():
    SOURCE.mkdir(parents=True, mode=0o700, exist_ok=False)
    metadata = {}
    for rel, size in FILES.items():
        path = SOURCE / rel
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        try:
            os.posix_fallocate(fd, 0, size)
            os.fsync(fd)
        finally:
            os.close(fd)
        if path.stat().st_blocks * 512 < size * .95:
            raise RuntimeError('Master fixture allocation is sparse: ' + rel)
        if shutil.disk_usage('/data/stage5-v3').free < FLOOR:
            raise RuntimeError('Master data disk crossed absolute five-GB floor')
        metadata[rel] = {'bytes': size, 'sha256': sha(path)}
    manifest = SOURCE / 'source-manifest.json'
    with manifest.open('x') as stream:
        json.dump({'status': 'SYNTHETIC_NO_SENSITIVE_FULLSIZE_ARCHIVE',
                   'qualification_id': 'FULLSIZE-READINESS-20261019',
                   'created_utc': now(), 'payload_bytes': PAYLOAD,
                   'files': metadata,
                   'source_provenance': 'allocated-zero synthetic bytes; no capture or GT'},
                  stream, indent=2)
        stream.write('\n')
    manifest.chmod(0o600)
    return {'source_manifest_sha256': sha(manifest),
            'source_files': metadata,
            'master_data_free_after_bytes': shutil.disk_usage('/data/stage5-v3').free}


def copy_and_verify(host):
    address, control = HOSTS[host]
    before = disk(host)
    remote(host, ['install', '-d', '-m', '700', DEST / 'archive'])
    command = ['rsync', '-a', '-e', 'ssh -S ' + control +
               ' -o BatchMode=yes', str(SOURCE) + '/',
               'root@' + address + ':' + str(DEST / 'archive') + '/']
    started = time.monotonic()
    process = subprocess.Popen(command, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    while process.poll() is None:
        time.sleep(5)
        space = disk(host)
        used = int(remote(host, ['du', '-sb', DEST / 'archive']).split()[0])
        remaining = max(0, PAYLOAD - min(PAYLOAD, used))
        if (space['root'] < FLOOR or space['data'] - BACKGROUND < FLOOR or
                space['root'] - remaining - PAYLOAD - BACKGROUND < FLOOR):
            process.terminate()
            process.communicate(timeout=30)
            raise RuntimeError(host + ' dynamic archive forecast crossed five-GB floor')
    out, err = process.communicate(timeout=30)
    if process.returncode:
        raise RuntimeError(host + ' rsync failed: ' + err[-1000:] + out[-400:])
    remote(host, ['install', '-d', '-m', '700', DEST / 'scripts'])
    subprocess.run(['scp', '-q', '-o', 'ControlPath=' + control,
                    str(CODE / 'verify_fullsize_worker.py'),
                    'root@' + address + ':' + str(DEST / 'scripts/verify_fullsize_worker.py')],
                   check=True, timeout=30)
    receipt_path = DEST / 'verification.json'
    response = remote(host, ['python3', '-B',
        DEST / 'scripts/verify_fullsize_worker.py', '--output', receipt_path], timeout=1200)
    receipt = json.loads(response.splitlines()[-1])
    if receipt['status'] != 'FULLSIZE_ARCHIVE_SHA_AND_RESTORED_READ_PASS':
        raise RuntimeError(host + ' restored-read verification did not pass')
    result = {'host': host, 'status': receipt['status'], 'before': before,
              'after': disk(host), 'elapsed_seconds': time.monotonic() - started,
              'source_manifest_sha256': sha(SOURCE / 'source-manifest.json'),
              'worker_receipt_path': str(receipt_path),
              'worker_receipt_sha256': remote(host, ['sha256sum', receipt_path]).split()[0]}
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError('Full-size qualification result already exists')
    result = {'status': 'FULLSIZE_ARCHIVE_QUALIFICATION_FAILED',
              'at_utc': now(), 'payload_bytes': PAYLOAD,
              'real_faults_injected': 0, 'historical_archives_deleted': False,
              'worker_results': []}
    try:
        result['preflight'] = check_allocation()
        result['fixture'] = generate_fixture()
        for host in HOSTS:
            result['worker_results'].append(copy_and_verify(host))
        if sha(SOURCE / 'source-manifest.json') != result['fixture']['source_manifest_sha256']:
            raise RuntimeError('Master fixture manifest changed during transfer')
        result['status'] = 'THREE_WORKER_FULLSIZE_ARCHIVE_AND_READBACK_PASS'
    except BaseException as exc:
        result['error'] = type(exc).__name__ + ': ' + str(exc)
    finally:
        result['completed_utc'] = now()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x') as stream:
            json.dump(result, stream, indent=2)
            stream.write('\n')
        args.output.chmod(0o600)
        print(json.dumps({'status': result['status'],
                          'workers_verified': len(result['worker_results']),
                          'error': result.get('error')}), flush=True)
    if result['status'] != 'THREE_WORKER_FULLSIZE_ARCHIVE_AND_READBACK_PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
