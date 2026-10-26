#!/usr/bin/env python3
"""Verify every offsite archive file before it can replace a local replica."""

import argparse
import hashlib
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


BASE = Path('/data/ops-competition-2026-archive/incoming')
FLOOR = 5_000_000_000


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def manifest_entries(manifest):
    entries = manifest.get('files')
    if manifest.get('status') != 'COMPETITION_ARCHIVE_SOURCE_MANIFEST' or not isinstance(entries, list):
        raise ValueError('Unexpected source manifest')
    paths = set()
    for item in entries:
        raw = item.get('path')
        if not isinstance(raw, str) or not raw or raw.startswith('/'):
            raise ValueError('Unsafe manifest path')
        path = PurePosixPath(raw)
        if '..' in path.parts or str(path) != raw or raw in paths:
            raise ValueError('Unsafe or duplicate manifest path')
        if not isinstance(item.get('bytes'), int) or item['bytes'] < 0:
            raise ValueError('Invalid file size')
        if not isinstance(item.get('sha256'), str) or len(item['sha256']) != 64:
            raise ValueError('Invalid file digest')
        paths.add(raw)
    return entries


def verify(root, expected_manifest_sha):
    root = Path(root)
    if root.is_symlink() or not root.resolve().is_relative_to(BASE.resolve()):
        raise ValueError('Archive must stay under the isolated incoming root')
    manifest_path = root.parent / 'source-manifest.json'
    if digest(manifest_path) != expected_manifest_sha:
        raise ValueError('Source manifest SHA-256 mismatch')
    entries = manifest_entries(json.loads(manifest_path.read_text()))
    expected = {item['path'] for item in entries}
    actual = set()
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in dirs:
            path = Path(parent) / name
            if path.is_symlink():
                raise ValueError('Archive contains a directory symlink')
        for name in files:
            path = Path(parent) / name
            if not stat.S_ISREG(path.lstat().st_mode):
                raise ValueError('Archive contains a non-regular file')
            actual.add(path.relative_to(root).as_posix())
    if expected != actual:
        raise ValueError(f'File set differs: missing={len(expected-actual)} extra={len(actual-expected)}')
    bytes_checked = 0
    for item in entries:
        path = root / item['path']
        if path.stat().st_size != item['bytes'] or digest(path) != item['sha256']:
            raise ValueError('Archive file mismatch: ' + item['path'])
        bytes_checked += item['bytes']
    free = os.statvfs(root).f_bavail * os.statvfs(root).f_frsize
    if free < FLOOR:
        raise ValueError('Offsite disk below five GB floor')
    return {'status': 'OFFSITE_ARCHIVE_VERIFIED',
            'verified_at_utc': datetime.now(timezone.utc).isoformat(),
            'file_count': len(entries), 'bytes_checked': bytes_checked,
            'source_manifest_sha256': expected_manifest_sha,
            'root': str(root), 'data_free_bytes_after': free,
            'all_files_sha256_and_readback_pass': True,
            'not_a_formal_case': True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.parent != args.root.parent:
        raise ValueError('Immutable verification output must be beside the archive')
    result = verify(args.root, args.manifest_sha256)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    args.output.chmod(0o600)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
