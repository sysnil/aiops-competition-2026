#!/usr/bin/env python3
"""Pre-register the one authorized SF-003 formal attempt, without injection."""

import argparse
import hashlib
import json
import os
import secrets
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from handshake import atomic
from run_registry import BACKEND, ROOT, load_bundle, sha
from prepare_pod_registration import build
from sf003_contract import validate_plan_fields


OPS = Path('/root/ops-competition-2026')
RUN_ID = 'SF-003-ATTEMPT-01'
RAW = Path('/data/stage5-v3/ops-competition-2026/runs') / RUN_ID
FORMAL = OPS / 'sf003-formal-v1-20261016'
IDENTITY = FORMAL / 'plans/current-identity.json'
POD_ADMISSION = FORMAL / 'design/pod-memory-admission-draft.yaml'
QUALIFICATION = OPS / 'qualification/COMP-SF01-POD-SHORT'
QUALIFICATION_PLAN = OPS / 'plans/COMP-SF01-POD-SHORT.json'
WATCHDOG = OPS / 'qualification/SF003-WATCHDOG-SELFTEST-W2-ATTEMPT01.json'
AUTHORITY = OPS / 'plans/SF-remaining-9-production-v2-draft.json'
STORAGE_POLICY = OPS / 'sf003-to-sf012-production-20261015/design/storage-policy-5gb.json'
REQUEST_START_SMOKE = Path('/data/stage5-v3/ops-competition-2026/rehearsals/SF010-REQUEST-START-QUALITY-20261016/terminal.json')
POD_NOFAULT_SMOKE = Path('/data/stage5-v3/ops-competition-2026/rehearsals/SF003-POD-NOFAULT-QUALITY-20261016/terminal.json')
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
    if (RAW.exists() or (ROOT/'plans'/RUN_ID).exists() or
            sha(AUTHORITY) != EXPECTED_AUTHORITY or
            read(STORAGE_POLICY)['authorized_absolute_free_floor_bytes'] != 5_000_000_000):
        raise RuntimeError('One-attempt identity, authority or five-GB policy changed')
    if not POD_ADMISSION.is_file():
        raise RuntimeError('Pod direct-evidence admission contract missing')
    joint_run = Path('/data/stage5-v3/ops-competition-2026/rehearsals/SF010-CONTROLLER-DRYRUN-03')
    capture = read(joint_run/'terminal.json')
    if (capture['status'] != 'NOFAULT_30MIN_CAPTURE_PASS' or
            capture['all_chunks_pass'] is not True):
        raise RuntimeError('Qualified shared thirty-minute capture chain not validated')
    request_start_smoke = read(REQUEST_START_SMOKE)
    if (request_start_smoke['status'] != 'NOFAULT_REQUEST_START_QUALITY_PASS' or
            request_start_smoke['faults_injected'] != 0 or
            request_start_smoke['matched_requests'] < 3000 or
            any(code != 0 for code in request_start_smoke['child_exit_codes'].values())):
        raise RuntimeError('Fresh request-start and immutable trace audit smoke not validated')
    pod_smoke = read(POD_NOFAULT_SMOKE)
    if (pod_smoke['status'] != 'NOFAULT_REQUEST_START_QUALITY_PASS' or
            pod_smoke['faults_injected'] != 0 or
            pod_smoke['matched_requests'] < 3000 or
            any(code != 0 for code in pod_smoke['child_exit_codes'].values())):
        raise RuntimeError('SF-003 public-only live conversion smoke not validated')
    snapshot = read(args.five_host_snapshot)
    age = time.time() - datetime.fromisoformat(snapshot['at_utc']).timestamp()
    if (snapshot['status'] != 'READ_ONLY_FIVE_GB_SNAPSHOT' or
            snapshot['one_case']['status'] != 'DYNAMIC_STORAGE_PASS' or
            not 0 <= age <= 120):
        raise RuntimeError('Fresh five-host prospective capacity gate required')
    residue = read(args.five_host_residue)
    residue_age = time.time() - datetime.fromisoformat(residue['at_utc']).timestamp()
    if residue['status'] != 'HOST_RESIDUE_PASS' or not 0 <= residue_age <= 120:
        raise RuntimeError('Fresh five-host no-fault residue gate required')
    identity = read(IDENTITY)
    age = time.time() - datetime.fromisoformat(identity['at_utc']).timestamp()
    if (identity['status'] != 'CANDIDATE_CGROUPS_INSPECTED_NOT_QUALIFIED' or
            not 0 <= age <= 300):
        raise RuntimeError('Fresh current Pod/PID/Container inspection required')
    proof = read(WATCHDOG)
    age = time.time() - datetime.fromisoformat(proof['completed_utc']).timestamp()
    if (proof['status'] != 'COMPETITION_WATCHDOG_SELFTEST_PASS' or
            proof['adapter_sha256'] != sha(OPS/'scripts/competition_adapter.py') or
            not 0 <= age <= 3600):
        raise RuntimeError('Fresh independent Worker2 Watchdog selftest required')
    decision = read(QUALIFICATION/'decision.json')
    if decision['qualified'] is not True or decision['blockers']:
        raise RuntimeError('Historical real PodMemoryStress60 qualification invalid')
    os.environ['SF003_RUN'] = str(RAW)
    os.environ['KUBECONFIG'] = '/etc/kubernetes/admin.conf'
    sys.path.insert(0, str(FORMAL/'scripts'))
    from capture_controller import source_lock
    from sf003_safety import safety
    from pilot_common import telemetry_state, OFFSETS
    expected = [{key: row[key] for key in ('service','name','uid','node',
                 'container_id','restart_count')} for row in identity['targets']]
    environment, collector = safety(expected), telemetry_state()
    if collector['refused'] != {'grpc': 0.0, 'http': 0.0}:
        raise RuntimeError('Collector receiver refusal baseline not clean')
    candidate_fields = {'id': RUN_ID, 'raw_root': str(RAW),
        'control_root': str(RAW/'control'),
        'collector_uid': collector['uid'],
        'targets_and_siblings': expected}
    validate_plan_fields(candidate_fields)
    if args.validate_only:
        inspection = dict(identity, identity_sha256=sha(IDENTITY))
        registration = build(read(QUALIFICATION_PLAN), inspection)
        locked = source_lock()
        print(json.dumps({'status': 'SF003_FORMAL_INPUTS_VALIDATED_NO_CASE_CREATED',
                          'targets': len(registration['targets']),
                          'collector_uid': collector['uid'],
                          'capture_sources': len(locked),
                          'five_host_snapshot_sha256': sha(args.five_host_snapshot),
                          'five_host_residue_sha256': sha(args.five_host_residue),
                          'watchdog_proof_sha256': sha(WATCHDOG)}))
        return
    folder = ROOT/'plans'/RUN_ID
    folder.mkdir(parents=True, mode=0o700)
    health_path = folder/'health.json'
    health = {'status': 'SF003_FORMAL_ENVIRONMENT_PASS', 'at_utc': utc(time.time()),
              'current_environment_blockers': [], 'environment': environment,
              'collector_uid': collector['uid'],
              'collector_refused': collector['refused'],
              'five_host_snapshot_sha256': sha(args.five_host_snapshot),
              'five_host_residue_sha256': sha(args.five_host_residue),
              'identity_sha256': sha(IDENTITY)}
    atomic(health_path, health, True)
    source = read(QUALIFICATION_PLAN)
    identity['identity_sha256'] = sha(IDENTITY)
    registration = build(source, identity)
    registration_path = folder/'pod-registration.json'
    atomic(registration_path, registration, True)
    t0 = time.time() + 300
    lock = source_lock()
    windows = {f'chunk-{index:02d}':
        {'start_utc': utc(t0 + OFFSETS[index]),
         'end_utc': utc(t0 + OFFSETS[index+1])}
        for index in range(len(OFFSETS)-1)}
    RAW.mkdir(parents=True, mode=0o700)
    schedule = {'id': RUN_ID, 'formal_case': True,
                'start_utc': utc(t0), 'end_utc': utc(t0+1800),
                'duration_seconds': 1800, 'seed': args.seed,
                'windows': windows, 'trace_source': 'JAEGER_OPENSEARCH_STORAGE',
                'capture_offsets_seconds': OFFSETS, 'source_sha256': lock,
                'load': {'profile': 'MEDIUM', 'sessions_per_second': 1.4,
                         'image_per_second': 1, 'cart_per_second': 2}}
    schedule_path = RAW/'schedule.json'
    atomic(schedule_path, schedule, True)
    action_id = RUN_ID+'-POD'
    action = {'run_id': RUN_ID, 'action_id': action_id,
        'fault_event_id': 'SF-003-F01', 'fault_type': 'PodMemoryStress',
        'mechanism_id': 'POD', 'worker': 'k8s-worker2',
        'targets': registration['targets'], 'duration_seconds': 60,
        'offset_seconds': 600, 'planned_utc': utc(t0+600),
        'recovery_offset_seconds': 840, 'onset_tolerance_seconds': 2,
        'unit': 'ops-sf003-'+action_id.lower(), 'backend': BACKEND,
        'execute': 'RUN_PINNED_QUALIFIED_ADAPTER',
        'revoke': 'REVOKE_STOP_AND_PINNED_CLEANUP',
        'qualified_registration': str(registration_path),
        'qualified_registration_sha256': sha(registration_path),
        'watchdog_proof_file': str(WATCHDOG),
        'watchdog_proof_sha256': sha(WATCHDOG),
        'injection_output': str(OPS/'qualification/private-attempts'/action_id),
        'runtime': str(ROOT/'worker-runtime'/action_id),
        'registration_file': str(folder/'run-plan.json')}
    if Path(action['runtime']).exists() or Path(action['injection_output']).exists():
        raise RuntimeError('Formal Worker runtime or private injection record already exists')
    sources = {str(path): sha(path) for directory in (ROOT/'scripts', FORMAL/'scripts')
               for path in directory.glob('*.py')}
    sources.update(lock)
    sources['/root/cross-layer-observability-control-rehearsal/scripts/workload_generator_rehearsal.py'] = sha(Path('/root/cross-layer-observability-control-rehearsal/scripts/workload_generator_rehearsal.py'))
    sources['/root/stage5-v3-autonomous-preparation/scripts/fixed_control_load_c3.py'] = sha(Path('/root/stage5-v3-autonomous-preparation/scripts/fixed_control_load_c3.py'))
    sources[str(OPS/'scripts/competition_adapter.py')] = sha(OPS/'scripts/competition_adapter.py')
    sources[str(OPS/'plans/competition-fault-admission.yaml')] = sha(OPS/'plans/competition-fault-admission.yaml')
    sources[str(POD_ADMISSION)] = sha(POD_ADMISSION)
    plan = {'id': RUN_ID, 'backend': BACKEND, 'formal_case': True,
        'authorization_scope': 'ONE_SF003_POD_MEMORY60_ONLY',
        'duration_seconds': 1800, 'start_utc': utc(t0), 'seed': args.seed,
        'profile': 'MEDIUM', 'raw_root': str(RAW),
        'control_root': str(RAW/'control'),
        'five_host_heartbeat_path': str(RAW/'control/five-host-storage.json'),
        'five_host_monitor_sha256': sources[str(ROOT/'scripts/five_host_heartbeat.py')],
        'capture_schedule_sha256': sha(schedule_path),
        'capture_controller_unit': 'ops-sf003-formal-capture01-20261015',
        'controller_unit': 'ops-sf003-formal-controller01-20261015',
        'observer_unit': 'ops-sf003-formal-observer01-20261015',
        'health_file': str(health_path), 'health_sha256': sha(health_path),
        'collector_uid': collector['uid'], 'identity_file': str(IDENTITY),
        'identity_sha256': sha(IDENTITY),
        'targets_and_siblings': expected,
        'run_token': secrets.token_hex(24), 'actions': [action],
        'source_sha256': sources,
        'source_plan_sha256': EXPECTED_AUTHORITY,
        'storage_policy_sha256': sha(STORAGE_POLICY),
        'five_host_residue_sha256': sha(args.five_host_residue),
        'shared_capture_rehearsal_sha256': sha(joint_run/'terminal.json'),
        'qualified_pod_decision_sha256': sha(QUALIFICATION/'decision.json'),
        'pod_nofault_smoke_sha256': sha(POD_NOFAULT_SMOKE),
        'pod_admission_sha256': sha(POD_ADMISSION)}
    plan['request_start_smoke_terminal_sha256'] = sha(REQUEST_START_SMOKE)
    plan_path = folder/'run-plan.json'
    validate_plan_fields(plan)
    atomic(plan_path, plan, True)
    lock_path = folder/'schedule-lock.json'
    atomic(lock_path, {'run_id': RUN_ID, 'plan_sha256': sha(plan_path),
                       'actions': [action]}, True)
    manifest_path = folder/'run-manifest.json'
    manifest = {'schema': 'EXPLICIT_RUN_REGISTRY_V1', 'run_id': RUN_ID,
        'backend': BACKEND, 'plan_path': str(plan_path),
        'plan_sha256': sha(plan_path), 'schedule_lock_path': str(lock_path),
        'schedule_lock_sha256': sha(lock_path), 'actions': [action],
        'controller_unit': plan['controller_unit'],
        'observer_unit': plan['observer_unit']}
    atomic(manifest_path, manifest, True)
    load_bundle(manifest_path, sha(manifest_path))
    print(json.dumps({'status': 'SF003_FORMAL_ATTEMPT_PREREGISTERED_NO_FAULT_YET',
                      'manifest': str(manifest_path),
                      'manifest_sha256': sha(manifest_path),
                      'start_utc': plan['start_utc']}))


if __name__ == '__main__':
    main()
