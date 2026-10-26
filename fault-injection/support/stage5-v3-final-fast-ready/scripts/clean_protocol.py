"""Fail-closed checks shared by the independent no-fault lifecycle runner."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


MOUNT = Path('/data/stage5-v3')
DATA_ROOT = MOUNT / 'final-fast-ready'
UUID = 'a35051a2-3d09-4b83-bbc0-3bb055e8a4f3'
PHASES = {'BEFORE': (0, 600), 'DURING': (600, 900), 'AFTER': (900, 1500)}
STAGES = ('PREFLIGHT', 'SOURCE_LOCK', 'BEFORE', 'DURING', 'AFTER',
          'TRACE_COMPLETION', 'QUALITY_EVALUATION', 'ARCHIVE', 'FINAL_HEALTH')


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def save_new(path, value):
    path = Path(path)
    if path.exists():
        raise ValueError('Output already exists: ' + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write('\n')


def validate_plan(plan):
    if (plan.get('status') != 'PREREGISTERED_NO_FAULT_CLEAN' or
            plan.get('fault_injected') is not False or
            plan.get('formal_case') is not False or
            plan.get('duration_seconds') != 1500 or
            plan.get('phases_seconds') != {'before': 600, 'during': 300, 'after': 600} or
            plan.get('target_path') != 'NETWORK_PATH/k8s-worker2/k8s-worker3' or
            plan.get('trace_source') != 'JAEGER_OPENSEARCH_STORAGE' or
            plan.get('seed') not in (864101, 864102) or
            plan.get('workload', {}).get('sessions_per_second') != 1.4 or
            plan['workload'].get('weights') != {'browse': 20, 'shopping': 30,
                                               'checkout': 50} or
            plan['workload'].get('image_per_second') != 1 or
            plan['workload'].get('cart_per_second') != 2 or
            plan.get('data_root') != str(DATA_ROOT)):
        raise ValueError('Clean plan differs from pre-registered no-fault contract')
    run_id = plan.get('id', '')
    if not run_id.startswith('S5V3FFR-CLEAN-') or not run_id.replace('-', '').isalnum():
        raise ValueError('Invalid immutable run ID')
    if plan.get('planned_raw_bytes', 0) < 5 * 1024 ** 3:
        raise ValueError('Insufficient reserved raw-data budget')
    return run_id


def validate_disk(mount, target, root_free, data_free, free_inodes, planned_bytes,
                  root_floor=46_000_000_000, data_floor=64 * 1024 ** 3):
    if (mount.get('source') != '/dev/sdb1' or mount.get('uuid') != UUID or
            mount.get('fstype') != 'ext4' or not mount.get('rw') or
            mount.get('same_device_as_root') or not mount.get('writable') or
            root_free < root_floor or data_free - planned_bytes < data_floor or
            free_inodes < 100_000):
        raise RuntimeError('Independent data disk or unchanged safety budget failed')
    path = Path(target)
    if not path.is_absolute() or not path.is_relative_to(DATA_ROOT / 'runs'):
        raise RuntimeError('Raw output path escapes registered data root')


def assert_disk(target, planned_bytes):
    def field(name):
        return subprocess.check_output(['findmnt', '-n', '-o', name, '-T', str(MOUNT)],
                                       text=True, timeout=10).strip()
    options = field('OPTIONS').split(',')
    info = {'source': field('SOURCE'), 'uuid': field('UUID'),
            'fstype': field('FSTYPE'), 'rw': 'rw' in options,
            'writable': os.access(MOUNT, os.W_OK),
            'same_device_as_root': os.stat(MOUNT).st_dev == os.stat('/').st_dev}
    stat = os.statvfs(MOUNT)
    free_inodes = stat.f_favail
    root_free, data_free = shutil.disk_usage('/').free, shutil.disk_usage(MOUNT).free
    validate_disk(info, target, root_free, data_free, free_inodes, planned_bytes)
    if any(part.is_symlink() for part in (MOUNT, DATA_ROOT, DATA_ROOT / 'runs', target)):
        raise RuntimeError('Symlink in raw output path')
    return {'mount': info, 'root_free_bytes': root_free,
            'data_free_bytes': data_free, 'data_free_inodes': free_inodes,
            'planned_bytes': planned_bytes}


def validate_snapshot(before, current):
    for key in ('pod_uids', 'endpoint_slices_sha256', 'node_placement',
                'route_proof_sha256', 'live_route_sha256',
                'collector_config_sha256'):
        if before.get(key) != current.get(key) or not before.get(key):
            raise RuntimeError('Source lock changed: ' + key)
    if before.get('network_path') != 'NETWORK_PATH/k8s-worker2/k8s-worker3':
        raise RuntimeError('Stale NetworkPath direction')


def validate_capture(phase, manifest, start, end):
    if (manifest.get('phase') != phase or
            manifest.get('trace_source') != 'JAEGER_OPENSEARCH_STORAGE' or
            manifest.get('window') != {'start_utc': start, 'end_utc': end} or
            not manifest.get('sha256') or
            manifest.get('trace_records', 0) <= 0 or
            manifest.get('log_records', 0) <= 0):
        raise RuntimeError('Live capture incomplete or misaligned: ' + phase)


def validate_trace(phase, first, plus60, scoring, audit):
    if any(not row or row.get('phase') != phase for row in
           (first, plus60, scoring, audit)):
        raise RuntimeError('Trace completion phase mismatch')
    if (scoring.get('status') != 'FINAL_SNAPSHOT_FROZEN' or
            audit.get('status') != 'LATE_AUDIT_FROZEN_UNCLASSIFIED' or
            scoring.get('query_duration_seconds', 999) > 60 or
            audit.get('query_duration_seconds', 999) > 60):
        raise RuntimeError('Timed scoring or late audit incomplete')
    for row, maximum in ((scoring, 180), (audit, 300)):
        end = datetime.fromisoformat(first['query_end_utc']).timestamp()
        done = datetime.fromisoformat(row['completed_utc']).timestamp()
        if done - end > maximum:
            raise RuntimeError('Trace deadline missed')


def validate_archive(source, workers):
    source_files = {str(p.relative_to(source)): sha(p) for p in source.rglob('*')
                    if p.is_file()}
    if not source_files or any(p.is_symlink() for p in source.rglob('*')):
        raise RuntimeError('Source archive invalid')
    for worker in workers:
        copies = {str(p.relative_to(worker)): sha(p) for p in worker.rglob('*')
                  if p.is_file()}
        if copies != source_files:
            raise RuntimeError('Worker archive SHA mismatch')
        for name in ('schedule.json', 'capture-pipeline/pipeline-result.json'):
            if name not in copies or not json.loads((worker / name).read_text()):
                raise RuntimeError('Worker restore read failed')
    return {'file_count': len(source_files), 'files_sha256': source_files,
            'workers': [str(path) for path in workers]}


def next_stage(current, wanted, failed=False):
    if failed:
        return 'FAILED_TERMINAL'
    if current is None and wanted == STAGES[0]:
        return wanted
    if current in STAGES and STAGES.index(current) + 1 < len(STAGES) and \
            wanted == STAGES[STAGES.index(current) + 1]:
        return wanted
    raise ValueError('Illegal lifecycle transition: ' + str(current) + ' -> ' + wanted)
