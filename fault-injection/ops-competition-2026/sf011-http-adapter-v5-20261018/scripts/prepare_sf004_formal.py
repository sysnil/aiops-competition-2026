#!/usr/bin/env python3
"""Pre-register an HTTP-only, source-pinned SF-011 formal run."""

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
RUN_ID = 'SF-011-ATTEMPT-05'
RAW = Path('/data/stage5-v3/ops-competition-2026/runs') / RUN_ID
FORMAL = OPS / 'sf011-http-formal-v5-20261018'
IDENTITY = FORMAL / 'plans/current-identity.json'
HTTP_ADMISSION = FORMAL / 'design/http-home-admission-draft.yaml'
AUTHORITY = OPS / 'low-stable-v4-20261017/design/sf011-attempt05-retry-draft.json'
FREEZE = OPS / 'low-stable-v4-20261017/design/sf011-attempt05-freeze-manifest.json'
HEARTBEAT_QUALIFICATION = OPS / 'low-stable-v4-20261017/results/sf011-att05-heartbeat-qualification-terminal.json'
HEARTBEAT_CONTRACT = OPS / 'low-stable-v4-20261017/design/five-host-heartbeat-qualification-sf011-att05.json'
CALIBRATION = OPS / 'low-stable-v4-20261017/results/low-stable-control-calibration.json'
STORAGE_POLICY = OPS / 'sf003-to-sf012-production-20261015/design/storage-policy-5gb.json'
NOFAULT_VALIDATIONS = (
    Path('/data/stage5-v3/ops-competition-2026/low-stable-v4-20261017/validation-runs-v4/VAL4/assessment.json'),
    Path('/data/stage5-v3/ops-competition-2026/low-stable-v4-20261017/validation-runs-v6/VAL6/assessment.json'),
)
REAL_HTTP_PROOF = Path('/data/stage5-v3/ops-competition-2026/runs/SF-010-ATTEMPT-07/control/fault-controller-terminal.json')
EXPECTED_AUTHORITY = '815a7123f690300852812d55b5b9a37031d9b17f41afa86ca6349db52c7884f3'


def utc(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text())


def main():
    import yaml
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
    frozen = read(FREEZE)
    heartbeat_qualification = read(HEARTBEAT_QUALIFICATION)
    if (frozen['status'] != 'SF011_ATTEMPT05_INPUTS_FROZEN' or
            frozen['run_id'] != RUN_ID or
            any(sha(path) != digest for path, digest in frozen['source_sha256'].items()) or
            frozen['public_safe_no_fault_assessment_sha256'] != sha(NOFAULT_VALIDATIONS[1]) or
            frozen['heartbeat_qualification_sha256'] != sha(HEARTBEAT_QUALIFICATION) or
            heartbeat_qualification['status'] != 'FIVE_HOST_HEARTBEAT_1800S_PASS' or
            heartbeat_qualification['completed_samples'] != 121 or
            heartbeat_qualification['faults_injected'] != 0 or
            heartbeat_qualification['contract_sha256'] != sha(HEARTBEAT_CONTRACT) or
            not 0 <= time.time() - datetime.fromisoformat(
                heartbeat_qualification['completed_utc']).timestamp() <= 3600):
        raise RuntimeError('Pre-attempt public-safe source freeze is invalid')
    admission = yaml.safe_load(HTTP_ADMISSION.read_text())
    calibration = read(CALIBRATION)
    if (admission['case_id'] != 'SF-011' or
            admission['normal_controls']['image_absolute_p95_tolerance_ms'] != 2.0 or
            admission['normal_controls']['independent_no_fault_calibration_sha256'] != sha(CALIBRATION) or
            calibration['status'] != 'LOW_STABLE_INDEPENDENT_NOFAULT_CONTROL_AUDIT' or
            set(calibration['runs']) != {'VAL5', 'VAL6'} or
            calibration['formal_results_used'] is not False):
        raise RuntimeError('Independent Image control calibration is absent or changed')
    proof = read(REAL_HTTP_PROOF)
    if (proof['status'] != 'SF010_FAULT_SEQUENCE_COMPLETE_PENDING_CAPTURE' or
            proof['stages']['http_delay']['delayed_home_requests'] < 60):
        raise RuntimeError('Qualified HTTP route and effect proof absent')
    for assessment_path in NOFAULT_VALIDATIONS:
        validation = read(assessment_path)
        execution = read(assessment_path.parent / 'execution-result.json')
        if (validation['status'] != 'NO_FAULT_VALIDATION_PASS' or
                validation['faults_injected'] != 0 or
                validation['config_sha256'] !=
                'ad42e84b2a1d25b6b9b716f2a119f8d3ab6c50a975b860f3a8eed939618dd05f' or
                sum(validation['request_counts'].values()) < 3000 or
                any(validation['missing_proxy_trace_ids'].values()) or
                execution['status'] != 'VALIDATION_EXECUTION_COMPLETE_PENDING_ASSESSMENT' or
                any(code != 0 for code in execution['exit_codes'].values())):
            raise RuntimeError('Two new 30-minute request and trace validations required')
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
    os.environ['SF011_RUN'] = str(RAW)
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
        print(json.dumps({'status': 'SF011_FORMAL_INPUTS_VALIDATED_NO_CASE_CREATED',
                          'candidates': len(expected), 'collector_uid': collector['uid'],
                          'capture_sources': len(locked),
                          'five_host_snapshot_sha256': sha(args.five_host_snapshot),
                          'five_host_residue_sha256': sha(args.five_host_residue)}))
        return
    folder = ROOT / 'plans' / RUN_ID
    folder.mkdir(parents=True, mode=0o700)
    health_path = folder / 'health.json'
    health = {'status': 'SF011_FORMAL_ENVIRONMENT_PASS', 'at_utc': utc(time.time()),
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
                'load': {'profile': 'LOW', 'policy_name': 'LOW_STABLE',
                         'sessions_per_second': 0.7, 'image_per_second': 1,
                         'cart_per_second': 1, 'home_per_second': 1,
                         'recommendations_per_second': 1}}
    schedule_path = RAW / 'schedule.json'
    atomic(schedule_path, schedule, True)
    sources = {str(path): sha(path) for directory in (ROOT / 'scripts', FORMAL / 'scripts')
               for path in directory.glob('*.py')}
    sources.update(locked)
    sources[str(HTTP_ADMISSION)] = sha(HTTP_ADMISSION)
    sources[str(CALIBRATION)] = sha(CALIBRATION)
    sources[str(HEARTBEAT_CONTRACT)] = sha(HEARTBEAT_CONTRACT)
    for path in ('/root/cross-layer-observability-control-rehearsal/scripts/workload_generator_rehearsal.py',
                 '/root/ops-competition-2026/low-stable-v4-20261017/formal-v2/fixed_business_streams_faultable.py'):
        sources[path] = sha(path)
    plan = {'id': RUN_ID, 'backend': BACKEND, 'formal_case': True,
        'authorization_scope': 'ONE_SF011_HTTP_HOME_DELAY60_ONLY',
        'no_worker_timer': True,
        'duration_seconds': 1800, 'start_utc': utc(t0), 'seed': args.seed,
        'profile': 'LOW', 'raw_root': str(RAW),
        'control_root': str(RAW / 'control'),
        'five_host_heartbeat_path': str(RAW / 'control/five-host-storage.json'),
        'five_host_monitor_sha256': sources[str(ROOT / 'scripts/five_host_heartbeat.py')],
        'capture_schedule_sha256': sha(schedule_path),
        'capture_controller_unit': 'ops-sf011-formal-capture05-20261017',
        'controller_unit': 'ops-sf011-formal-controller05-20261017',
        'observer_unit': 'ops-sf011-formal-observer05-20261017',
        'health_file': str(health_path), 'health_sha256': sha(health_path),
        'collector_uid': collector['uid'], 'identity_file': str(IDENTITY),
        'identity_sha256': sha(IDENTITY), 'targets_and_siblings': expected,
        'run_token': secrets.token_hex(24), 'actions': [],
        'request_delay': {'operation': 'home', 'offset_seconds': 900,
                          'duration_seconds': 60, 'delay_ms': 250,
                          'lease_path': str(RAW / 'control/http-reqdelay-lease.json')},
        'source_sha256': sources, 'source_plan_sha256': EXPECTED_AUTHORITY,
        'storage_policy_sha256': sha(STORAGE_POLICY),
        'input_freeze_manifest_sha256': sha(FREEZE),
        'heartbeat_qualification_sha256': sha(HEARTBEAT_QUALIFICATION),
        'five_host_residue_sha256': sha(args.five_host_residue),
        'no_fault_validation_sha256': {str(path): sha(path) for path in NOFAULT_VALIDATIONS},
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
    print(json.dumps({'status': 'SF011_FORMAL_ATTEMPT_PREREGISTERED_NO_FAULT_YET',
                      'manifest': str(manifest_path),
                      'manifest_sha256': sha(manifest_path),
                      'start_utc': plan['start_utc']}))


if __name__ == '__main__':
    main()
