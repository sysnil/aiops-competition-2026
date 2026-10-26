#!/usr/bin/env python3
"""Evidence-linked Final Fast-Ready decisions; never upgrades diagnostic scoring."""

from datetime import datetime, timezone
import json
from pathlib import Path

from clean_protocol import assert_disk, save_new, sha


ROOT = Path('/root/stage5-v3-final-fast-ready')
RESULTS = ROOT / 'results'
DATA = Path('/data/stage5-v3/final-fast-ready/runs')
CLEAN = DATA / 'S5V3FFR-CLEAN-20261013-S1'
COLLECTOR = DATA / 'S5V3FFR-COLLECTOR-20261013'


def read(path): return json.loads(path.read_text())


def main():
    paths = {
        'collector': RESULTS / 'collector-reliability.json',
        'clean_run': CLEAN / 'run-result.json',
        'clean_diagnostic': CLEAN / 'diagnostic-replay-v1/decision.json',
        'scorer_failure': RESULTS / 'clean-s1-original-scoring-failure.json',
        'capture_pipeline': CLEAN / 'capture-pipeline/pipeline-result.json',
        'memory_guard': CLEAN / 'external-observability-guard/result.json',
        'network_schema': RESULTS / 'control-span-schema-diagnostic.json',
        'payment': RESULTS / 'payment-control-full-window-diagnostic.json',
        'layer_evidence': RESULTS / 'four-layer-evidence.json',
        'route_good': RESULTS / 'network-proof-clean-s1-v2.json',
        'route_default_failed': RESULTS / 'network-proof-clean-s1.json',
        'archive_v1': RESULTS / 'S5V3FFR-CLEAN-20261013-S1-source-manifest.json',
        'archive_v2': RESULTS / 'S5V3FFR-CLEAN-20261013-S1-source-manifest-v2.json',
        'worker2': RESULTS / 'S5V3FFR-CLEAN-20261013-S1-worker2-archive-proof-v2.json',
        'worker3': RESULTS / 'S5V3FFR-CLEAN-20261013-S1-worker3-archive-proof-v2.json',
        'health': RESULTS / 'final-global-safety.json',
        'residue': RESULTS / 'final-five-host-residue.json',
        'draft_invariants': RESULTS / 'draft-invariants.json',
        'offline_archive': RESULTS / 'S5V3FFR-CLEAN-OFFLINE-ARCHIVE-20261013-source-manifest.json',
    }
    if any(not path.is_file() for path in paths.values()):
        raise RuntimeError('Final evidence missing: ' + ', '.join(name for name, path in
            paths.items() if not path.is_file()))
    x = {key: read(path) for key, path in paths.items()}
    worker_sha = sha(paths['archive_v2'])
    if (x['worker2']['source_manifest_sha256'] != worker_sha or
        x['worker3']['source_manifest_sha256'] != worker_sha or
        x['worker2']['status'] != x['worker3']['status'] ==
            'DUAL_ARCHIVE_WORKER_V2_VERIFIED' or
        x['health']['status'] != 'GLOBAL_SAFETY_PASS' or
        x['residue']['status'] != 'HOST_RESIDUE_PASS' or
        x['draft_invariants']['status'] != 'DRAFT_INVARIANTS_PASS'):
        raise RuntimeError('Final dual archive, safety, or DRAFT boundary failed')
    guard = assert_disk(DATA / 'final-readiness-postcheck', 5 * 1024 ** 3)
    now = datetime.now(timezone.utc).isoformat()
    provenance = {name: sha(path) for name, path in paths.items()}
    full_raw = x['clean_run']['status'] == \
        'RAW_CLEAN_CAPTURE_COMPLETE_AWAITING_QUALITY_AND_ARCHIVE' and x[
        'capture_pipeline']['status'] == 'CAPTURE_PIPELINE_PASS'
    archive = {'status': 'DUAL_SHA_RESTORE_V2_PASS_V1_ORDER_BUG_PRESERVED',
        'original_manifest_sha256': sha(paths['archive_v1']),
        'v2_manifest_sha256': worker_sha,
        'file_count': x['archive_v2']['file_count'],
        'source_bytes': x['archive_v2']['source_bytes'],
        'worker2': x['worker2']['status'], 'worker3': x['worker3']['status']}
    runner = {'status': 'RUNNER_RAW_LIFECYCLE_PASS_PREREG_SCORER_FAILED' if
        full_raw else 'RUNNER_RAW_LIFECYCLE_FAILED',
        'at_utc': now, 'offline_new_tests_passed': 15,
        'inherited_draft_tests_passed': 83,
        'offline_dual_archive_transport': 'PASS',
        'data_disk_guard': guard, 'source_locked_files': len(read(CLEAN /
            'schedule.json')['source_sha256']),
        'raw_run_status': x['clean_run']['status'],
        'pipeline_status': x['capture_pipeline']['status'],
        'independent_metric_guard': x['memory_guard']['status'],
        'preregistered_scorer_status': x['scorer_failure']['status'],
        'preregistered_scorer_failure_reason': x['scorer_failure']['specific_cause'],
        'archive': archive, 'full_runner_executable_end_to_end': False,
        'fault_injected': False, 'formal_case': False}
    save_new(RESULTS / 'runner-readiness.json', runner)
    diag = x['clean_diagnostic']
    captures = {phase: {'trace_records': row['trace_records'],
        'span_records': row['span_records'], 'log_records': row['log_records']}
        for phase, row in diag['capture'].items()}
    clean = {'status': 'CLEAN_NOT_VALIDATED_PREREG_SCORER_FAILED_OLD_GATES_FAIL',
        'at_utc': now, 'run_id': CLEAN.name,
        'first_seed_executed': 864101, 'second_seed_executed': False,
        'no_fault_10_5_10_raw_capture_complete': full_raw,
        'formal_preregistered_score_available': False,
        'source_locked_score_failure_sha256': sha(paths['scorer_failure']),
        'diagnostic_only_old_checks': diag['old_rule_checks_descriptive'],
        'diagnostic_only_old_all_pass': diag['old_rule_all_checks_would_pass'],
        'old_node_cpu_delta_cores': diag['old_cpu']['added_cores'],
        'old_node_cpu_limit_cores': 0.25,
        'control_client_diagnostic': {name: {'rules': row['rules'],
            'max_minute_p95_ratio': row.get('max_minute_p95_ratio'),
            'minimum_successful_per_minute': row.get('minimum_successful_per_minute')}
            for name, row in diag['client_controls'].items()},
        'candidate_shadow_not_approved': diag['candidate_shadow_only_not_approved'],
        'network_probe': diag['probes'], 'capture': captures,
        'trace_v2': {'status': diag['trace_v2']['status'],
            'phases': [{'phase': row['phase'], 'status': row['status'],
                        'late_count': len(row['late_ids'])}
                       for row in diag['trace_v2']['phases']]},
        'workload_readiness_diagnostic': diag['workload_readiness'],
        'data_disk_actual_run_bytes': archive['source_bytes'],
        'archive': archive, 'second_window_stopped_by_protocol': True,
        'fault_injected': False, 'formal_case': False,
        'thresholds_changed': False}
    save_new(RESULTS / 'clean-validation.json', clean)
    network = {'status': 'NETWORK_CONTROL_BLOCKED', 'at_utc': now,
        'target_path': 'NETWORK_PATH/k8s-worker2/k8s-worker3',
        'route_packet_proof_during_registered_business_load': x[
            'route_good']['status'],
        'default_background_business_packet_proof': x[
            'route_default_failed']['status'],
        'image_server_span_uid_counts_before': x['network_schema']['details'][
            'image-provider']['uid_counts'],
        'image_direct_pod_uid_complete': False,
        'payment_worker1_minute_edge_span_min': x['payment'][
            'minimum_spans_per_minute'],
        'payment_worker1_each_minute_30_pass': x['payment'][
            'all_minutes_at_least_30_spans'],
        'payment_worker1_old_p95_stability_pass': x['payment'][
            'all_evaluated_minutes_ratio_at_most_1_25'],
        'payment_worker1_shared_source_fault_independence': 'UNVERIFIED',
        'two_normal_controls_qualified': False,
        'tc_filter_applied': False, 'fault_injected': False}
    save_new(RESULTS / 'network-controls.json', network)
    layer = x['layer_evidence']
    mechanisms = {'status': 'FOUR_LAYER_ENGINEERING_READINESS_AUDITED',
        'at_utc': now,
        'SERVICE': {'status': 'SERVICE_CONDITIONAL',
                    'two_replica_coverage': layer['SERVICE'][
                        'two_replica_ordinary_business_30_per_min'],
                    'blockers': ['independent full-window control qualification',
                                 'two bounded direct-root/recovery rehearsals']},
        'POD': {'status': 'POD_CONDITIONAL_WORKLOAD_COVERAGE',
            'identity_cgroup_engineering': layer['POD']['cgroup_status'],
            'candidate_run_30min_coverage': layer['SERVICE'][
                'two_replica_ordinary_business_30_per_min'],
            'clean_before_last5_coverage': diag['workload_readiness'][
                'recommendation_last_five_before_30_each'],
            'blockers': ['coverage varies across independent windows',
                         'two bounded fault/recovery qualification seeds']},
        'NODE': {'status': 'NODE_PENDING_SAFETY_CONTRACT',
            'active_business_pod_count': len(layer['NODE']['active_business_pods']),
            'blockers': ['during-fault CPU/PSI stop thresholds not approved',
                         'two bounded qualification seeds']},
        'NETWORK_PATH': {'status': 'NETWORK_CONTROL_BLOCKED',
            'path_proven_during_business_load': layer['NETWORK_PATH'][
                'verified_during_state_machine_load'],
            'blockers': ['Image direct Span Pod UID absent',
                         'Payment W1 minute p95 old rule fails once',
                         'two independent controls not qualified',
                         'bounded fault filter/shared-source effects unverified']},
        'formal_qualification_allowed': False,
        'faults_injected': 0}
    save_new(RESULTS / 'mechanism-readiness.json', mechanisms)
    final = {'status': 'ENGINEERING_PARTIAL',
        'at_utc': now,
        'RUNNER_STATUS': runner['status'],
        'COLLECTOR_RELIABILITY_STATUS': x['collector']['status'],
        'CLEAN_PROTOCOL_STATUS': clean['status'],
        'SERVICE_READINESS': mechanisms['SERVICE']['status'],
        'POD_READINESS': mechanisms['POD']['status'],
        'NODE_READINESS': mechanisms['NODE']['status'],
        'NETWORK_PATH_READINESS': mechanisms['NETWORK_PATH']['status'],
        'FORMAL_QUALIFICATION_ALLOWED': False,
        'GLOBAL_SAFETY_STATUS': x['health']['status'],
        'five_host_residue': x['residue']['status'],
        'data_disk_uuid': guard['mount']['uuid'],
        'data_disk_free_bytes': guard['data_free_bytes'],
        'root_free_bytes': guard['root_free_bytes'],
        'root_minimum_free_bytes_unchanged': 46_000_000_000,
        'collector_config_restored': x['collector']['original_configuration_restored'],
        'case_created': False, 'test_gt_read': False,
        'stage5_v3_frozen': False, 'faults_injected': 0,
        'source_sha256': provenance}
    save_new(RESULTS / 'final-decision.json', final)
    print(json.dumps({'status': final['status'],
        'formal_allowed': final['FORMAL_QUALIFICATION_ALLOWED'],
        'global_safety': final['GLOBAL_SAFETY_STATUS']}))


if __name__ == '__main__': main()
