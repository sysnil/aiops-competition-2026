#!/usr/bin/env python3
"""Freeze one new 1800-second Marker-only, live-auth validation plan."""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
ROOT = OPS / 'final-case-fail-closed-v1-20261019'
DATA = Path('/data/stage5-v3/ops-competition-2026/final-case-fail-closed-v1-20261019')
OLD = OPS / 'final-case-production-readiness-20261019/design/full-chain-low-stable-public-safe-contract.json'
TARGET = ROOT / 'design/live-auth-nofault-1800.json'
FORMAL = OPS / 'sf013-adapter-v4-20261019/scripts'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    if TARGET.exists() or (DATA / 'validation-runs-v7/VAL7').exists():
        raise RuntimeError('No overwrite of plan or no-fault data')
    old = json.loads(OLD.read_text())
    if (old['status'] != 'PREREGISTERED_FINAL_CASE_NOFAULT_1800_MARKER_ONLY' or
            old['runs']['VAL6']['duration_seconds'] != 1800 or
            old['faults_allowed'] is not False):
        raise RuntimeError('Previous successful no-fault contract changed')
    external = {path: digest for path, digest in old['source_sha256'].items()
                if ('final-case-production-readiness-20261019' not in path and
                    'final-case-timer-safety-v1-20261019' not in path and
                    'sf013-adapter-v3-20261019' not in path)}
    paths = list((ROOT / 'validation-v7').glob('*.py'))
    paths += [ROOT / 'scripts' / name for name in (
        'start_formal_nofault.py', 'readiness_observer.py',
        'marker_controller.py', 'marker_guard.py', 'marker_worker.py',
        'master_authorizer.py', 'worker_auth.py', 'auth_protocol.py',
        'guard_core.py', 'timer_lifecycle.py', 'handshake.py')]
    paths += [FORMAL / name for name in (
        'fault_worker.py', 'run_registry.py', 'qualified_adapter.py',
        'sf003_controller.py', 'timer_guard.py')]
    if any(not path.is_file() for path in paths):
        raise RuntimeError('Registered v4 source missing')
    sources = {**external, **{str(path): sha(path) for path in paths}}
    if any(sha(path) != digest for path, digest in sources.items()):
        raise RuntimeError('Referenced source drifted before registration')
    plan = {**old,
        'status': 'PREREGISTERED_LIVE_AUTH_NOFAULT_1800',
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'runs': {'VAL7': {'duration_seconds': 1800, 'seed': 751720}},
        'source_sha256': sources,
        'reference_plan_sha256': sha(OLD),
        'live_auth_source_sha256': sha(ROOT / 'scripts/auth_protocol.py'),
        'readiness_harness': {
            'run_id': 'SF-FINAL-LIVE-AUTH-NOFAULT-1800-20261019-01',
            'real_fault_adapter_access': False,
            'marker_only': True,
            'planned_marker_offset_seconds': 600,
            'output_root': str(DATA / 'validation-runs-v7/VAL7')}}
    TARGET.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    with TARGET.open('x') as stream:
        json.dump(plan, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    TARGET.chmod(0o600)
    print(json.dumps({'status': plan['status'], 'plan_sha256': sha(TARGET),
                      'source_count': len(sources),
                      'faults_allowed': plan['faults_allowed']}))


if __name__ == '__main__':
    main()
