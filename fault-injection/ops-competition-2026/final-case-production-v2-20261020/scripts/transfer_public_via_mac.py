#!/usr/bin/env python3
"""Relay only six verified Public files from Master to protected 51 staging."""

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import tempfile
import time
from pathlib import Path


MASTER = '192.168.53.52'
TARGET = '192.168.51.51'
MASTER_SOCKET = '/tmp/sf-final-master.sock'
TARGET_SOCKET = '/tmp/sf-final-51.sock'
SOURCE_BASE = '/data/stage5-v3/ops-competition-2026'
STAGING = '/data/aiops-contest/staging'
NAMES = ('metrics.parquet', 'logs.parquet', 'traces.parquet',
         'trace_index.parquet', 'topology.json', 'manifest.json')
FLOOR = 5_000_000_000


def ssh(host, socket, args):
    result = subprocess.run(['ssh', '-S', socket, '-o', 'BatchMode=yes',
        '-o', 'ConnectTimeout=4', 'root@' + host, shlex.join(map(str, args))],
        capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError('PUBLIC_RELAY_REMOTE_FAILED:' + host + ':' +
                           result.stderr[-300:])
    return result.stdout


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024**2), b''):
            h.update(chunk)
    return h.hexdigest()


def verify_local(directory):
    if {path.name for path in directory.iterdir()} != set(NAMES):
        raise RuntimeError('PUBLIC_RELAY_FILE_ALLOWLIST')
    manifest = json.loads((directory / 'manifest.json').read_text())
    if set(manifest['files']) != set(NAMES) - {'manifest.json'}:
        raise RuntimeError('PUBLIC_RELAY_MANIFEST_ALLOWLIST')
    for name, expected in manifest['files'].items():
        path = directory / name
        if (path.is_symlink() or digest(path) != expected['sha256'] or
                path.stat().st_size != expected['bytes']):
            raise RuntimeError('PUBLIC_RELAY_SOURCE_HASH:' + name)
    for name in NAMES[:4]:
        with (directory / name).open('rb') as stream:
            if stream.read(4) != b'PAR1':
                raise RuntimeError('PUBLIC_RELAY_PARQUET_HEADER:' + name)
            stream.seek(-4, os.SEEK_END)
            if stream.read(4) != b'PAR1':
                raise RuntimeError('PUBLIC_RELAY_PARQUET_FOOTER:' + name)
    return manifest


def relay(run_id):
    case = run_id.split('-ATTEMPT-', 1)[0]
    if case not in {f'SF-{index:03d}' for index in range(13, 18)}:
        raise ValueError('PUBLIC_RELAY_CASE_SCOPE')
    source = f'{SOURCE_BASE}/exports/{run_id}/public'
    check = ('import json,pathlib,sys; r=pathlib.Path(sys.argv[1]); '
        'e=pathlib.Path(sys.argv[2]); '
        'print(json.dumps({"admission":json.loads((r/"admission.json").read_text())["status"],'
        '"archive":json.loads((r/"archive-result.json").read_text())["status"],'
        '"export":json.loads((e/"private-export-verified.json").read_text())["status"],'
        '"manifest_sha256":json.loads((e/"private-export-verified.json").read_text())["manifest_sha256"],'
        '"archive_manifest_sha256":json.loads((r/"archive-result.json").read_text())["readback"]["public_manifest"]}))')
    source_status = json.loads(ssh(MASTER, MASTER_SOCKET,
        ['python3', '-c', check, f'{SOURCE_BASE}/runs/{run_id}',
         f'{SOURCE_BASE}/exports/{run_id}']))
    if (source_status.get('admission') != 'ADMITTED_FOR_GT_DRAFT' or
            source_status.get('archive') != 'TWO_HOST_ARCHIVE_SHA_AND_READBACK_PASS' or
            source_status.get('export') !=
            'PUBLIC_EXPORT_VERIFIED_PENDING_ADMISSION_AND_ARCHIVE' or
            source_status.get('manifest_sha256') !=
            source_status.get('archive_manifest_sha256')):
        raise RuntimeError('PUBLIC_RELAY_SOURCE_NOT_READY')
    final = f'{STAGING}/{case}-incoming'
    temporary = f'{STAGING}/.{case}-{run_id}-{int(time.time())}.incoming'
    ssh(TARGET, TARGET_SOCKET, ['test', '!', '-e', final])
    ssh(TARGET, TARGET_SOCKET, ['test', '!', '-e', temporary])
    before = int(ssh(TARGET, TARGET_SOCKET,
        ['df', '-B1', '--output=avail', STAGING]).splitlines()[-1])
    with tempfile.TemporaryDirectory(prefix='sf-final-public-') as temp:
        local = Path(temp)
        for name in NAMES:
            subprocess.run(['rsync', '-a', '--checksum', '-e',
                'ssh -S ' + MASTER_SOCKET + ' -o BatchMode=yes',
                f'root@{MASTER}:{source}/{name}', str(local / name)],
                check=True, capture_output=True, timeout=900)
        manifest = verify_local(local)
        if digest(local / 'manifest.json') != source_status['manifest_sha256']:
            raise RuntimeError('PUBLIC_RELAY_MASTER_ARCHIVE_HASH_MISMATCH')
        bytes_total = sum(path.stat().st_size for path in local.iterdir())
        if before - bytes_total < FLOOR:
            raise RuntimeError('PUBLIC_RELAY_TARGET_FLOOR')
        ssh(TARGET, TARGET_SOCKET, ['mkdir', '-p', '-m', '700', temporary])
        started = time.time()
        subprocess.run(['rsync', '-a', '--checksum', '-e',
            'ssh -S ' + TARGET_SOCKET + ' -o BatchMode=yes',
            *[str(local / name) for name in NAMES],
            f'root@{TARGET}:{temporary}/'],
            check=True, capture_output=True, timeout=900)
        ssh(TARGET, TARGET_SOCKET, ['chmod', '700', temporary])
        ssh(TARGET, TARGET_SOCKET, ['chmod', '600',
            *[temporary + '/' + name for name in NAMES]])
        verify = ('import hashlib,json,pathlib,sys; p=pathlib.Path(sys.argv[1]); '
            'm=json.loads((p/"manifest.json").read_text()); '
            'assert {x.name for x in p.iterdir()}=='
            '{"metrics.parquet","logs.parquet","traces.parquet",'
            '"trace_index.parquet","topology.json","manifest.json"}; '
            'assert all(hashlib.sha256((p/n).read_bytes()).hexdigest()==v["sha256"] '
            'and (p/n).stat().st_size==v["bytes"] for n,v in m["files"].items()); '
            'print(json.dumps({"manifest_sha256":hashlib.sha256((p/"manifest.json").read_bytes()).hexdigest(),'
            '"bytes":sum(x.stat().st_size for x in p.iterdir())}))')
        target = json.loads(ssh(TARGET, TARGET_SOCKET,
                                ['python3', '-c', verify, temporary]))
        if (target['manifest_sha256'] != digest(local / 'manifest.json') or
                target['bytes'] != bytes_total):
            raise RuntimeError('PUBLIC_RELAY_TARGET_HASH_MISMATCH')
        after = int(ssh(TARGET, TARGET_SOCKET,
            ['df', '-B1', '--output=avail', STAGING]).splitlines()[-1])
        if after < FLOOR:
            raise RuntimeError('PUBLIC_RELAY_TARGET_FLOOR_AFTER_TRANSFER')
        ssh(TARGET, TARGET_SOCKET, ['mv', '-T', temporary, final])
        return {'status': 'PUBLIC_TRANSFER_SHA_VERIFIED_NOT_PUBLISHED',
                'run_id': run_id, 'case_id': case,
                'source': f'root@{MASTER}:{source}',
                'target': f'root@{TARGET}:{final}',
                'files': len(NAMES), 'bytes': bytes_total,
                'manifest_sha256': target['manifest_sha256'],
                'row_counts': manifest['row_counts'],
                'target_free_before_bytes': before,
                'target_free_after_bytes': after,
                'elapsed_seconds': time.time() - started,
                'private_transferred': False, 'api_published': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    print(json.dumps(relay(args.run_id), sort_keys=True))


if __name__ == '__main__':
    main()
