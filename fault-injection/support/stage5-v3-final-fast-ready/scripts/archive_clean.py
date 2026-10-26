#!/usr/bin/env python3
"""Immutable Master inventory, two-worker byte verification and final health closure."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import socket

from clean_protocol import assert_disk, save_new, sha


ROOT = Path('/root/stage5-v3-final-fast-ready')
DATA = Path('/data/stage5-v3/final-fast-ready/runs')
WORKER = Path('/data/rca-archives/stage5-v3-final-fast-ready')
WORKERS = {'k8s-worker2', 'k8s-worker3'}


def inventory(folder):
    if not folder.is_dir() or folder.is_symlink():
        raise RuntimeError('Archive directory absent or symlink')
    files = {}
    for path in folder.rglob('*'):
        if path.is_symlink():
            raise RuntimeError('Archive has symlink')
        if path.is_file():
            files[str(path.relative_to(folder))] = {'sha256': sha(path),
                'bytes': path.stat().st_size}
    required = {'schedule.json', 'run-result.json',
                'load_pair/state-machine/sessions.jsonl',
                'load_pair/controls/image_asset.jsonl',
                'load_pair/controls/cart_view.jsonl',
                'capture-pipeline/pipeline-result.json'}
    if not required <= set(files) or not any(p.startswith(
            'capture-pipeline/captures/during/metrics/') for p in files):
        raise RuntimeError('Raw lifecycle source families incomplete')
    return files


def restore(folder):
    schedule = json.loads((folder / 'schedule.json').read_text())
    pipeline = json.loads((folder / 'capture-pipeline/pipeline-result.json').read_text())
    metrics = next((folder / 'capture-pipeline/captures/during/metrics').glob('*.json'))
    logs = next((folder / 'capture-pipeline/captures/during/logs').glob('*.json'))
    traces = next((folder / 'capture-pipeline/captures/during/trace-storage-pages').glob('*.json'))
    metric_rows = json.loads(metrics.read_text())
    log_rows = json.loads(logs.read_text())
    trace_rows = json.loads(traces.read_text())
    if not schedule['run_id'] or pipeline['status'] != 'CAPTURE_PIPELINE_PASS' or \
            metric_rows['status'] != 'success' or 'hits' not in log_rows or \
            'hits' not in trace_rows:
        raise RuntimeError('Archived source cannot be restored')
    return {'run_id': schedule['run_id'], 'metric_series': len(metric_rows[
        'data']['result']), 'log_page_hits': len(log_rows['hits']['hits']),
        'trace_page_hits': len(trace_rows['hits']['hits'])}


def fresh(path, expected):
    value = json.loads(path.read_text())
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(value[
        'captured_at_utc' if expected == 'GLOBAL_SAFETY_PASS' else
        'at_utc'])).total_seconds()
    if value['status'] != expected or not 0 <= age <= 180:
        raise RuntimeError('Final health or five-host residue failed/stale')
    return value


def create(run_id, output):
    if socket.gethostname() != 'k8s-master':
        raise RuntimeError('Master source inventory only')
    source = DATA / run_id
    assert_disk(source, 0)
    files = inventory(source)
    value = {'status': 'RAW_RUN_FROZEN_FOR_DUAL_ARCHIVE',
        'run_id': run_id, 'created_utc': datetime.now(timezone.utc).isoformat(),
        'source': str(source), 'files': files, 'file_count': len(files),
        'source_bytes': sum(row['bytes'] for row in files.values()),
        'restore_sample': restore(source), 'fault_injected': False}
    save_new(output, value)
    return value


def verify(run_id, manifest_path, output):
    if socket.gethostname() not in WORKERS:
        raise RuntimeError('Worker archive verification only')
    manifest = json.loads(manifest_path.read_text())
    if manifest['run_id'] != run_id or manifest['source'] != str(DATA / run_id):
        raise RuntimeError('Wrong source identity')
    archive = WORKER / run_id
    files = inventory(archive)
    restored = restore(archive)
    if files != manifest['files'] or restored != manifest['restore_sample']:
        raise RuntimeError('Worker archive SHA or restore mismatch')
    value = {'status': 'DUAL_ARCHIVE_WORKER_VERIFIED',
        'worker': socket.gethostname(), 'run_id': run_id,
        'source_manifest_sha256': sha(manifest_path),
        'archive': str(archive), 'files': len(files),
        'source_bytes': manifest['source_bytes'], 'restore_sample': restored,
        'verified_utc': datetime.now(timezone.utc).isoformat()}
    save_new(output, value)
    return value


def finalize(run_id, manifest_path, proofs, health_path, residue_path, output):
    if socket.gethostname() != 'k8s-master':
        raise RuntimeError('Master finalization only')
    manifest = json.loads(manifest_path.read_text())
    if manifest['run_id'] != run_id or manifest['files'] != inventory(DATA / run_id):
        raise RuntimeError('Master source changed after archive freeze')
    valid = {}
    for path in proofs:
        row = json.loads(path.read_text())
        if (row.get('status') != 'DUAL_ARCHIVE_WORKER_VERIFIED' or
            row.get('source_manifest_sha256') != sha(manifest_path) or
            row.get('run_id') != run_id or row.get('worker') not in WORKERS):
            raise RuntimeError('Worker archive proof invalid')
        valid[row['worker']] = row
    if set(valid) != WORKERS:
        raise RuntimeError('Both Worker archives required')
    health = fresh(health_path, 'GLOBAL_SAFETY_PASS')
    residue = fresh(residue_path, 'HOST_RESIDUE_PASS')
    value = {'status': 'FULL_NO_FAULT_CLEAN_LIFECYCLE_ARCHIVED',
        'run_id': run_id, 'at_utc': datetime.now(timezone.utc).isoformat(),
        'source_manifest_sha256': sha(manifest_path),
        'worker_proof_sha256': {name: sha(path) for name, path in zip(
            (json.loads(path.read_text())['worker'] for path in proofs), proofs)},
        'five_host_residue': residue['status'], 'global_safety': health['status'],
        'fault_injected': False, 'formal_case': False}
    save_new(output, value)
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('create', 'verify', 'finalize'))
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--proof', type=Path, action='append')
    parser.add_argument('--health', type=Path)
    parser.add_argument('--residue', type=Path)
    args = parser.parse_args()
    if args.action == 'create': value = create(args.run_id, args.manifest)
    elif args.action == 'verify': value = verify(args.run_id, args.manifest, args.output)
    else: value = finalize(args.run_id, args.manifest, args.proof,
                           args.health, args.residue, args.output)
    print(json.dumps({'status': value['status'], 'run_id': args.run_id}))


if __name__ == '__main__': main()
