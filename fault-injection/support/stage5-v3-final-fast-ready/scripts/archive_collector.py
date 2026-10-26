#!/usr/bin/env python3
"""Per-file SHA and restored-read proof for the no-fault Collector study."""

import argparse
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import socket

from clean_protocol import assert_disk, save_new, sha


RUN_ID = 'S5V3FFR-COLLECTOR-20261013'
MASTER = Path('/data/stage5-v3/final-fast-ready/runs') / RUN_ID
WORKER = Path('/data/rca-archives/stage5-v3-final-fast-ready') / RUN_ID


def files(root):
    if not root.is_dir() or root.is_symlink():
        raise RuntimeError('Run archive absent or symlink')
    result = {}
    for path in root.rglob('*'):
        if path.is_symlink():
            raise RuntimeError('Run archive contains symlink')
        if path.is_file():
            result[str(path.relative_to(root))] = {'sha256': sha(path),
                'bytes': path.stat().st_size}
    if not {'schedule.json', 'result.json', 'monitor.jsonl',
            'state-machine/sessions.jsonl', 'controls/image_asset.jsonl',
            'controls/cart_view.jsonl'} <= set(result):
        raise RuntimeError('Collector study raw sources incomplete')
    return result


def restore(root):
    schedule = json.loads((root / 'schedule.json').read_text())
    result = json.loads((root / 'result.json').read_text())
    monitor = [json.loads(line) for line in (root / 'monitor.jsonl').read_text().splitlines()]
    sessions = [json.loads(line) for line in (root /
        'state-machine/sessions.jsonl').read_text().splitlines()]
    controls = {name: sum(1 for _ in (root / 'controls' /
        (name + '.jsonl')).open()) for name in ('image_asset', 'cart_view')}
    trace = root / 'timed-traces/scoring_plus120'
    pages = sorted(trace.glob('page-*.json.gz')) if trace.is_dir() else []
    span_page_hits = None
    if pages:
        with gzip.open(pages[0], 'rt', encoding='utf-8') as source:
            span_page_hits = len(json.load(source)['hits']['hits'])
    if schedule['id'] != RUN_ID or result['run_id'] != RUN_ID or not monitor or \
            not sessions or any(v == 0 for v in controls.values()):
        raise RuntimeError('Collector archive restore invalid')
    return {'run_id': RUN_ID, 'monitor_samples': len(monitor),
        'sessions': len(sessions), 'control_rows': controls,
        'scoring_span_page_hits': span_page_hits,
        'timed_status': result.get('TIMED_TRACE_COMPLETION', 'INCOMPLETE')}


def create(manifest):
    if socket.gethostname() != 'k8s-master':
        raise RuntimeError('Master source inventory only')
    assert_disk(MASTER, 0)
    result = {'status': 'COLLECTOR_RAW_FROZEN_FOR_ARCHIVE',
        'source': str(MASTER), 'run_id': RUN_ID,
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'files': files(MASTER), 'restore': restore(MASTER),
        'fault_injected': False}
    result['file_count'] = len(result['files'])
    result['bytes'] = sum(v['bytes'] for v in result['files'].values())
    save_new(manifest, result)
    return result


def verify(manifest, output):
    if socket.gethostname() not in ('k8s-worker2', 'k8s-worker3'):
        raise RuntimeError('Only registered Worker replicas can verify')
    expected = json.loads(manifest.read_text())
    observed_files, observed_restore = files(WORKER), restore(WORKER)
    if expected['source'] != str(MASTER) or expected['files'] != observed_files or \
            expected['restore'] != observed_restore:
        raise RuntimeError('Collector archive SHA or restored-read mismatch')
    result = {'status': 'COLLECTOR_DUAL_ARCHIVE_VERIFIED', 'worker': socket.gethostname(),
        'run_id': RUN_ID, 'manifest_sha256': sha(manifest),
        'files': len(observed_files), 'bytes': expected['bytes'],
        'restore': observed_restore,
        'verified_utc': datetime.now(timezone.utc).isoformat()}
    save_new(output, result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('create', 'verify'))
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = create(args.manifest) if args.action == 'create' else verify(
        args.manifest, args.output)
    print(json.dumps({'status': result['status'], 'files': result['files'] if
        isinstance(result['files'], int) else len(result['files'])}))


if __name__ == '__main__': main()
