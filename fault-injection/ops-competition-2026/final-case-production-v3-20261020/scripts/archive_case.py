#!/usr/bin/env python3
"""Archive one admitted final Case to its registered Worker with SHA readback."""

import argparse
import hashlib
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
FORMAL = OPS / 'final-case-formal-entry-v7-20261020/scripts'
sys.path.insert(0, str(FORMAL))
from storage_policy import ALLOCATION, FLOOR, ROOTS
from safety_monitor import read_space


DATA = Path('/data/stage5-v3/ops-competition-2026')
DEST = Path('/root/ops-competition-2026/final-case-archives')
CHANNELS = {
    'k8s-worker1': ('/run/ops-final-ssh/w1.sock', '192.168.53.53'),
    'k8s-worker2': ('/run/ops-final-ssh/w2.sock', '192.168.53.54'),
    'k8s-worker3': ('/run/ops-final-ssh/w3.sock', '192.168.53.55'),
}


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024**2), b''):
            digest.update(block)
    return digest.hexdigest()


def remote(worker, argv):
    socket, host = CHANNELS[worker]
    result = subprocess.run(['ssh', '-S', socket, '-o', 'BatchMode=yes',
        '-o', 'ConnectTimeout=3', 'root@' + host, shlex.join(map(str, argv))],
        capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError('ARCHIVE_REMOTE_FAILED:' + result.stderr[-350:])
    return result.stdout


def free(worker, mount):
    output = remote(worker, ['df', '-B1', '--output=avail', mount]).splitlines()
    if len(output) != 2:
        raise RuntimeError('ARCHIVE_FREE_SPACE_PARSE')
    return int(output[1])


def all_free_above_floor():
    return all(read_space(host, mount) >= FLOOR
               for host, mounts in ROOTS.items() for mount in mounts)


def manifest(source):
    target = source / 'archive.sha256'
    files = sorted(path for path in source.rglob('*') if path.is_file())
    if any(path.is_symlink() for path in source.rglob('*')):
        raise ValueError('ARCHIVE_SYMLINK_FORBIDDEN')
    if target.exists():
        subprocess.run(['sha256sum', '-c', '--status', str(target)],
                       cwd=source, check=True, timeout=1800)
        files.remove(target)
    else:
        with target.open('x') as stream:
            for path in files:
                name = str(path.relative_to(source))
                if '\n' in name or '\\' in name:
                    raise ValueError('ARCHIVE_UNSAFE_FILENAME')
                stream.write(sha(path) + '  ' + name + '\n')
        target.chmod(0o600)
    return len(files), sum(path.stat().st_size for path in files)


def copy_tree(worker, source, staging, mount):
    socket, host = CHANNELS[worker]
    transfer = subprocess.Popen(['rsync', '-a', '--numeric-ids', '-e',
        'ssh -S ' + socket + ' -o BatchMode=yes',
        str(source) + '/', 'root@' + host + ':' + str(staging) + '/'],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        while transfer.poll() is None:
            if free(worker, mount) < FLOOR or not all_free_above_floor():
                transfer.terminate()
                transfer.communicate(timeout=15)
                raise RuntimeError('ARCHIVE_DYNAMIC_SPACE_FLOOR')
            time.sleep(5)
        _, stderr = transfer.communicate(timeout=15)
        if transfer.returncode:
            raise RuntimeError('ARCHIVE_RSYNC_FAILED:' + stderr[-400:])
    finally:
        if transfer.poll() is None:
            transfer.terminate()
            transfer.communicate(timeout=15)


def archive(run_id):
    case = run_id.split('-ATTEMPT-', 1)[0]
    if case not in ALLOCATION or not run_id.startswith(case + '-ATTEMPT-'):
        raise ValueError('ARCHIVE_FINAL_CASE_ID')
    worker, mount = ALLOCATION[case]
    if mount != '/':
        raise RuntimeError('ARCHIVE_ALLOCATION_NOT_APPROVED_ROOT_DISK')
    raw = DATA / 'runs' / run_id
    export = DATA / 'exports' / run_id
    if raw.is_symlink() or export.is_symlink() or not raw.is_dir() or not export.is_dir():
        raise ValueError('ARCHIVE_MASTER_SOURCES_MISSING')
    if (json.loads((raw / 'admission.json').read_text())['status'] !=
            'ADMITTED_FOR_GT_DRAFT' or
            json.loads((export / 'private-export-verified.json').read_text())[
                'status'] != 'PUBLIC_EXPORT_VERIFIED_PENDING_ADMISSION_AND_ARCHIVE'):
        raise RuntimeError('ARCHIVE_ADMISSION_OR_EXPORT_NOT_PASS')
    if (raw / 'archive-result.json').exists():
        raise ValueError('ARCHIVE_ALREADY_DONE')
    counts = {'raw': manifest(raw), 'export': manifest(export)}
    total = sum(row[1] for row in counts.values())
    initial = free(worker, mount)
    if initial - total < FLOOR or not all_free_above_floor():
        raise RuntimeError('ARCHIVE_WORKER_CAPACITY_BLOCKED')
    remote(worker, ['mkdir', '-p', '-m', '700', DEST])
    staging = DEST / ('.' + run_id + '.' + str(time.time_ns()) + '.incoming')
    final = DEST / run_id
    remote(worker, ['test', '!', '-e', staging])
    remote(worker, ['test', '!', '-e', final])
    remote(worker, ['mkdir', '-p', '-m', '700', staging])
    for name, source in (('raw', raw), ('export', export)):
        remote(worker, ['mkdir', '-p', '-m', '700', staging / name])
        copy_tree(worker, source, staging / name, mount)
        remote(worker, ['sh', '-c', 'cd ' + shlex.quote(str(staging / name)) +
                ' && sha256sum -c --status archive.sha256'])
    remote(worker, ['mv', '-T', staging, final])
    readback = {
        'raw_schedule': remote(worker, ['sha256sum', final / 'raw/schedule.json']).split()[0],
        'raw_admission': remote(worker, ['sha256sum', final / 'raw/admission.json']).split()[0],
        'public_manifest': remote(worker, ['sha256sum', final / 'export/public/manifest.json']).split()[0],
    }
    expected = {'raw_schedule': sha(raw / 'schedule.json'),
                'raw_admission': sha(raw / 'admission.json'),
                'public_manifest': sha(export / 'public/manifest.json')}
    if readback != expected or free(worker, mount) < FLOOR:
        raise RuntimeError('ARCHIVE_READBACK_OR_FINAL_FLOOR_FAILED')
    result = {'status': 'TWO_HOST_ARCHIVE_SHA_AND_READBACK_PASS',
              'run_id': run_id, 'master_raw': str(raw),
              'master_export': str(export), 'worker': worker,
              'worker_archive': str(final), 'files': sum(row[0] for row in counts.values()),
              'source_bytes': total, 'worker_free_before_bytes': initial,
              'worker_free_after_bytes': free(worker, mount),
              'raw_sha_manifest': sha(raw / 'archive.sha256'),
              'export_sha_manifest': sha(export / 'archive.sha256'),
              'readback': readback}
    with (raw / 'archive-result.json').open('x') as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
        stream.write('\n')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    print(json.dumps(archive(args.run_id), sort_keys=True))


if __name__ == '__main__':
    main()
