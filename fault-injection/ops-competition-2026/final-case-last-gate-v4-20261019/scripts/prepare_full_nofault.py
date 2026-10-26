#!/usr/bin/env python3
"""Pre-register and hash one 1800-second SF-017 harmless rehearsal."""

import hashlib
import json
import os
import secrets
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

FORMAL = Path('/root/ops-competition-2026/final-case-formal-entry-v4-20261019')
SHARED = Path('/root/ops-competition-2026/final-case-unified-execution-v2-20261019')
AUTH_SHARED = Path('/root/ops-competition-2026/final-case-unified-execution-v1-20261019')
ROOT = Path('/root/ops-competition-2026/final-case-last-gate-v4-20261019')
DATA = Path('/data/stage5-v3/ops-competition-2026/rehearsals')
CAP = Path('/root/ops-competition-2026/low-stable-v4-20261017/capture')
OPS = Path('/root/ops-competition-2026')
sys.path.insert(0, str(FORMAL / 'scripts'))
from control_contract import load_manifest
from safety_monitor import sample


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024**2), b''):
            digest.update(block)
    return digest.hexdigest()


def source_lock():
    formal = [*sorted((FORMAL / 'scripts').glob('*.py'))]
    shared = [*sorted((SHARED / 'scripts').glob('*.py'))]
    auth_shared = [AUTH_SHARED / 'scripts' / name for name in
                   ('master_authorizer.py', 'auth_protocol.py',
                    'action_decision.py')]
    extra = [
        ROOT / 'scripts/prepare_full_nofault.py',
        ROOT / 'scripts/rehearsal_observer.py',
        ROOT / 'scripts/full_window_rehearsal.py',
        ROOT / 'scripts/audit_full_window.py',
        ROOT / 'scripts/archive_rehearsal.py',
        ROOT / 'scripts/sacrificial_watchdog.py',
        FORMAL / 'design/integration-contract-draft.json',
        SHARED / 'design/action-entry-map-draft.json',
        OPS / 'sf013-formal-v4-20261019/design/workload-policy.json',
        OPS / 'low-stable-v4-20261017/formal-v2/fixed_business_streams_plain.py',
        Path('/root/cross-layer-observability-control-rehearsal/scripts/workload_generator_rehearsal.py'),
        Path('/root/cross-layer-workload-generation/design/people.json'),
        Path('/root/stage5-v3-observability-diagnostics/snapshots/reference/workload-state-machine-c3.yaml'),
        OPS / 'low-stable-v4-20261017/design/load-profiles-v4b.yaml',
        OPS / 'sf002-production-20261014/scripts/bounded_storage.py',
        *sorted(CAP.glob('*.py')),
    ]
    paths = sorted(set([*formal, *shared, *auth_shared, *extra]))
    if any(not path.is_file() or path.is_symlink() for path in paths):
        raise RuntimeError('NOFAULT_SOURCE_MISSING_OR_SYMLINK')
    return {str(path): sha(path) for path in paths}


def create():
    if socket.gethostname() != 'k8s-master':
        raise RuntimeError('NOFAULT_PREPARE_MASTER_ONLY')
    token = secrets.token_hex(4).upper()
    run = 'SF-017-ATTEMPT-MARKERTEST-' + token
    t0 = time.time() + 150
    action_base = {'run_id': run, 'capability': 'MARKER_ONLY',
                   'effect': 'MARKER',
                   'entrypoint': str(SHARED / 'scripts/marker_cli.py')}
    http_id = run + '-HTTP'
    service_id = run + '-SERVICE'
    actions = [
        {**action_base, 'action_id': http_id, 'kind': 'HTTP',
         'worker': 'k8s-master', 'mechanism': 'HTTPReqDelay',
         'duration_seconds': 240,
         'timer_command': str(FORMAL / 'scripts/http_workload.py'),
         'runtime': str(FORMAL / 'runtime' / run / http_id),
         'planned_start_utc': iso(t0 + 600)},
        {**action_base, 'action_id': service_id, 'kind': 'SERVICE',
         'worker': 'k8s-worker3', 'mechanism': 'ServicePause',
         'duration_seconds': 240,
         'timer_command': str(FORMAL / 'scripts/worker_fault_entry.py'),
         'runtime': str(FORMAL / 'runtime' / run / service_id),
         'planned_start_utc': iso(t0 + 960),
         'unit': 'ops-sf017-' + service_id.lower()},
    ]
    controller = 'ops-sf017-' + run.lower() + '-controller'
    source = source_lock()
    manifest = {'schema': 'FINAL_CASE_UNIFIED_ENTRY_V1',
                'mode': 'HARMLESS_REHEARSAL', 'run_id': run,
                'rehearsal_duration_seconds': 1800,
                'rehearsal_data_root': str(DATA / (run + '-full1800')),
                'start_utc': iso(t0), 'window_end_utc': iso(t0 + 1800),
                'workload_seed': 31, 'safety': 'NOFAULT_BASIC_HEALTH_PASS',
                'actions': actions,
                'controller_unit': controller,
                'guard_unit': controller + '-guard',
                'monitor_unit': controller + '-monitor',
                'source_sha256': {key: value for key, value in source.items()
                    if key.startswith(str(FORMAL / 'scripts')) or
                       key.startswith(str(SHARED / 'scripts')) or
                       key.startswith(str(AUTH_SHARED / 'scripts'))},
                'capture_source_sha256': source}
    checked = sample(manifest)
    if checked['status'] != 'DYNAMIC_STORAGE_AND_BASIC_HEALTH_PASS':
        raise RuntimeError('NOFAULT_PREPARE_BASIC_SAFETY')
    folder = FORMAL / 'rehearsal' / run
    folder.mkdir(parents=True, mode=0o700, exist_ok=False)
    path = folder / 'manifest.json'
    with path.open('x') as stream:
        json.dump(manifest, stream, indent=2, sort_keys=True)
        stream.write('\n')
    digest = sha(path)
    load_manifest(path, digest)
    freeze = {'status': 'FROZEN_HARMLESS_1800_CANDIDATE',
              'at_utc': iso(time.time()), 'run_id': run,
              'manifest_path': str(path), 'manifest_sha256': digest,
              'trace_source': 'JAEGER_OPENSEARCH_STORAGE',
              'source_sha256': source, 'preflight': checked,
              'formal_attempt_created': False,
              'real_faults_injected': 0}
    freeze_path = folder / 'freeze-manifest.json'
    with freeze_path.open('x') as stream:
        json.dump(freeze, stream, indent=2, sort_keys=True)
        stream.write('\n')
    return {'run_id': run, 'start_utc': manifest['start_utc'],
            'manifest_path': str(path), 'manifest_sha256': digest,
            'freeze_path': str(freeze_path),
            'freeze_sha256': sha(freeze_path)}


if __name__ == '__main__':
    print(json.dumps(create(), sort_keys=True), flush=True)
