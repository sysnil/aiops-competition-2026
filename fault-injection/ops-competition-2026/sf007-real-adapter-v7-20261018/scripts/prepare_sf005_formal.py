#!/usr/bin/env python3
"""Pre-register the one authorized SF-007 formal attempt, without injection."""

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
from prepare_service_registration import build
from sf005_contract import validate_plan_fields


OPS = Path('/root/ops-competition-2026')
RUN_ID = 'SF-007-ATTEMPT-07'
RAW = Path('/data/stage5-v3/ops-competition-2026/runs') / RUN_ID
FORMAL = OPS / 'sf007-formal-v7-20261018'
IDENTITY = FORMAL / 'plans/current-identity.json'
QUALIFICATION = OPS / 'qualification/COMP-SF01-SERVICE-SHORT'
QUALIFICATION_PLAN = OPS / 'plans/COMP-SF01-SERVICE-SHORT.json'
WATCHDOG = OPS / 'qualification/SF007-WATCHDOG-SELFTEST-W3-ATTEMPT07.json'
AUTHORITY = OPS / 'plans/SF-12-cases-30min-production-plan.json'
STORAGE_POLICY = OPS / 'sf003-to-sf012-production-20261015/design/storage-policy-5gb.json'
NOFAULT_VALIDATIONS = (
    Path('/data/stage5-v3/ops-competition-2026/low-stable-v4-20261017/validation-runs-v4/VAL4/assessment.json'),
    Path('/data/stage5-v3/ops-competition-2026/low-stable-v4-20261017/validation-runs-v6/VAL6/assessment.json'),
    Path('/data/stage5-v3/ops-competition-2026/low-stable-v4-20261017/validation-runs-sf007-v2/VAL7/assessment.json'),
)
NEW_STREAM_CONTRACT = OPS / 'low-stable-v4-20261017/design/full-chain-sf007-v2-validation-contract.json'
STORAGE_V2_CONTRACT = OPS / 'low-stable-v4-20261017/storage-v2/qualification-contract.json'
STORAGE_V2_TERMINAL = OPS / 'low-stable-v4-20261017/storage-v2/results/STORAGE-V2-NOFAULT-01/terminal.json'
CASE_BUDGET = OPS / 'low-stable-v4-20261017/storage-v2/case-budgets/sf007-att07.json'
QUALIFIED_BUDGET = OPS / 'low-stable-v4-20261017/storage-v2/case-budgets/sf007-att06.json'
CASE_BUDGET_READER = OPS / 'low-stable-v4-20261017/storage-v2/dynamic_storage_case_v12.py'
CASE_BUDGET_NOFAULT = OPS / 'low-stable-v4-20261017/storage-v2/results/SF007-ATT06-BUDGET-NOFAULT-01/terminal.json'
EXPECTED_AUTHORITY = '762ff940a8198e0ef4470614f410edaddab3bc73a277f8a36f04015cc34943f4'
DESIGN = OPS / 'low-stable-v4-20261017/design/remaining-cases-draft.json'
EXPECTED_DESIGN = '64567bac143fa955cba501abf591049adba42ec2c15499ab7629e7e9b72e08ef'
SERVICE_ADMISSION = FORMAL / 'design/service-admission-draft.yaml'
CALIBRATION = OPS / 'low-stable-v4-20261017/results/low-stable-control-calibration.json'


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
    admission = yaml.safe_load(SERVICE_ADMISSION.read_text())
    calibration = read(CALIBRATION)
    if (admission['schema'] != 'SF007_SERVICE_PAUSE30_ADMISSION_V2' or
            admission['no_fault_calibration_sha256'] != sha(CALIBRATION) or
            admission['public_root_evidence']['maximum_normalized_server_completion_ratio'] != .20 or
            admission['normal_controls']['image_absolute_p95_tolerance_ms'] != 2.0 or
            calibration['status'] != 'LOW_STABLE_INDEPENDENT_NOFAULT_CONTROL_AUDIT' or
            set(calibration['runs']) != {'VAL5', 'VAL6'} or
            calibration['formal_results_used'] is not False):
        raise RuntimeError('Prospective SF-007 direct-evidence contract changed')
    if (RAW.exists() or (ROOT/'plans'/RUN_ID).exists() or
            sha(AUTHORITY) != EXPECTED_AUTHORITY or sha(DESIGN) != EXPECTED_DESIGN or
            read(STORAGE_POLICY)['authorized_absolute_free_floor_bytes'] != 5_000_000_000):
        raise RuntimeError('One-attempt identity, authority or five-GB policy changed')
    joint_run = Path('/data/stage5-v3/ops-competition-2026/rehearsals/SF010-CONTROLLER-DRYRUN-03')
    capture = read(joint_run/'terminal.json')
    markers = read(joint_run/'marker-control/terminal.json')
    if (capture['status'] != 'NOFAULT_30MIN_CAPTURE_PASS' or
            capture['all_chunks_pass'] is not True or
            markers['status'] != 'SF010_TWO_EVENT_MARKER_SCHEDULER_PASS' or
            markers['business_faults_injected'] != 0 or len(markers['events']) != 2):
        raise RuntimeError('Joint no-fault capture and two-stage Marker rehearsal not validated')
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
    stream_contract = read(NEW_STREAM_CONTRACT)
    stream_assessment = read(NOFAULT_VALIDATIONS[-1])
    stream_schedule = read(NOFAULT_VALIDATIONS[-1].parent / 'schedule.json')
    if (stream_contract['workload']['control_concurrency_per_stream']['recommendations'] != 12 or
            stream_contract['workload']['control_timeout_seconds'] != 8 or
            stream_contract['workload']['control_streams'][-1]['rate_per_second'] != 1.0 or
            stream_schedule['plan_sha256'] != sha(NEW_STREAM_CONTRACT) or
            stream_assessment['request_counts'].get('recommendations') != 1800 or
            stream_assessment['request_successes'].get('recommendations') != 1800 or
            not all(stream_assessment['checks'].values())):
        raise RuntimeError('New Recommendation concurrency requires independent 1800-second validation')
    storage_contract = read(STORAGE_V2_CONTRACT)
    storage_qualification = read(STORAGE_V2_TERMINAL)
    if (storage_contract['status'] != 'PREREGISTERED_STORAGE_V2_NOFAULT_1800S' or
            storage_qualification['status'] != 'STORAGE_V2_NOFAULT_1800_PASS' or
            storage_qualification['contract_sha256'] != sha(STORAGE_V2_CONTRACT) or
            storage_qualification['samples'] < 120 or
            storage_qualification['faults_injected'] != 0 or
            storage_qualification['formal_cases_created'] != 0):
        raise RuntimeError('Prospective storage gate lacks full no-fault qualification')
    case_budget_nofault = read(CASE_BUDGET_NOFAULT)
    if (case_budget_nofault['status'] != 'SF007_ATT06_BUDGET_NOFAULT_1800_PASS' or
            case_budget_nofault['case_budget_sha256'] != sha(QUALIFIED_BUDGET) or
            {key: value for key, value in read(CASE_BUDGET).items()
             if key not in ('run_id', 'background_reservation_basis')} !=
            {key: value for key, value in read(QUALIFIED_BUDGET).items()
             if key not in ('run_id', 'background_reservation_basis')} or
            case_budget_nofault['samples'] < 120 or
            case_budget_nofault['faults_injected'] != 0 or
            case_budget_nofault['formal_cases_created'] != 0):
        raise RuntimeError('Worker1 archive budget lacks independent no-fault qualification')
    snapshot = read(args.five_host_snapshot)
    age = time.time() - datetime.fromisoformat(snapshot['at_utc']).timestamp()
    if (snapshot['status'] != 'READ_ONLY_FIVE_GB_SNAPSHOT' or
            snapshot.get('run_id') != RUN_ID or
            snapshot.get('archive_target') != 'worker1' or
            snapshot.get('commitment_contract_sha256') != sha(CASE_BUDGET) or
            snapshot.get('preflight_source_sha256') != sha(CASE_BUDGET_READER) or
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
        raise RuntimeError('Fresh independent Worker3 Watchdog selftest required')
    decision = read(QUALIFICATION/'decision.json')
    if decision['qualified'] is not True or decision['blockers']:
        raise RuntimeError('Historical real ServicePause30 qualification invalid')
    os.environ['SF007_RUN'] = str(RAW)
    os.environ['KUBECONFIG'] = '/etc/kubernetes/admin.conf'
    sys.path.insert(0, str(FORMAL/'scripts'))
    from capture_controller import source_lock
    from sf005_safety import safety
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
        print(json.dumps({'status': 'SF007_FORMAL_INPUTS_VALIDATED_NO_CASE_CREATED',
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
    health = {'status': 'SF007_FORMAL_ENVIRONMENT_PASS', 'at_utc': utc(time.time()),
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
    registration_path = folder/'service-registration.json'
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
                'load': {'profile': 'LOW', 'policy_name': 'LOW_STABLE',
                         'sessions_per_second': 0.7, 'image_per_second': 1,
                         'cart_per_second': 1, 'home_per_second': 1,
                         'recommendations_per_second': 1}}
    schedule_path = RAW/'schedule.json'
    atomic(schedule_path, schedule, True)
    action_id = RUN_ID+'-SERVICE'
    action = {'run_id': RUN_ID, 'action_id': action_id,
        'fault_event_id': 'SF-007-F01', 'fault_type': 'ServicePause',
        'mechanism_id': 'SERVICE', 'worker': 'k8s-worker3',
        'targets': registration['targets'], 'duration_seconds': 30,
        'offset_seconds': 600, 'planned_utc': utc(t0+600),
        'recovery_offset_seconds': 810, 'onset_tolerance_seconds': 2,
        'unit': 'ops-sf007-'+action_id.lower(), 'backend': BACKEND,
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
    plain_stream = OPS/'low-stable-v4-20261017/formal-v2/fixed_business_streams_plain.py'
    sources[str(plain_stream)] = sha(plain_stream)
    sources[str(OPS/'scripts/competition_adapter.py')] = sha(OPS/'scripts/competition_adapter.py')
    storage_gate = OPS/'low-stable-v4-20261017/storage-v2/storage_forecast_gate.py'
    sources[str(storage_gate)] = sha(storage_gate)
    storage_validator = OPS/'low-stable-v4-20261017/storage-v2/validate_heartbeat.py'
    sources[str(storage_validator)] = sha(storage_validator)
    sources[str(CASE_BUDGET)] = sha(CASE_BUDGET)
    sources[str(QUALIFIED_BUDGET)] = sha(QUALIFIED_BUDGET)
    sources[str(CASE_BUDGET_READER)] = sha(CASE_BUDGET_READER)
    sources[str(OPS/'plans/competition-fault-admission.yaml')] = sha(OPS/'plans/competition-fault-admission.yaml')
    sources[str(SERVICE_ADMISSION)] = sha(SERVICE_ADMISSION)
    sources[str(CALIBRATION)] = sha(CALIBRATION)
    plan = {'id': RUN_ID, 'backend': BACKEND, 'formal_case': True,
        'authorization_scope': 'ONE_SF007_SERVICE30_ONLY',
        'duration_seconds': 1800, 'start_utc': utc(t0), 'seed': args.seed,
        'profile': 'LOW', 'raw_root': str(RAW),
        'control_root': str(RAW/'control'),
        'five_host_heartbeat_path': str(RAW/'control/five-host-storage.json'),
        'five_host_monitor_sha256': sources[str(ROOT/'scripts/five_host_heartbeat.py')],
        'capture_schedule_sha256': sha(schedule_path),
        'capture_controller_unit': 'ops-sf007-formal-capture07-20261017',
        'controller_unit': 'ops-sf007-formal-controller07-20261017',
        'observer_unit': 'ops-sf007-formal-observer07-20261017',
        'health_file': str(health_path), 'health_sha256': sha(health_path),
        'collector_uid': collector['uid'], 'identity_file': str(IDENTITY),
        'identity_sha256': sha(IDENTITY),
        'targets_and_siblings': expected,
        'run_token': secrets.token_hex(24), 'actions': [action],
        'source_sha256': sources,
        'source_plan_sha256': EXPECTED_AUTHORITY,
        'v4_design_sha256': EXPECTED_DESIGN,
        'storage_policy_sha256': sha(STORAGE_POLICY),
        'five_host_residue_sha256': sha(args.five_host_residue),
        'joint_rehearsal_terminal_sha256': sha(joint_run/'terminal.json'),
        'joint_marker_terminal_sha256': sha(joint_run/'marker-control/terminal.json'),
        'service_admission_sha256': sha(SERVICE_ADMISSION)}
    plan['no_fault_control_calibration_sha256'] = sha(CALIBRATION)
    plan['no_fault_validation_sha256'] = {str(path): sha(path) for path in NOFAULT_VALIDATIONS}
    plan['new_stream_validation_contract_sha256'] = sha(NEW_STREAM_CONTRACT)
    plan['storage_v2_qualification_sha256'] = sha(STORAGE_V2_TERMINAL)
    plan['storage_v2_contract_sha256'] = sha(STORAGE_V2_CONTRACT)
    plan['case_budget_contract_sha256'] = sha(CASE_BUDGET)
    plan['case_budget_nofault_sha256'] = sha(CASE_BUDGET_NOFAULT)
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
    print(json.dumps({'status': 'SF007_FORMAL_ATTEMPT_PREREGISTERED_NO_FAULT_YET',
                      'manifest': str(manifest_path),
                      'manifest_sha256': sha(manifest_path),
                      'start_utc': plan['start_utc']}))


if __name__ == '__main__':
    main()
