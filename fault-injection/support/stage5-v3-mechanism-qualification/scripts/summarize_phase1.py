"""Machine-readable Phase 1 summary; never changes Stage5-v3 DRAFT status."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import yaml


MECHANISMS = {
    'SERVICE_PROCESS_STALL': ('SERVICE', 'process_stall'),
    'POD_RECOMMENDATION_MEMORY': ('POD', 'memory_pressure'),
    'NODE_CPU_WORKER1': ('NODE', 'cpu_contention'),
    'NETWORK_W3W2_LOSS': ('NETWORK_PATH', 'egress_filtered_netem_loss'),
}


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--clean-decision', type=Path, required=True)
    parser.add_argument('--clean-analysis', type=Path, required=True)
    parser.add_argument('--public-boundary-audit', type=Path, required=True)
    parser.add_argument('--previous-clean-decision', type=Path)
    parser.add_argument('--final-health', type=Path, required=True)
    parser.add_argument('--final-residue', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refusing overwrite of qualification summary')
    clean = read(args.clean_decision)
    analysis = read(args.clean_analysis)
    boundary = read(args.public_boundary_audit)
    previous = read(args.previous_clean_decision) if args.previous_clean_decision else None
    health = read(args.final_health)
    residue = read(args.final_residue)
    if (analysis['run_id'] != clean['run_id'] or
            boundary['status'] != 'NO_PRIVATE_LEAK_DETECTED'):
        raise ValueError('Clean decision and public boundary audit do not agree')
    registration_path = args.root / 'design/registration-v1.yaml'
    manifest_path = args.root / 'design/registration-manifest.json'
    plan = yaml.safe_load(registration_path.read_text()) if registration_path.exists() else None
    manifest = read(manifest_path) if manifest_path.exists() else None
    if plan is not None and (manifest is None or manifest['status'] !=
                            'QUALIFICATION_REGISTRATION_FROZEN_PRE_FAULT' or
                            manifest['plan_sha256'] != sha(registration_path)):
        raise ValueError('Attempt results lack an immutable preregistration')
    rows = {}
    for key, (layer, family) in MECHANISMS.items():
        seed_results = []
        specs = [row for row in (plan or {}).get('experiments', [])
                 if row['layer'] == layer and row['family'] == family]
        if plan and len(specs) != 2:
            raise ValueError('Frozen mechanism does not have two seed attempts')
        for index in (0, 1):
            spec = specs[index] if specs else None
            source = args.root / 'results' / (spec['id'] + '.json') if spec else None
            if source and source.is_file():
                result = read(source)
                if (result['experiment_id'] != spec['id'] or
                        result['seed'] != spec['seed'] or
                        result.get('formal_case_created') is not False or
                        result.get('rca_predictions_run') is not False):
                    raise ValueError('Seed result is not the registered rehearsal')
                seed_results.append({'seed': spec['seed'], 'attempt_id': spec['id'],
                                     'status': result['status'],
                                     'injection': result['injection_validity'],
                                     'observability': result['observability_validity'],
                                     'business_impact': result['business_impact']['label'],
                                     'result_sha256': sha(source)})
            else:
                seed_results.append({'seed': spec['seed'] if spec else None,
                                     'attempt_id': spec['id'] if spec else None,
                                     'status': 'NOT_RUN',
                                     'reason': clean['mechanisms'][key]['reasons']})
        if clean['mechanisms'][key]['status'] != 'PRECONDITION_PASS':
            recommendation = 'BLOCKED_PRECONDITION'
        elif all(row['status'] == 'QUALIFICATION_SEED_PASS' for row in seed_results):
            recommendation = 'READY_SUGGESTED_PENDING_HUMAN_REVIEW'
        elif any(row['status'] != 'NOT_RUN' for row in seed_results):
            recommendation = 'CONDITIONAL_REHEARSAL_INCOMPLETE_OR_FAILED'
        else:
            recommendation = 'CONDITIONAL_NOT_YET_REHEARSED'
        rows[key] = {'layer': layer, 'family': family,
                     'clean_precondition': clean['mechanisms'][key],
                     'seed_results': seed_results,
                     'qualification_recommendation': recommendation}
    state_files = sorted((args.root / 'attempts').glob('*/fault-state-*.json'))
    injected = {path.parent.name for path in state_files
                if read(path).get('start_at_utc')}
    if (health['status'] != 'STAGE5_BASE_HEALTH_PASS' or
            residue['status'] != 'HOST_RESIDUE_PASS'):
        raise ValueError('Final health or five-host residue gate is not PASS')
    result = {
        'status': 'PHASE1_READY_SUGGESTIONS_REVIEW_REQUIRED' if any(
            row['qualification_recommendation'].startswith('READY_SUGGESTED')
            for row in rows.values()) else 'PHASE1_NO_READY_MECHANISM',
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'scope': 'independent_rehearsal_only_not_formal_DEV_TEST',
        'stage5_v3_contracts_frozen': False,
        'registration_status': manifest['status'] if manifest else 'NOT_FROZEN_NO_FAULT',
        'clean_decision_status': clean['status'],
        'no_fault_windows': {
            'previous': ({'run_id': previous['run_id'], 'status': previous['status'],
                          'blockers': previous['base_blockers']}
                         if previous else None),
            'current': {'run_id': clean['run_id'], 'status': clean['status'],
                        'blockers': clean['base_blockers'],
                        'recommendation_server_traces_before_last_five_minutes':
                            clean['recommendation_server_traces_before_last_five_minutes'],
                        'checkout_cart_traces_before_last_five_minutes':
                            clean['checkout_cart_traces_before_last_five_minutes'],
                        'normal_controls': clean['normal_controls'],
                        'probe_valid': clean['probe_valid'],
                        'capture_status': analysis['pipeline_status'],
                        'route_proof_status': analysis['route_proof_status'],
                        'public_boundary_status': boundary['status']}},
        'faults_injected': len(injected), 'injected_attempt_ids': sorted(injected),
        'formal_cases_created': 0, 'rca_predictions_run': 0,
        'final_health': health['status'], 'final_residue': residue['status'],
        'mechanisms': rows,
        'source_sha256': {str(path): sha(path) for path in
                          (args.clean_decision, args.clean_analysis,
                           args.public_boundary_audit, args.final_health,
                           args.final_residue,
                           *((args.previous_clean_decision,) if
                             args.previous_clean_decision else ()))},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'status': result['status'],
                      'faults_injected': result['faults_injected'],
                      'mechanisms': {key: row['qualification_recommendation']
                                     for key, row in rows.items()}}))


if __name__ == '__main__':
    main()
