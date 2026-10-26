#!/usr/bin/env python3
"""Verify and physically restore a synthetic full-size archive on one Worker."""

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path('/root/ops-competition-2026/final-case-archive-qualification-full-20261019')
FLOOR = 5_000_000_000


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def verify(root, expected):
    result = {}
    for rel, metadata in expected.items():
        path = root / rel
        if (path.is_symlink() or not path.is_file() or
                path.stat().st_size != metadata['bytes']):
            raise RuntimeError('Archive SHA/size mismatch: ' + rel)
        digest = sha(path)
        if digest != metadata['sha256']:
            raise RuntimeError('Archive SHA/size mismatch: ' + rel)
        result[rel] = {'bytes': path.stat().st_size, 'sha256': digest,
                       'allocated_bytes': path.stat().st_blocks * 512}
        if result[rel]['allocated_bytes'] < metadata['bytes'] * .95:
            raise RuntimeError('Archive was sparse, not a real capacity test: ' + rel)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if (args.output.exists() or socket.gethostname() not in
            ('k8s-worker1', 'k8s-worker2', 'k8s-worker3')):
        raise RuntimeError('Wrong Worker or immutable output exists')
    archive = ROOT / 'archive'
    manifest = archive / 'source-manifest.json'
    data = json.loads(manifest.read_text())
    if (data['status'] != 'SYNTHETIC_NO_SENSITIVE_FULLSIZE_ARCHIVE' or
            data['payload_bytes'] != 7_350_324_113 or
            set(data['files']) != {'raw/telemetry.bin', 'public/export.bin'}):
        raise RuntimeError('Unregistered archive fixture')
    before = verify(archive, data['files'])
    restored = ROOT / 'restored'
    restored.mkdir(mode=0o700, exist_ok=False)
    for rel in data['files']:
        destination = restored / rel
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        subprocess.run(['cp', '--reflink=never', '--sparse=never',
                        str(archive / rel), str(destination)], check=True, timeout=900)
        destination.chmod(0o600)
        if shutil.disk_usage('/').free < FLOOR:
            raise RuntimeError('Root disk crossed absolute five-GB floor')
    after = verify(restored, data['files'])
    result = {'status': 'FULLSIZE_ARCHIVE_SHA_AND_RESTORED_READ_PASS',
              'at_utc': datetime.now(timezone.utc).isoformat(),
              'host': socket.gethostname(), 'source_manifest_sha256': sha(manifest),
              'archive_files': before, 'restored_files': after,
              'root_free_after_bytes': shutil.disk_usage('/').free,
              'data_free_after_bytes': shutil.disk_usage('/data').free,
              'historical_archive_touched': False}
    if min(result['root_free_after_bytes'], result['data_free_after_bytes']) < FLOOR:
        raise RuntimeError('Five-GB floor violated after verification')
    args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    args.output.chmod(0o600)
    print(json.dumps({'status': result['status'], 'host': result['host'],
                      'bytes': data['payload_bytes']}))


if __name__ == '__main__':
    main()
