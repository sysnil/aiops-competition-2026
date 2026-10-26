"""Freeze only an independent rehearsal batch, never the Stage5-v3 protocol."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import yaml


ROOT = Path('/root/stage5-v3-autonomous-preparation')
STAGE5_V3 = Path('/root/stage5-v3-cross-layer-rca')
EXTERNAL = (
    Path('/root/cross-layer-observability-control-rehearsal/scripts/workload_generator_rehearsal.py'),
    ROOT / 'design/load-profiles-c3.yaml',
    ROOT / 'snapshots/proxy-upstream-repair-c2b-preparation/frontend-proxy-envoy-c2.tmpl.yaml',
    ROOT / 'runtime/proxy-c2b-worker2-pin/deployment-after-pin.json',
    ROOT / 'design/clean-c3-candidate-manifest.json',
    Path('/root/cross-layer-workload-generation/design/people.json'),
    STAGE5_V3 / 'DESIGN.md',
    STAGE5_V3 / 'design/admission-rules-draft.yaml',
)
SOURCE_NAMES = (
    'run_load_pair_c3.py', 'fixed_control_load_c3.py',
    'freeze_clean_candidate.py',
    'capture_pipeline_c2.py', 'capture_live_c2.py',
    'trace_storage_reader.py', 'schedule_trace_completion.py',
    'check_ingest_lag.py', 'probe_network.py', 'verify_business_routes.py',
    'audit_hosts.py',
    'stop_attempt_processes.py', 'resource_fault.py',
    'resource_memory_holder.py', 'network_fault.py', 'build_preflight.py',
    'freeze_registration.py', 'host_io.py', 'run_clean_c3.py',
    'analyze_clean_c3.py', 'assess_clean.py', 'assess_resource_pressure.py',
    'build_registration.py',
    'run_attempt.py', 'assess_attempt.py', 'summarize_phase1.py',
    'audit_public_boundary.py',
)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_plan(plan):
    expected = {'SERVICE': 'process_stall', 'POD': 'memory_pressure',
                'NODE': 'cpu_contention', 'NETWORK_PATH': 'egress_filtered_netem_loss'}
    rows = plan['experiments']
    counts = Counter(row['layer'] for row in rows)
    if (len(rows) != 8 or counts != {layer: 2 for layer in expected} or
            len({row['id'] for row in rows}) != 8 or
            len({row['seed'] for row in rows}) != 8 or
            any(row.get('family') != expected[row['layer']] or
                row.get('fault_seconds') != 300 or
                row.get('formal_case') is not False for row in rows)):
        raise ValueError('Phase 1 plan must contain two fixed rehearsals per mechanism')
    for layer in expected:
        selected = sorted((row for row in rows if row['layer'] == layer),
                          key=lambda row: row['id'])
        prefix = 'NETWORK' if layer == 'NETWORK_PATH' else layer
        if [row['id'] for row in selected] != [f'S5V3AP-{prefix}-S1',
                                               f'S5V3AP-{prefix}-S2']:
            raise ValueError('Mechanism seed IDs are not preregistered')
        if selected[0]['load_plan_sha256'] != selected[1]['load_plan_sha256']:
            raise ValueError('Repeated seeds have different workload versions')
        for field in ('target_entity', 'fault_seconds', 'controls',
                      'direct_evidence', 'admission_thresholds', 'family',
                      'intensity', 'target_pods', 'control_pods',
                      'network_source_pod', 'network_destination_pod',
                      'node_business_pods', 'precondition_status',
                      'impact_envelope'):
            if selected[0].get(field) != selected[1].get(field):
                raise ValueError('Repeated seeds have different protocols: ' + field)
        if selected[0].get('precondition_status') not in ('PASS', 'BLOCKED'):
            raise ValueError('Mechanism eligibility is not determined before registration')
    if (plan.get('stage5_v3_protocol_frozen') is not False or
            plan.get('formal_case_created') is not False or
            plan.get('faults_injected_before_registration') != 0 or
            len(plan.get('clean_evidence_paths', [])) != 6 or
            any(not Path(path).is_relative_to(ROOT / 'results')
                for path in plan['clean_evidence_paths'])):
        raise ValueError('Registration must precede every Phase 1 fault')


def verify_manifest(path):
    value = json.loads(path.read_text())
    if (value.get('status') != 'QUALIFICATION_REGISTRATION_FROZEN_PRE_FAULT' or
            value.get('scope') !=
            'independent_rehearsal_only_not_Stage5_v3_protocol_freeze' or
            value.get('stage5_v3_contracts_frozen') is not False or
            value.get('formal_cases_created') != 0 or
            len(value.get('experiment_order', [])) != 8):
        raise ValueError('Rehearsal registration manifest is invalid')
    for name, digest in value['source_sha256'].items():
        if sha(Path(name)) != digest:
            raise ValueError('Registered source changed: ' + name)
    plan = ROOT / 'design/registration-v1.yaml'
    parsed_plan = yaml.safe_load(plan.read_text())
    verify_plan(parsed_plan)
    if value['plan_sha256'] != sha(plan):
        raise ValueError('Registered attempt plan changed')
    if any(value['source_sha256'].get(path) != sha(Path(path))
           for path in parsed_plan['clean_evidence_paths']):
        raise ValueError('Two-Clean source evidence differs from frozen manifest')
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if (args.plan != ROOT / 'design/registration-v1.yaml' or
            args.output != ROOT / 'design/registration-manifest.json' or
            (args.output.exists() and not args.verify)):
        raise ValueError('Registration path is not the fresh independent batch')
    if args.verify:
        result = verify_manifest(args.output)
        print(json.dumps({'status': result['status'],
                          'experiments': len(result['experiment_order']),
                          'sha256': sha(args.output)}))
        return
    plan = yaml.safe_load(args.plan.read_text())
    verify_plan(plan)
    load_plan = ROOT / 'design/load-plan-c3.yaml'
    workload = ROOT / 'design/workload-state-machine-c3.yaml'
    if any(row['load_plan_sha256'] != sha(load_plan) for row in plan['experiments']):
        raise ValueError('Registered workload plan hash changed')
    sources = [args.plan, load_plan, workload,
               *(Path(path) for path in plan['clean_evidence_paths']),
               *(ROOT / 'scripts' / name for name in SOURCE_NAMES),
               ROOT / 'tests/test_safety.py', *EXTERNAL]
    hashes = {str(path): sha(path) for path in sources}
    result = {
        'status': 'QUALIFICATION_REGISTRATION_FROZEN_PRE_FAULT',
        'scope': 'independent_rehearsal_only_not_Stage5_v3_protocol_freeze',
        'registered_at_utc': datetime.now(timezone.utc).isoformat(),
        'plan_sha256': hashes[str(args.plan)],
        'source_sha256': hashes,
        'experiment_order': [row['id'] for row in plan['experiments']],
        'seeds': {row['id']: row['seed'] for row in plan['experiments']},
        'faults_injected': 0, 'formal_cases_created': 0,
        'stage5_v3_contracts_frozen': False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    args.plan.chmod(0o444)
    args.output.chmod(0o444)
    verify_manifest(args.output)
    print(json.dumps({'status': result['status'], 'experiments': 8,
                      'sha256': sha(args.output)}))


if __name__ == '__main__':
    main()
