"""Mechanism-specific no-fault readiness from immutable qualification summaries."""

import argparse
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text())


def pod_bins(analysis, service, pod):
    return analysis['service_server_trace_ids_by_pod_and_minute'].get(service, {}).get(
        pod, [0] * 25)


def metric_ok(analysis, family, phase, expected_rows, minimum):
    rows = analysis['target_metric_samples'][phase][family]
    return len(rows) >= expected_rows and all(row['samples'] >= minimum for row in rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--analysis', required=True, type=Path)
    parser.add_argument('--health', required=True, type=Path)
    parser.add_argument('--residue', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refusing overwrite of no-fault decision')
    value = read(args.analysis)
    health = read(args.health)
    residue = read(args.residue)
    reasons = []
    if (health['status'] != 'STAGE5_BASE_HEALTH_PASS' or
            residue['status'] != 'HOST_RESIDUE_PASS'):
        reasons.append('FIVE_HOST_OR_COLLECTOR_HEALTH_FAILED')
    if (value['lifecycle_status'] != 'CLEAN_LIFECYCLE_PASS' or
            value['load_status'] != 'LOAD_PAIR_COMPLETE' or
            value['pipeline_status'] != 'CAPTURE_PIPELINE_PASS' or
            value['route_proof_status'] != 'ROUTE_FLOW_PROOF_PASS'):
        reasons.append('NO_FAULT_LIFECYCLE_CAPTURE_OR_ROUTE_FAILED')
    if any(row['trace_source'] != 'JAEGER_OPENSEARCH_STORAGE' or
           row['completion_lag_seconds'] > 120 or row['start_lag_seconds'] > 25
           for row in value['source_health'].values()):
        reasons.append('CAPTURE_SOURCE_OR_FRESHNESS_FAILED')
    if any(row['attempted'] != row['successful']
           for bins in value['state_machine_client'].values() for row in bins):
        reasons.append('STATE_MACHINE_CREATED_ERRORS_WITHOUT_FAULT')

    probes = value['probes']
    probe_ok = all(
        probes[phase]['planned'] == planned and
        probes[phase]['valid_numeric'] >= minimum and
        probes[phase]['max_consecutive_invalid'] <= 2
        for phase, planned, minimum in (('before', 120, 108),
                                         ('during', 60, 54),
                                         ('after', 120, 108)))
    if not probe_ok:
        reasons.append('NO_FAULT_PROBE_WINDOW_FAILED')

    pod_meta = value['pod_identity_by_phase']['before']
    rec = sorted((uid, row['name']) for uid, row in pod_meta.items()
                 if row['service'] == 'recommendation')
    rec_coverage = {name: pod_bins(value, 'recommendation', uid)[5:10]
                    for uid, name in rec}
    rec_ok = len(rec) == 2 and all(min(counts) >= 30
                                   for counts in rec_coverage.values())
    identity_ok = all(set(value['pod_identity_by_phase'][phase]) >=
                      {uid for uid, _ in rec} for phase in ('during', 'after'))
    if not identity_ok:
        reasons.append('RECOMMENDATION_POD_UID_CHANGED')

    resource_ok = all(
        metric_ok(value, family, phase, 2, minimum)
        for phase, minimum in (('before', 36), ('during', 18), ('after', 36))
        for family in ('cpu', 'memory', 'cfs_periods', 'cfs_throttled_periods',
                       'cfs_throttled_seconds'))
    node_metrics_ok = all(
        metric_ok(value, family, phase, 1, minimum)
        for phase, minimum in (('before', 36), ('during', 18), ('after', 36))
        for family in ('node_cpu', 'node_cpu_time', 'node_memory'))
    worker1_frontends = [uid for uid, row in pod_meta.items()
                         if row['service'] == 'frontend' and row['node'] == 'k8s-worker1']
    worker1_shipping = [uid for uid, row in pod_meta.items()
                        if row['service'] == 'shipping' and row['node'] == 'k8s-worker1']
    node_business_ok = (len(worker1_frontends) == len(worker1_shipping) == 1 and
                        sum(pod_bins(value, 'frontend', worker1_frontends[0])[5:10]) >= 150 and
                        sum(value['server_trace_ids_without_uid_by_service_and_minute'].get(
                            'shipping', [0] * 25)[5:10]) >= 5)

    target = value['call_edge_trace_ids_per_minute'].get('checkout->cart', [0] * 25)
    network_target_ok = min(target[5:10]) >= 30
    controls = {}
    for name, edge in (('image_asset', 'frontend-proxy->image-provider'),
                       ('product_detail', 'frontend->product-catalog')):
        edge_key = name + '/' + edge
        spans = value['fixed_control_edge_trace_ids_per_minute'].get(edge_key,
                                                                     [0] * 25)
        client = value['fixed_control_client'][name]
        control_ok = all(row['successful'] >= 30 and spans[m] >= 30
                         for m, row in enumerate(client))
        stable = all(row['client_p95_to_clean_median'] is not None and
                     row['client_p95_to_clean_median'] <= 1.25
                     for row in client[10:])
        controls[name] = {'request_and_span_coverage': control_ok,
                          'stable_latency': stable,
                          'minimum_successes': min(row['successful'] for row in client),
                          'minimum_edge_trace_ids': min(spans)}
    controls_ok = all(row['request_and_span_coverage'] and row['stable_latency']
                      for row in controls.values())

    base_ok = not reasons
    mechanism = {
        'SERVICE_PROCESS_STALL': {'status': 'PRECONDITION_PASS' if
            base_ok and rec_ok and identity_ok and controls_ok else 'BLOCKED',
            'reasons': [name for name, condition in (
                ('RECOMMENDATION_BOTH_REPLICAS_30_PER_MIN', rec_ok),
                ('POD_UID_CONTINUITY', identity_ok),
                ('INDEPENDENT_CONTROLS', controls_ok)) if not condition]},
        'POD_RECOMMENDATION_MEMORY': {'status': 'PRECONDITION_PASS' if
            base_ok and rec_ok and identity_ok and resource_ok else 'BLOCKED',
            'reasons': [name for name, condition in (
                ('RECOMMENDATION_TARGET_AND_SIBLING_30_PER_MIN', rec_ok),
                ('POD_UID_CONTINUITY', identity_ok),
                ('FIVE_POD_RESOURCE_SERIES', resource_ok)) if not condition]},
        'NODE_CPU_WORKER1': {'status': 'PRECONDITION_PASS' if
            base_ok and node_business_ok and node_metrics_ok else 'BLOCKED',
            'reasons': [name for name, condition in (
                ('TWO_ACTIVE_BUSINESS_PODS_WORKER1', node_business_ok),
                ('NODE_METRIC_COVERAGE', node_metrics_ok)) if not condition]},
        'NETWORK_W3W2_LOSS': {'status': 'PRECONDITION_PASS' if
            base_ok and network_target_ok and controls_ok and probe_ok else 'BLOCKED',
            'reasons': [name for name, condition in (
                ('CHECKOUT_CART_30_SPANS_EACH_PREFAULT_MINUTE', network_target_ok),
                ('FIXED_INDEPENDENT_CONTROLS', controls_ok),
                ('FULL_NETWORK_PROBE_WINDOW', probe_ok)) if not condition]},
    }
    if reasons:
        for row in mechanism.values():
            row['reasons'] = reasons + row['reasons']
    result = {'status': 'CLEAN_BASE_PASS' if base_ok else 'CLEAN_BASE_BLOCKED',
              'base_blockers': reasons, 'run_id': value['run_id'],
              'recommendation_server_traces_before_last_five_minutes': rec_coverage,
              'checkout_cart_traces_before_last_five_minutes': target[5:10],
              'normal_controls': controls,
              'probe_valid': {phase: row['valid_numeric'] for phase, row in probes.items()},
              'worker1_two_business_pods_active': node_business_ok,
              'worker1_shipping_linkage': 'UNIQUE_SERVICE_POD_INFERENCE_NOT_TRACE_UID',
              'mechanisms': mechanism,
              'faults_injected': 0, 'formal_cases_created': 0}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'status': result['status'],
                      'mechanisms': {name: row['status'] for name, row in mechanism.items()},
                      'base_blockers': reasons}))


if __name__ == '__main__':
    main()
