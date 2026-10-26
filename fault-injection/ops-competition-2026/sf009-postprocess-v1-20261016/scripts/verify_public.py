#!/usr/bin/env python3
"""Independent six-file Public integrity and Parquet readback."""

import hashlib
import json
from datetime import datetime
from pathlib import Path

import pyarrow.compute as pc
import pyarrow.parquet as pq


ROOT = Path('/data/stage5-v3/ops-competition-2026/packages/SF-009-ATTEMPT-01/public')
OUTPUT = Path('/root/ops-competition-2026/sf009-postprocess-v1-20261016/results/public-verification.json')
EXPECTED = {'metrics.parquet', 'logs.parquet', 'traces.parquet',
            'trace_index.parquet', 'topology.json', 'manifest.json'}


def sha(path):
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024**2), b''):
            checksum.update(block)
    return checksum.hexdigest()


if __name__ == '__main__':
    files = {path.name for path in ROOT.iterdir() if path.is_file()}
    if files != EXPECTED or any(path.is_symlink() for path in ROOT.iterdir()):
        raise RuntimeError('Public directory differs from six-file whitelist')
    manifest = json.loads((ROOT / 'manifest.json').read_text())
    if (manifest['case_id'] != 'SF-009' or
            manifest['private_leak_scan'] != 'PASS' or
            set(manifest['files']) != EXPECTED - {'manifest.json'}):
        raise RuntimeError('Public manifest identity or leak audit failed')
    window = manifest['window']
    start = datetime.fromisoformat(window['start_utc'])
    end = datetime.fromisoformat(window['end_utc'])
    if (end-start).total_seconds() != 1800:
        raise RuntimeError('Public window is not 1800 seconds')
    for name, item in manifest['files'].items():
        path = ROOT / name
        if path.stat().st_size != item['bytes'] or sha(path) != item['sha256']:
            raise RuntimeError('Public file hash or length mismatch: ' + name)
    counts = {}
    time_bounds = {}
    for name, field in (('metrics', 'timestamp'), ('logs', 'timestamp'),
                        ('traces', 'start_time')):
        path = ROOT / (name + '.parquet')
        parquet = pq.ParquetFile(path)
        if parquet.metadata.num_rows != manifest['row_counts'][name]:
            raise RuntimeError('Parquet row count mismatch: ' + name)
        timestamps = pq.read_table(path, columns=[field])[field]
        bounds = pc.min_max(timestamps).as_py()
        if not (start <= bounds['min'] <= bounds['max'] < end):
            raise RuntimeError('Parquet timestamp outside frozen window: ' + name)
        counts[name] = parquet.metadata.num_rows
        time_bounds[name] = {key: value.isoformat() for key,value in bounds.items()}
    traces = set(pq.read_table(ROOT / 'traces.parquet',
                                columns=['trace_id'])['trace_id'].to_pylist())
    index = pq.read_table(ROOT / 'trace_index.parquet', columns=['trace_id'])
    ids = index['trace_id'].to_pylist()
    if (len(ids) != manifest['trace_index_rows'] or len(ids) != len(set(ids)) or
            set(ids) != traces):
        raise RuntimeError('Trace Index and Span identities differ')
    if any(len(manifest['minute_counts'][name]) != 30 or
           any(int(value) <= 0 for value in manifest['minute_counts'][name].values())
           for name in ('metrics', 'logs', 'traces')):
        raise RuntimeError('Public three-modality minute coverage incomplete')
    result = {'status': 'PUBLIC_PACKAGE_VERIFIED', 'case_id': 'SF-009',
              'run_id': 'SF-009-ATTEMPT-01', 'file_count': len(files),
              'bytes': sum((ROOT / name).stat().st_size for name in files),
              'manifest_sha256': sha(ROOT / 'manifest.json'),
              'row_counts': counts, 'trace_index_rows': len(ids),
              'time_bounds': time_bounds, 'private_leak_scan': 'PASS',
              'all_30_minutes_three_modalities': True}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with OUTPUT.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    OUTPUT.chmod(0o600)
    print(json.dumps(result))
