#!/usr/bin/env python3
"""Read-only SHA audit of the ten historical READY Public bundles."""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
LEDGER = OPS / 'plans/SF-003-to-SF-012-production-v2-ledger.json'
HANDOFF = OPS / 'plans/SF-003-to-SF-012-master-ready-handoff-20261018.json'
EXPECTED_LEDGER_SHA = '813035c9c5e69741c73de7d0afb92e427d8745714992ab74c0c41c414f7e69f8'
EXPECTED_HANDOFF_SHA = '8b8d9ed83779370318c94b40a9be0a401b3d38705d35cb586f26d6963164d401'
PUBLIC = {'metrics.parquet', 'logs.parquet', 'traces.parquet',
          'trace_index.parquet', 'topology.json', 'manifest.json'}


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def audit(row):
    root = Path(row['public_path_on_master'])
    if set(path.name for path in root.iterdir()) != PUBLIC:
        raise RuntimeError(row['case_id'] + ': six-file Public whitelist changed')
    manifest_path = root / 'manifest.json'
    if sha(manifest_path) != row['public_manifest_sha256']:
        raise RuntimeError(row['case_id'] + ': Manifest SHA changed')
    manifest = json.loads(manifest_path.read_text())
    files = {name: {'bytes': path.stat().st_size, 'sha256': sha(path)}
             for name in PUBLIC if name != 'manifest.json'
             for path in [root / name]}
    if files != manifest['files']:
        raise RuntimeError(row['case_id'] + ': Public file SHA/size mismatch')
    if (manifest['case_id'] != row['case_id'] or
            manifest['window']['start_utc'] != row['window']['start_utc'] or
            manifest['window']['end_utc'] != row['window']['end_utc'] or
            manifest['row_counts'] != row['row_counts'] or
            manifest['trace_index_rows'] != row['trace_index_rows'] or
            sum(item['bytes'] for item in files.values()) +
            manifest_path.stat().st_size != row['public_total_bytes']):
        raise RuntimeError(row['case_id'] + ': Public window/count/size changed')
    start = datetime.fromisoformat(row['window']['start_utc'])
    end = datetime.fromisoformat(row['window']['end_utc'])
    if (end - start).total_seconds() != 1800:
        raise RuntimeError(row['case_id'] + ': not a complete thirty-minute window')
    return {'case_id': row['case_id'], 'attempt_id': row['attempt_id'],
            'manifest_sha256': row['public_manifest_sha256'],
            'public_bytes': row['public_total_bytes'], 'files_verified': 6}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError('Historical audit output already exists')
    if sha(LEDGER) != EXPECTED_LEDGER_SHA or sha(HANDOFF) != EXPECTED_HANDOFF_SHA:
        raise RuntimeError('Historical production index changed')
    handoff = json.loads(HANDOFF.read_text())
    rows = handoff['entries']
    if (len(rows) != 10 or
            {row['case_id'] for row in rows} !=
            {f'SF-{number:03d}' for number in range(3, 13)}):
        raise RuntimeError('Historical READY case set changed')
    checked = [audit(row) for row in rows]
    result = {'status': 'TEN_HISTORICAL_READY_PUBLIC_SHA_PASS',
              'at_utc': datetime.now(timezone.utc).isoformat(),
              'ledger_sha256': sha(LEDGER), 'handoff_sha256': sha(HANDOFF),
              'cases': checked, 'public_files_verified': 60,
              'private_gt_or_raw_capture_read': False,
              'files_modified': False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': result['status'],
                      'cases': len(checked), 'files': result['public_files_verified']}))


if __name__ == '__main__':
    main()
