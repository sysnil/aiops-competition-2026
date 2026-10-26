#!/usr/bin/env python3
"""Pre-register an HTTP-only, source-pinned SF-004 formal run."""

import argparse
import json
import os
import secrets
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from handshake import atomic
from run_registry import BACKEND, ROOT, load_bundle, sha
from sf004_contract import validate_plan_fields


OPS = Path('/root/ops-competition-2026')
RUN_ID = 'SF-004-ATTEMPT-01'
RAW = Path('/data/stage5-v3/ops-competition-2026/runs') / RUN_ID
FORMAL = OPS / 'sf004-formal-v1-20261016'
IDENTITY = FORMAL / 'plans/current-identity.json'
HTTP_ADMISSION = FORMAL / 'design/http-home-admission-draft.yaml'
AUTHORITY = OPS / 'plans/SF-remaining-9-production-v2-draft.json'
STORAGE_POLICY = OPS / 'sf003-to-sf012-production-20261015/design/storage-policy-5gb.json'
SHARED_CAPTURE = Path('/data/stage5-v3/ops-competition-2026/rehearsals/SF010-CONTROLLER-DRYRUN-03/terminal.json')
REQUEST_SMOKE = Path('/data/stage5-v3/ops-competition-2026/rehearsals/SF010-REQUEST-START-QUALITY-20261016/terminal.json')
HOME_NOFAULT_SMOKE = Path('/data/stage5-v3/ops-competition-2026/rehearsals/SF004-HOME-NOFAULT-QUALITY-20261016/terminal.json')
REAL_HTTP_PROOF = Path('/data/stage5-v3/ops-competition-2026/runs/SF-010-ATTEMPT-07/control/fault-controller-terminal.json')
EXPECTED_AUTHORITY = 'fde6207a6953e85d97cb8a837628abb52913d6b191cfa0486d40d6e03dec88ab'


def utc(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--five-host-snapshot', type=Path, required=True)
    parser.add_argument('--five-host-residue', type=Path, required=True)
    parser.add_argument('--seed', type=int, required=True)
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    if (RAW.exists() or (ROOT / 'plans' / RUN_ID).exists() or
            sha(AUTHORITY) != EXPECTED_AUTHORITY or
            read(STORAGE_POLICY)['authorized_absolute_free_floor_bytes'] != 5_000_000_000):
        raise RuntimeError('Attempt identity, authority or five-GB policy changed')
    if not HTTP_ADMISSION.is_file():
        raise RuntimeError('HTTP public direct-evidence admission contract missing')
    capture = read(SHARED_CAPTURE)
    smoke = read(REQUEST_SMOKE)
    proof = read(REAL_HTTP_PROOF)
    if (capture['status'] != 'NOFAULT_30MIN_CAPTURE_PASS' or
            capture['all_chunks_pass'] is not True or
            smoke['status'] != 'NOFAULT_REQUEST_START_QUALITY_PASS' or
            smoke['faults_injected'] != 0 or
            proof['status'] != 'SF010_FAULT_SEQUENCE_COMPLETE_PENDING_CAPTURE' or
            proof['stages']['http_delay']['delayed_home_requests'] < 60):
        raise RuntimeError('Current source route and full capture qualification absent')
    home_smoke = read(HOME_NOFAULT_SMOKE)
    if (home_smoke['status'] != 'NOFAULT_REQUEST_START_QUALITY_PASS' or
            home_smoke['faults_injected'] != 0 or
            home_smoke['matched_requests'] < 3000 or
            any(code != 0 for code in home_smoke['child_exit_codes'].values())):
        raise RuntimeError('SF-004 live public conversion smoke failed')
    snapshot = read(args.five_host_snapshot)
    residue = read(args.five_host_residue)
    if (snapshot['status'] != 'READ_ONLY_FIVE_GB_SNAPSHOT' or
            snapshot['one_case']['status'] != 'DYNAMIC_STORAGE_PASS' or
            not 0 <= time.time() - datetime.fromisoformat(snapshot['at_utc']).timestamp() <= 120 or
            residue['status'] != 'HOST_RESIDUE_PASS' or
            not 0 <= time.time() - datetime.fromisoformat(residue['at_utc']).timestamp() <= 120):
        raise RuntimeError('Fresh five-host safety and storage snapshots required')
    identity = read(IDENTITY)
    if (identity['status'] != 'CANDIDATE_CGROUPS_INSPECTED_NOT_QUALIFIED' or
            not 0 <= time.time() - datetime.fromisoformat(identity['at_utc']).timestamp() <= 300):
        raise RuntimeError('Fresh public candidate Pod metadata required')
    os.environ['SF004_RUN'] = str(RAW)
    os.environ['KUBECONFIG'] = '/etc/kubernetes/admin.conf'
    sys.path.insert(0, str(FORMAL / 'scripts'))
    from capture_controller import source_lock
    from sf004_safety import safety
    from pilot_common import telemetry_state, OFFSETS
    expected = [{key: row[key] for key in ('service', 'name', 'uid', 'node',
                 'container_id', 'restart_count')} for row in identity['targets']]
    validate_plan_fields({'id': RUN_ID, 'raw_root': str(RAW),
                          'control_root': str(RAW / 'control'),
                          'collector_uid': telemetry_state()['uid'],
                          'targets_and_siblings': expected})
    environment, collector = safety(expected), telemetry_state()
    if collector['refused'] != {'grpc': 0.0, 'http': 0.0}:
        raise RuntimeError('Collector receiver refusal baseline not clean')
    locked = source_lock()
    if args.validate_only:
        print(json.dumps({'status': 'SF004_FORMAL_INPUTS_VALIDATED_NO_CASE_CREATED',
                          'candidates': len(expected), 'collector_uid': collector['uid'],
                          'capture_sources': len(locked),
                          'five_host_snapshot_sha256': sha(args.five_host_snapshot),
                          'five_host_residue_sha256': sha(args.five_host_residue)}))
        return
    folder = ROOT / 'plans' / RUN_ID
    folder.mkdir(parents=True, mode=0o700)
    health_path = folder / 'health.json'
    health = {'status': 'SF004_FORMAL_ENVIRONMENT_PASS', 'at_utc': utc(time.time()),
              'current_environment_blockers': [], 'environment': environment,
              'collector_uid': collector['uid'], 'collector_refused': collector['refused'],
              'five_host_snapshot_sha256': sha(args.five_host_snapshot),
              'five_host_residue_sha256': sha(args.five_host_residue),
              'identity_sha256': sha(IDENTITY)}
    atomic(health_path, health, True)
    t0 = time.time() + 300
    windows = {f'chunk-{index:02d}': {'start_utc': utc(t0 + OFFSETS[index]),
               'end_utc': utc(t0 + OFFSETS[index + 1])} for index in range(6)}
    RAW.mkdir(parents=True, mode=0o700)
    schedule = {'id': RUN_ID, 'formal_case': True,
                'start_utc': utc(t0), 'end_utc': utc(t0 + 1800),
                'duration_seconds': 1800, 'seed': args.seed,
                'windows': windows, 'trace_source': 'JAEGER_OPENSEARCH_STORAGE',
                'capture_offsets_seconds': OFFSETS, 'source_sha256': locked,
                'load': {'profile': 'MEDIUM', 'sessions_per_second': 1.4,
                         'image_per_second': 1, 'cart_per_second': 2}}
    schedule_path = RAW / 'schedule.json'
    atomic(schedule_path, schedule, True)
    sources = {str(path): sha(path) for directory in (ROOT / 'scripts', FORMAL / 'scripts')
               for path in directory.glob('*.py')}
    sources.update(locked)
    sources[str(HTTP_ADMISSION)] = sha(HTTP_ADMISSION)
    for path in ('/root/cross-layer-observability-control-rehearsal/scripts/workload_generator_rehearsal.py',
                 '/root/stage5-v3-autonomous-preparation/scripts/fixed_control_load_c3.py'):
        sources[path] = sha(path)
    plan = {'id': RUN_ID, 'backend': BACKEND, 'formal_case': True,
        'authorization_scope': 'ONE_SF004_HTTP_HOME_DELAY60_ONLY',
        'no_worker_timer': True,
        'duration_seconds': 1800, 'start_utc': utc(t0), 'seed': args.seed,
        'profile': 'MEDIUM', 'raw_root': str(RAW),
        'control_root': str(RAW / 'control'),
        'five_host_heartbeat_path': str(RAW / 'control/five-host-storage.json'),
        'five_host_monitor_sha256': sources[str(ROOT / 'scripts/five_host_heartbeat.py')],
        'capture_schedule_sha256': sha(schedule_path),
        'capture_controller_unit': 'ops-sf004-formal-capture01-20261016',
        'controller_unit': 'ops-sf004-formal-controller01-20261016',
        'observer_unit': 'ops-sf004-formal-observer01-20261016',
        'health_file': str(health_path), 'health_sha256': sha(health_path),
        'collector_uid': collector['uid'], 'identity_file': str(IDENTITY),
        'identity_sha256': sha(IDENTITY), 'targets_and_siblings': expected,
        'run_token': secrets.token_hex(24), 'actions': [],
        'request_delay': {'operation': 'home', 'offset_seconds': 900,
                          'duration_seconds': 60, 'delay_ms': 250,
                          'lease_path': str(RAW / 'control/http-reqdelay-lease.json')},
        'source_sha256': sources, 'source_plan_sha256': EXPECTED_AUTHORITY,
        'storage_policy_sha256': sha(STORAGE_POLICY),
        'five_host_residue_sha256': sha(args.five_host_residue),
        'shared_capture_sha256': sha(SHARED_CAPTURE),
        'request_start_smoke_sha256': sha(REQUEST_SMOKE),
        'home_nofault_smoke_sha256': sha(HOME_NOFAULT_SMOKE),
        'http_admission_sha256': sha(HTTP_ADMISSION),
        'real_http_qualification_sha256': sha(REAL_HTTP_PROOF)}
    plan_path = folder / 'run-plan.json'
    validate_plan_fields(plan)
    atomic(plan_path, plan, True)
    lock_path = folder / 'schedule-lock.json'
    atomic(lock_path, {'run_id': RUN_ID, 'plan_sha256': sha(plan_path),
                       'actions': []}, True)
    manifest_path = folder / 'run-manifest.json'
    manifest = {'schema': 'EXPLICIT_RUN_REGISTRY_V1', 'run_id': RUN_ID,
        'backend': BACKEND, 'plan_path': str(plan_path),
        'plan_sha256': sha(plan_path), 'schedule_lock_path': str(lock_path),
        'schedule_lock_sha256': sha(lock_path), 'actions': [],
        'no_worker_timer': True,
        'controller_unit': plan['controller_unit'],
        'observer_unit': plan['observer_unit']}
    atomic(manifest_path, manifest, True)
    load_bundle(manifest_path, sha(manifest_path))
    print(json.dumps({'status': 'SF004_FORMAL_ATTEMPT_PREREGISTERED_NO_FAULT_YET',
                      'manifest': str(manifest_path),
                      'manifest_sha256': sha(manifest_path),
                      'start_utc': plan['start_utc']}))


if __name__ == '__main__':
    main()
