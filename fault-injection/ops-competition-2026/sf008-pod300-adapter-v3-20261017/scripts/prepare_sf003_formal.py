#!/usr/bin/env python3
"""Pre-register the one authorized SF-008 formal attempt, without injection."""

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
RUN_ID = 'SF-008-ATTEMPT-08'
RAW = Path('/data/stage5-v3/ops-competition-2026/runs') / RUN_ID
FORMAL = OPS / 'sf008-pod300-formal-v3-20261017'
IDENTITY = FORMAL / 'plans/current-identity.json'
POD_ADMISSION = FORMAL / 'design/pod-memory-admission-draft.yaml'
CASE_DESIGN = FORMAL / 'design/case-design.json'
QUALIFICATION = OPS / 'qualification/COMP-SF01-POD-300'
QUALIFICATION_PLAN = OPS / 'plans/COMP-SF01-POD-300.json'
WATCHDOG = OPS / 'qualification/SF008-WATCHDOG-SELFTEST-W2-ATTEMPT08.json'
AUTHORITY = OPS / 'low-stable-v4-20261017/design/remaining-cases-draft.json'
STORAGE_POLICY = OPS / 'sf003-to-sf012-production-20261015/design/storage-policy-5gb.json'
NOFAULT_VALIDATIONS = (
    Path('/data/stage5-v3/ops-competition-2026/low-stable-v4-20261017/validation-runs-v4/VAL4/assessment.json'),
    Path('/data/stage5-v3/ops-competition-2026/low-stable-v4-20261017/validation-runs-v6/VAL6/assessment.json'),
)
STORAGE_V2_CONTRACT = OPS / 'low-stable-v4-20261017/storage-v2/qualification-contract.json'
STORAGE_V2_TERMINAL = OPS / 'low-stable-v4-20261017/storage-v2/results/STORAGE-V2-NOFAULT-01/terminal.json'
CASE_BUDGET = OPS / 'low-stable-v4-20261017/storage-v2/case-budgets/sf008-att08.json'
CASE_BUDGET_REGISTRATION = OPS / 'low-stable-v4-20261017/storage-v2/case-budgets/sf008-att08-qualification-registration.json'
CASE_BUDGET_TERMINAL = OPS / 'low-stable-v4-20261017/storage-v2/results/SF008-ATT08-BUDGET-NOFAULT-01/terminal.json'
CASE_BUDGET_PREFLIGHT = OPS / 'low-stable-v4-20261017/storage-v2/dynamic_storage_case.py'
EXPECTED_AUTHORITY = '64567bac143fa955cba501abf591049adba42ec2c15499ab7629e7e9b72e08ef'


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
    case_design = read(CASE_DESIGN)
    if (case_design['status'] != 'DRAFT_NOT_FROZEN' or
            case_design['run_id'] != RUN_ID or
            case_design['formal_duration_seconds'] != 1800 or
            case_design['timeline']['pod_fault_seconds'] != [600, 900] or
            case_design['timeline']['recovery_check_offset_seconds'] != 1140 or
            case_design['timeline']['postcheck_offset_seconds'] != 1200 or
            case_design['timeline']['fault_terminal_deadline_offset_seconds'] != 1380 or
            case_design['mechanism']['id'] != 'POD_MEMORY_32M_300' or
            case_design['absolute_disk_floor_bytes'] != 5_000_000_000 or
            case_design['archive_target'] != 'worker2' or
            case_design['storage_commitment_contract'] != str(CASE_BUDGET) or
            case_design['background_data_reservation_bytes'] !=
                {'worker1': 2_000_000_000, 'worker3': 2_000_000_000}):
        raise RuntimeError('SF-008 ATTEMPT-08 case design changed')
    joint_run = Path('/data/stage5-v3/ops-competition-2026/rehearsals/SF010-CONTROLLER-DRYRUN-03')
    capture = read(joint_run/'terminal.json')
    if (capture['status'] != 'NOFAULT_30MIN_CAPTURE_PASS' or
            capture['all_chunks_pass'] is not True):
        raise RuntimeError('Qualified shared thirty-minute capture chain not validated')
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
            raise RuntimeError('Two full-chain 30-minute request and trace validations required')
    storage_contract = read(STORAGE_V2_CONTRACT)
    storage_qualification = read(STORAGE_V2_TERMINAL)
    if (storage_contract['status'] != 'PREREGISTERED_STORAGE_V2_NOFAULT_1800S' or
            storage_qualification['status'] != 'STORAGE_V2_NOFAULT_1800_PASS' or
            storage_qualification['contract_sha256'] != sha(STORAGE_V2_CONTRACT) or
            storage_qualification['samples'] < 120 or
            storage_qualification['faults_injected'] != 0 or
            storage_qualification['formal_cases_created'] != 0):
        raise RuntimeError('Prospective storage gate lacks full no-fault qualification')
    budget, budget_registration, budget_qualification = map(read,
        (CASE_BUDGET, CASE_BUDGET_REGISTRATION, CASE_BUDGET_TERMINAL))
    if (budget['status'] != 'PREREGISTERED_SF008_ATT08_STORAGE_BUDGET' or
            budget['run_id'] != RUN_ID or budget['archive_target'] != 'worker2' or
            budget_qualification['status'] != 'SF008_ATT08_BUDGET_NOFAULT_1800_PASS' or
            budget_qualification['case_budget_sha256'] != sha(CASE_BUDGET) or
            budget_qualification['qualification_registration_sha256'] !=
                sha(CASE_BUDGET_REGISTRATION) or
            budget_qualification['samples'] < 120 or
            budget_qualification['faults_injected'] != 0 or
            budget_qualification['formal_cases_created'] != 0 or
            budget_registration['case_budget_sha256'] != sha(CASE_BUDGET)):
        raise RuntimeError('Case-specific storage budget lacks full no-fault qualification')
    snapshot = read(args.five_host_snapshot)
    age = time.time() - datetime.fromisoformat(snapshot['at_utc']).timestamp()
    if (snapshot['status'] != 'READ_ONLY_FIVE_GB_SNAPSHOT' or
            snapshot['one_case']['status'] != 'DYNAMIC_STORAGE_PASS' or
            snapshot['commitment_contract_sha256'] != sha(CASE_BUDGET) or
            snapshot['preflight_source_sha256'] != sha(CASE_BUDGET_PREFLIGHT) or
            snapshot['archive_target'] != 'worker2' or
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
        raise RuntimeError('Historical real PodMemoryStress300 qualification invalid')
    os.environ['SF008_RUN'] = str(RAW)
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
        print(json.dumps({'status': 'SF008_FORMAL_INPUTS_VALIDATED_NO_CASE_CREATED',
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
    health = {'status': 'SF008_FORMAL_ENVIRONMENT_PASS', 'at_utc': utc(time.time()),
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
                'load': {'profile': 'LOW', 'policy_name': 'LOW_STABLE',
                         'sessions_per_second': 0.7, 'image_per_second': 1,
                         'cart_per_second': 1, 'home_per_second': 1,
                         'recommendations_per_second': 1}}
    schedule_path = RAW/'schedule.json'
    atomic(schedule_path, schedule, True)
    action_id = RUN_ID+'-POD'
    action = {'run_id': RUN_ID, 'action_id': action_id,
        'fault_event_id': 'SF-008-F01', 'fault_type': 'PodMemoryStress',
        'mechanism_id': 'POD', 'worker': 'k8s-worker2',
        'targets': registration['targets'], 'duration_seconds': 300,
        'offset_seconds': 600, 'planned_utc': utc(t0+600),
        'recovery_offset_seconds': 1140, 'onset_tolerance_seconds': 2,
        'unit': 'ops-sf008-'+action_id.lower(), 'backend': BACKEND,
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
    for path in (CASE_BUDGET, CASE_BUDGET_REGISTRATION, CASE_BUDGET_PREFLIGHT):
        sources[str(path)] = sha(path)
    sources[str(OPS/'plans/competition-fault-admission.yaml')] = sha(OPS/'plans/competition-fault-admission.yaml')
    sources[str(POD_ADMISSION)] = sha(POD_ADMISSION)
    sources[str(CASE_DESIGN)] = sha(CASE_DESIGN)
    plan = {'id': RUN_ID, 'backend': BACKEND, 'formal_case': True,
        'authorization_scope': 'ONE_SF008_POD_MEMORY300_ONLY',
        'duration_seconds': 1800, 'start_utc': utc(t0), 'seed': args.seed,
        'profile': 'LOW', 'raw_root': str(RAW),
        'control_root': str(RAW/'control'),
        'five_host_heartbeat_path': str(RAW/'control/five-host-storage.json'),
        'five_host_monitor_sha256': sources[str(ROOT/'scripts/five_host_heartbeat.py')],
        'capture_schedule_sha256': sha(schedule_path),
        'capture_controller_unit': 'ops-sf008-formal-capture08-20261017',
        'controller_unit': 'ops-sf008-formal-controller08-20261017',
        'observer_unit': 'ops-sf008-formal-observer08-20261017',
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
        'no_fault_validation_sha256': {str(path): sha(path) for path in NOFAULT_VALIDATIONS},
        'storage_v2_qualification_sha256': sha(STORAGE_V2_TERMINAL),
        'storage_v2_contract_sha256': sha(STORAGE_V2_CONTRACT),
        'case_budget_contract_sha256': sha(CASE_BUDGET),
        'case_budget_qualification_sha256': sha(CASE_BUDGET_TERMINAL),
        'case_budget_registration_sha256': sha(CASE_BUDGET_REGISTRATION),
        'pod_admission_sha256': sha(POD_ADMISSION)}
    plan['case_design_sha256'] = sha(CASE_DESIGN)
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
    print(json.dumps({'status': 'SF008_FORMAL_ATTEMPT_PREREGISTERED_NO_FAULT_YET',
                      'manifest': str(manifest_path),
                      'manifest_sha256': sha(manifest_path),
                      'start_utc': plan['start_utc']}))


if __name__ == '__main__':
    main()
