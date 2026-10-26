#!/usr/bin/env python3
"""Deterministic restored-read proof after preserved v1 glob-order failure."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import socket

from archive_clean import DATA, WORKER, inventory
from clean_protocol import assert_disk, save_new, sha


RUN_ID = 'S5V3FFR-CLEAN-20261013-S1'


def restore_sorted(root):
    schedule = json.loads((root / 'schedule.json').read_text())
    pipeline = json.loads((root / 'capture-pipeline/pipeline-result.json').read_text())
    paths = {name: sorted((root / 'capture-pipeline/captures/during' /
        name).glob('*.json'))[0] for name in ('metrics', 'logs', 'trace-storage-pages')}
    metric = json.loads(paths['metrics'].read_text())
    logs = json.loads(paths['logs'].read_text())
    traces = json.loads(paths['trace-storage-pages'].read_text())
    if (schedule['run_id'] != RUN_ID or pipeline['status'] != 'CAPTURE_PIPELINE_PASS'
        or metric['status'] != 'success' or 'hits' not in logs or 'hits' not in traces):
        raise RuntimeError('Deterministic archived sample cannot be parsed')
    return {'run_id': RUN_ID,
        'sample_paths': {name: str(path.relative_to(root)) for name, path in paths.items()},
        'metric_series': len(metric['data']['result']),
        'log_page_hits': len(logs['hits']['hits']),
        'trace_page_hits': len(traces['hits']['hits'])}


def create(original, output):
    if socket.gethostname() != 'k8s-master':
        raise RuntimeError('Master only')
    source = DATA / RUN_ID
    assert_disk(source, 0)
    old = json.loads(original.read_text())
    files = inventory(source)
    if old['source'] != str(source) or old['files'] != files:
        raise RuntimeError('Original source manifest and current raw tree differ')
    result = {'status': 'RAW_RUN_DETERMINISTIC_RESTORE_MANIFEST_V2',
        'run_id': RUN_ID, 'source': str(source), 'files': files,
        'source_bytes': old['source_bytes'], 'file_count': len(files),
        'restore_sample': restore_sorted(source),
        'original_manifest_sha256': sha(original),
        'reason': 'v1 verifier used unsorted glob for a sample metric JSON file',
        'original_v1_failure_preserved': True,
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'fault_injected': False}
    save_new(output, result)
    return result


def verify(manifest_path, output):
    if socket.gethostname() not in ('k8s-worker2', 'k8s-worker3'):
        raise RuntimeError('Only registered Workers verify')
    expected = json.loads(manifest_path.read_text())
    root = WORKER / RUN_ID
    files = inventory(root)
    restored = restore_sorted(root)
    if expected['files'] != files or expected['restore_sample'] != restored:
        raise RuntimeError('Worker archive SHA or deterministic restore mismatch')
    result = {'status': 'DUAL_ARCHIVE_WORKER_V2_VERIFIED',
        'worker': socket.gethostname(), 'run_id': RUN_ID,
        'source_manifest_sha256': sha(manifest_path),
        'original_manifest_sha256': expected['original_manifest_sha256'],
        'file_count': len(files), 'source_bytes': expected['source_bytes'],
        'restore_sample': restored,
        'verified_utc': datetime.now(timezone.utc).isoformat()}
    save_new(output, result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('create', 'verify'))
    parser.add_argument('--original-manifest', type=Path)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    value = create(args.original_manifest, args.manifest) if args.action == 'create' \
        else verify(args.manifest, args.output)
    print(json.dumps({'status': value['status'], 'run_id': RUN_ID}))


if __name__ == '__main__': main()
