#!/usr/bin/env python3
"""Archive one completed SF-005 formal attempt to two independent Workers."""

from __future__ import annotations

import json
import pathlib
import shlex
import subprocess
import tempfile


OPS = pathlib.Path(__file__).resolve().parents[2]
RUN_ID = 'SF-005-ATTEMPT-01'
SRC = pathlib.Path('/data/stage5-v3/ops-competition-2026/runs') / RUN_ID
INDEX = pathlib.Path('/data/stage5-v3/ops-competition-archive-index') / RUN_ID
ARCHIVE = pathlib.Path('/data/rca-archives/ops-competition-2026') / RUN_ID
SCRIPT = OPS / 'scripts/competition_archive.py'
HOSTS = {
    'master': ('192.168.53.52', '/tmp/sfgen-master.sock'),
    'w2': ('192.168.53.54', '/tmp/sfgen-w2.sock'),
    'w3': ('192.168.53.55', '/tmp/sfgen-w3.sock'),
}
FLOOR = 5_000_000_000


def ssh(host: str, args: list[str]) -> list[str]:
    addr, socket = HOSTS[host]
    return ['ssh', '-S', socket, '-o', 'BatchMode=yes', f'root@{addr}',
            shlex.join(list(map(str, args)))]


def remote(host: str, args: list[str], timeout: int = 120) -> str:
    result = subprocess.run(ssh(host, args), capture_output=True, text=True,
                            check=True, timeout=timeout)
    return result.stdout


def scp(source: pathlib.Path, host: str, target: pathlib.Path,
        reverse: bool = False) -> None:
    addr, socket = HOSTS[host]
    left = f'root@{addr}:{source}' if reverse else str(source)
    right = str(target) if reverse else f'root@{addr}:{target}'
    subprocess.run(['scp', '-q', '-o', 'BatchMode=yes', '-o', 'ControlPath=' + socket,
                    left, right], check=True, timeout=60)


def main() -> None:
    terminal = json.loads(remote('master', ['cat', SRC / 'terminal.json']))
    if terminal.get('status') != 'SF005_FORMAL_CAPTURE_PASS_PENDING_EXPORT':
        raise RuntimeError('Only the completed SF-005 formal capture may be archived')
    remote('master', ['install', '-d', '-m', '700', INDEX])
    source_manifest = INDEX / 'source-manifest.json'
    output = remote('master', ['python3', '-B',
        '/root/ops-competition-2026/scripts/competition_archive.py', 'create',
        '--root', SRC, '--manifest', source_manifest], timeout=900)
    source = json.loads(output)
    with tempfile.TemporaryDirectory(prefix='sf30-manifest-') as cache:
        local = pathlib.Path(cache) / 'source-manifest.json'
        scp(source_manifest, 'master', local, reverse=True)
        results = {}
        for host in ('w2', 'w3'):
            dest = ARCHIVE / 'raw'
            check = '''import pathlib,shutil,json
p=pathlib.Path(%r);free=shutil.disk_usage('/data').free;root=shutil.disk_usage('/').free
assert not p.exists()
assert root>=%d and free-%d>=%d
p.mkdir(parents=True,mode=0o700)
print(json.dumps({'root_free_before_bytes':root,'data_free_before_bytes':free,'reserved_archive_bytes':%d}))''' % (
                str(dest), FLOOR, source['bytes'], FLOOR, source['bytes'])
            capacity = json.loads(remote(host, ['python3', '-c', check]))
            reader = subprocess.Popen(ssh('master', ['tar', '-C', SRC, '-czf', '-', '.']),
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            writer = subprocess.Popen(ssh(host, ['tar', '-C', dest, '-xzf', '-']),
                                      stdin=reader.stdout, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE)
            reader.stdout.close()
            _, write_error = writer.communicate(timeout=1800)
            _, read_error = reader.communicate(timeout=30)
            if writer.returncode or reader.returncode:
                raise RuntimeError('Immutable streaming archive failed for ' + host + ': ' +
                                   (read_error + write_error).decode(errors='replace')[-1000:])
            scp(local, host, ARCHIVE / 'source-manifest.json')
            scp(SCRIPT, host, ARCHIVE / 'competition_archive.py')
            receipt = ARCHIVE / 'verification.json'
            verification = json.loads(remote(host, ['python3', '-B',
                ARCHIVE / 'competition_archive.py', 'verify', '--root', dest,
                '--manifest', ARCHIVE / 'source-manifest.json', '--output', receipt], timeout=900))
            if verification['status'] != 'COMPETITION_ARCHIVE_SHA_AND_RESTORED_READ_PASS':
                raise RuntimeError('Archive verification failed: ' + host)
            local_receipt = pathlib.Path(cache) / (host + '-receipt.json')
            scp(receipt, host, local_receipt, reverse=True)
            scp(local_receipt, 'master', INDEX / (host + '-verification.json'))
            results[host] = {'capacity_before': capacity, 'verification': verification}
        result = pathlib.Path(cache) / 'results.json'
        result.write_text(json.dumps({'status': 'SF005_RAW_DUAL_ARCHIVE_PASS',
                                      'source': source, 'workers': results}, indent=2) + '\n')
        scp(result, 'master', INDEX / 'result.json')
    print(json.dumps({'status': 'SF005_RAW_DUAL_ARCHIVE_PASS',
                      'files': source['file_count'], 'bytes': source['bytes'],
                      'worker_receipts': list(results)}))


if __name__ == '__main__':
    main()
