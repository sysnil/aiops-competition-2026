#!/usr/bin/env python3
"""Immutable Master-to-Worker2 no-fault archive with independent SHA readback."""

import argparse
import hashlib
import json
import shlex
import subprocess
import time
from pathlib import Path


ROOT = Path('/data/stage5-v3/ops-competition-2026/rehearsals')
HOST = '192.168.53.54'
SOCKET = '/run/ops-final-ssh/w2.sock'
DEST = Path('/root/ops-competition-2026/rehearsal-archives')
FLOOR = 5_000_000_000


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024**2), b''):
            digest.update(block)
    return digest.hexdigest()


def remote(argv):
    result = subprocess.run(['ssh', '-S', SOCKET, '-o', 'BatchMode=yes',
        'root@' + HOST, shlex.join(map(str, argv))],
        capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError('ARCHIVE_REMOTE_COMMAND_FAILED:' + result.stderr[-300:])
    return result.stdout


def archive(run):
    run = Path(run)
    if (run.is_symlink() or not run.is_dir() or run.parent != ROOT or
            not run.name.startswith('SF-017-ATTEMPT-MARKERTEST-') or
            not run.name.endswith('-full1800')):
        raise ValueError('ARCHIVE_REHEARSAL_SOURCE_SCOPE')
    if json.loads((run / 'quality-audit.json').read_text())['status'] != \
            'FULL_1800_NOFAULT_DATA_QUALITY_PASS':
        raise RuntimeError('ARCHIVE_QUALITY_NOT_PASSED')
    if (run / 'archive.sha256').exists():
        raise ValueError('ARCHIVE_ALREADY_REGISTERED')
    files = sorted(path for path in run.rglob('*') if path.is_file())
    bytes_total = sum(path.stat().st_size for path in files)
    free = int(remote(['df', '-B1', '--output=avail', '/']).splitlines()[1])
    if free - bytes_total < FLOOR:
        raise RuntimeError('ARCHIVE_WORKER2_ROOT_FLOOR')
    with (run / 'archive.sha256').open('x') as stream:
        for path in files:
            relative = path.relative_to(run)
            if '\n' in str(relative) or '\\' in str(relative):
                raise ValueError('ARCHIVE_FILENAME_UNSAFE')
            stream.write(sha(path) + '  ' + str(relative) + '\n')
    remote(['mkdir', '-p', '-m', '700', DEST])
    staging = DEST / ('.' + run.name + '.incoming')
    final = DEST / run.name
    remote(['test', '!', '-e', final])
    remote(['test', '!', '-e', staging])
    transfer = subprocess.Popen(['rsync', '-a', '--numeric-ids', '-e',
        'ssh -S ' + SOCKET + ' -o BatchMode=yes', str(run) + '/',
        'root@' + HOST + ':' + str(staging) + '/'],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        while transfer.poll() is None:
            remaining = int(remote(['df', '-B1', '--output=avail', '/']).splitlines()[1])
            if remaining < FLOOR:
                transfer.terminate()
                transfer.communicate(timeout=15)
                raise RuntimeError('ARCHIVE_DYNAMIC_FIVE_GB_FLOOR')
            time.sleep(5)
        _, stderr = transfer.communicate(timeout=15)
        if transfer.returncode:
            raise RuntimeError('ARCHIVE_RSYNC_FAILED:' + stderr[-400:])
    finally:
        if transfer.poll() is None:
            transfer.terminate()
            transfer.communicate(timeout=15)
    remote(['sh', '-c', 'cd ' + shlex.quote(str(staging)) +
            ' && sha256sum -c --status archive.sha256'])
    remote(['mv', '-T', staging, final])
    readback = {name: remote(['sha256sum', final / name]).split()[0]
                for name in ('schedule.json', 'quality-audit.json',
                             'capture/chunk-00/captures/during/capture-manifest.json')}
    if any(readback[name] != sha(run / name) for name in readback):
        raise RuntimeError('ARCHIVE_RESTORED_READBACK_MISMATCH')
    result = {'status': 'NOFAULT_ARCHIVE_SHA_AND_READBACK_PASS',
              'source': str(run), 'target_host': HOST, 'target': str(final),
              'source_files': len(files), 'source_bytes': bytes_total,
              'worker_free_before_bytes': free,
              'sha_manifest_sha256': sha(run / 'archive.sha256'),
              'readback': readback}
    (run / 'archive-result.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(archive(args.run)))


if __name__ == '__main__':
    main()
