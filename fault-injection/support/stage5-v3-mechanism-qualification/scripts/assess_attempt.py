"""Separate private injection checks from public root and client-impact evidence."""

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import statistics

import yaml


PHASES = ('before', 'during', 'after')


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def median(values):
    good = [value for value in values if isinstance(value, (int, float))]
    return statistics.median(good) if good else None


def series(run, phase, family, label, identity):
    path = run / 'capture-pipeline/captures' / phase / 'metrics' / (family + '.json')
    rows = read(path)['data']['result']
    selected = [row['values'] for row in rows if row['metric'].get(label) == identity]
    if len(selected) != 1:
        raise ValueError('Metric identity is missing or ambiguous: ' +
                         phase + '/' + family + '/' + identity)
    return [(float(stamp), float(value)) for stamp, value in selected[0]]


def gauge(run, phase, family, label, identity):
    return median([value for _, value in series(run, phase, family, label, identity)])


def counter_rate(run, phase, family, label, identity):
    values = series(run, phase, family, label, identity)
    if len(values) < 2 or values[-1][0] <= values[0][0] or (
            values[-1][1] < values[0][1]):
        raise ValueError('Counter was reset or lacked enough samples')
    return (values[-1][1] - values[0][1]) / (values[-1][0] - values[0][0])


def controls(analysis):
    result = {}
    for control, edge in (('image_asset', 'frontend-proxy->image-provider'),
                          ('product_detail', 'frontend->product-catalog')):
        trace_bins = analysis['fixed_control_edge_trace_ids_per_minute'].get(
            control + '/' + edge, [0] * 25)
        client = analysis['fixed_control_client'][control]
        result[control] = {
            'minimum_successes': min(row['successful'] for row in client),
            'minimum_call_edge_spans': min(trace_bins),
            'maximum_fault_and_recovery_p95_ratio': max(
                row['client_p95_to_clean_median'] or float('inf')
                for row in client[10:]),
            'pass': all(row['successful'] >= 30 and trace_bins[m] >= 30
                        for m, row in enumerate(client)) and
                    all(row['client_p95_to_clean_median'] is not None and
                        row['client_p95_to_clean_median'] <= 1.25
                        for row in client[10:]),
        }
    return result


def impact(analysis, spec):
    envelope = spec['impact_envelope']
    rows = analysis['state_machine_client']['state_machine/' +
                                            envelope['operation']]
    before, during = rows[:10], rows[10:15]
    attempted = sum(row['attempted'] for row in during)
    if attempted < envelope['minimum_attempted_during_five_minutes']:
        return {'label': 'UNMEASURABLE', 'attempted_during': attempted,
                'reason': 'insufficient_client_attempt_outcomes'}
    before_total = sum(row['attempted'] for row in before)
    if before_total < 300:
        return {'label': 'UNMEASURABLE', 'attempted_during': attempted,
                'attempted_before': before_total,
                'reason': 'insufficient_matched_clean_client_attempts'}
    before_errors = sum(row['attempted'] - row['successful'] for row in before)
    during_errors = sum(row['attempted'] - row['successful'] for row in during)
    error_delta = during_errors / attempted - before_errors / before_total
    baseline_p95 = median([row['client_p95_ms'] for row in before])
    during_p95 = median([row['client_p95_ms'] for row in during])
    ratio = during_p95 / baseline_p95 if during_p95 is not None and baseline_p95 else None
    positive = (error_delta > envelope['clean_max_minute_error_fraction'] or
                ratio is not None and ratio > envelope['clean_max_minute_p95_ratio'])
    if positive and (error_delta >= 0.05 or ratio is not None and ratio >= 1.5):
        label = 'STRONG'
    elif positive:
        label = 'WEAK'
    else:
        label = 'NONE'
    return {'label': label, 'attempted_during': attempted,
            'errors_during': during_errors, 'error_fraction_delta': error_delta,
            'client_p95_ratio_to_matched_before': ratio,
            'clean_noise_envelope': envelope}


def current_pod_identity(analysis, expected):
    return all(analysis['pod_identity_by_phase'][phase].get(
        expected['uid'], {}).get('name') == expected['name']
        for phase in PHASES)


def node_ready(run, phase, node):
    rows = read(run / 'capture-pipeline/captures' / phase /
                'kubernetes/nodes.json')['items']
    target = next((row for row in rows if row['metadata']['name'] == node), None)
    return bool(target and any(row['type'] == 'Ready' and row['status'] == 'True'
                               for row in target['status']['conditions']))


def root_evidence(run, analysis, spec):
    layer = spec['layer']
    threshold = spec['admission_thresholds']
    if layer == 'SERVICE':
        rows = []
        for pod in spec['target_pods']:
            name = pod['name']
            before = counter_rate(run, 'before', 'cpu', 'pod', name)
            during = counter_rate(run, 'during', 'cpu', 'pod', name)
            after = counter_rate(run, 'after', 'cpu', 'pod', name)
            rows.append({'pod': name, 'cpu_cores_before': before,
                         'cpu_cores_during': during, 'cpu_cores_after': after,
                         'drop_fraction': 1 - during / before if before > 0 else None,
                         'uid_stable': current_pod_identity(analysis, pod)})
        clients = analysis['state_machine_client']['state_machine/recommendations']
        before_count = sum(row['successful'] for row in clients[:10]) / 10
        during_count = sum(row['successful'] for row in clients[10:15]) / 5
        during_failures = sum(row['attempted'] - row['successful'] for row in clients[10:15])
        pass_root = all(row['uid_stable'] and row['drop_fraction'] is not None and
                        row['drop_fraction'] >= threshold['direct_cpu_drop_fraction_candidate']
                        for row in rows) and (during_failures > 0 or
                                              during_count < before_count * 0.2)
        return {'pass': pass_root, 'replica_metrics': rows,
                'recommendation_client_success_per_minute_before': before_count,
                'recommendation_client_success_per_minute_during': during_count,
                'recommendation_client_failures_during': during_failures}
    if layer == 'POD':
        target = spec['target_pods'][0]
        sibling = spec['control_pods'][0]
        change = {}
        for role, pod in (('target', target), ('sibling', sibling)):
            vals = {phase: gauge(run, phase, 'memory', 'pod', pod['name'])
                    for phase in PHASES}
            change[role] = {**vals, 'during_minus_before':
                            vals['during'] - vals['before'],
                            'after_minus_before': vals['after'] - vals['before'],
                            'uid_stable': current_pod_identity(analysis, pod),
                            'during_server_trace_ids_per_minute': analysis[
                                'service_server_trace_ids_by_pod_and_minute'].get(
                                'recommendation', {}).get(pod['uid'], [0] * 25)[10:15]}
        pass_root = (change['target']['uid_stable'] and change['sibling']['uid_stable'] and
                     change['target']['during_minus_before'] >=
                     threshold['target_memory_increase_bytes_candidate'] and
                     change['target']['during_minus_before'] >
                     change['sibling']['during_minus_before'] + 64 * 1024 * 1024 and
                     min(change['sibling']['during_server_trace_ids_per_minute']) >= 30)
        return {'pass': pass_root, 'pod_memory_and_sibling': change}
    if layer == 'NODE':
        node = spec['target_entity'].split('/', 1)[1]
        vals = {phase: gauge(run, phase, 'node_cpu', 'k8s_node_name', node)
                for phase in PHASES}
        ready_series = series(run, 'during', 'node_ready',
                              'k8s_node_name', node)
        ready_full_window = (len(ready_series) >= 18 and
                             all(value == 1 for _, value in ready_series))
        frontend = spec['node_business_pods'][0]
        front_p95 = analysis['service_server_p95_ms_by_pod_and_minute'].get(
            'frontend', {}).get(frontend['uid'], [None] * 25)
        ship_p95 = analysis['server_p95_ms_without_uid_by_service_and_minute'].get(
            'shipping', [None] * 25)
        ratios = {}
        for name, values in (('frontend_uid', front_p95),
                             ('shipping_unique_pod_inference', ship_p95)):
            baseline = median(values[:10])
            fault = median(values[10:15])
            ratios[name] = fault / baseline if fault is not None and baseline else None
        pass_root = (vals['during'] - vals['before'] >=
                     threshold['node_cpu_increase_cores_candidate'] and
                     ready_full_window and node_ready(run, 'during', node) and
                     node_ready(run, 'after', node) and
                     current_pod_identity(analysis, frontend) and
                     all(value is not None and value >=
                         threshold['same_node_two_pod_p95_ratio_min_candidate']
                         for value in ratios.values()))
        return {'pass': pass_root, 'node_cpu_usage': vals,
                'same_node_pod_p95_ratios': ratios,
                'shipping_identity': 'INFERRED_FROM_UNIQUE_SERVICE_POD_NOT_TRACE_UID',
                'node_ready_metric_samples_during': len(ready_series),
                'node_ready_full_window': ready_full_window,
                'node_ready_during': node_ready(run, 'during', node),
                'node_ready_after': node_ready(run, 'after', node)}
    if layer == 'NETWORK_PATH':
        probes = analysis['probes']
        target = analysis['call_edge_trace_ids_per_minute'].get('checkout->cart', [0] * 25)
        route = read(run / 'route-proof.json')
        row = next((item for item in route['rows']
                    if item['pair'] == 'checkout_cart_path'), None)
        uid_route = bool(row and row['status'] == 'VERIFIED' and
                         row['source_pod']['uid'] ==
                         spec['network_source_pod']['uid'] and
                         row['destination_pod']['uid'] ==
                         spec['network_destination_pod']['uid'])
        coverage = (probes['during']['valid_numeric'] >=
                    threshold['during_valid_iperf_samples_min'] and
                    probes['during']['max_consecutive_invalid'] <=
                    threshold['during_max_consecutive_invalid'])
        abnormal = (probes['during']['receiver_loss_median'] is not None and
                    probes['during']['receiver_loss_median'] >=
                    threshold['fault_median_receiver_loss_fraction_min_candidate'] and
                    probes['during']['icmp_loss_mean'] is not None and
                    probes['during']['icmp_loss_mean'] >=
                    threshold['fault_mean_icmp_loss_fraction_min_candidate'])
        recovery = all(probes[phase]['receiver_loss_median'] is not None and
                       probes[phase]['receiver_loss_median'] <=
                       threshold['before_after_receiver_loss_fraction_max_candidate']
                       for phase in ('before', 'after'))
        pass_root = (uid_route and coverage and abnormal and recovery and
                     min(target[5:10]) >=
                     threshold['prefault_target_edge_spans_per_minute'])
        return {'pass': pass_root, 'route_uid_and_packet_verified': uid_route,
                'target_edge_before_last_five_minutes': target[5:10],
                'probe_coverage_pass': coverage,
                'receiver_and_independent_icmp_signal_pass': abnormal,
                'loss_recovered': recovery, 'probes': probes}
    raise ValueError('Unregistered root layer')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--analysis', type=Path, required=True)
    parser.add_argument('--registration', type=Path, required=True)
    parser.add_argument('--post-health', type=Path, required=True)
    parser.add_argument('--post-residue', type=Path, required=True)
    parser.add_argument('--public-boundary-audit', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refusing to overwrite a qualification decision')
    attempt = read(args.run / 'result.json')
    analysis = read(args.analysis)
    plan = yaml.safe_load(args.registration.read_text())
    spec = next((row for row in plan['experiments'] if row['id'] ==
                 attempt['experiment_id']), None)
    if not spec or analysis['run_id'] != attempt['experiment_id']:
        raise ValueError('Rehearsal identity does not match registration')
    health = read(args.post_health)
    residue = read(args.post_residue)
    public_audit = read(args.public_boundary_audit)
    states = [read(path) for path in sorted(args.run.glob('fault-state-*.json'))]
    expected_hosts = set(attempt['fault_hosts'])
    start = datetime.fromisoformat(attempt['fault_start_utc'])
    runtime = []
    for row in states:
        actual_start = datetime.fromisoformat(row['start_at_utc'])
        cleanup = datetime.fromisoformat(row['cleanup_at_utc'])
        runtime.append({'host': row['host'], 'status': row['status'],
                        'start_lag_seconds': (actual_start - start).total_seconds(),
                        'active_to_cleanup_seconds': (cleanup - actual_start).total_seconds(),
                        'state_sha256': sha(args.run / ('fault-state-' + row['host'] + '.json'))})
    injection = (attempt['status'] == 'REHEARSAL_CAPTURED' and
                 len(states) == len(expected_hosts) and
                 {row['host'] for row in states} == expected_hosts and
                 all(row['status'] == 'CLEANED' and
                     abs(item['start_lag_seconds']) <= 5 and
                     290 <= item['active_to_cleanup_seconds'] <= 330
                     for row, item in zip(states, runtime)) and
                 health['status'] == 'STAGE5_BASE_HEALTH_PASS' and
                 residue['status'] == 'HOST_RESIDUE_PASS')
    acquisition = (analysis['pipeline_status'] == 'CAPTURE_PIPELINE_PASS' and
                   analysis['load_status'] == 'LOAD_PAIR_COMPLETE' and
                   all(row['trace_source'] == 'JAEGER_OPENSEARCH_STORAGE' and
                       row['start_lag_seconds'] <= 25 and
                       row['completion_lag_seconds'] <= 120
                       for row in analysis['source_health'].values()))
    control = controls(analysis)
    try:
        direct = root_evidence(args.run, analysis, spec)
    except (KeyError, ValueError, ZeroDivisionError) as exc:
        direct = {'pass': False, 'evidence_error': type(exc).__name__ + ': ' + str(exc)}
    if spec['layer'] == 'POD':
        sibling = direct.get('pod_memory_and_sibling', {}).get('sibling', {})
        control_ok = (sibling.get('uid_stable') is True and
                      min(sibling.get('during_server_trace_ids_per_minute') or [0]) >= 30)
    elif spec['layer'] == 'NODE':
        control_ok = control['image_asset']['pass']
    else:
        control_ok = all(row['pass'] for row in control.values())
    observable = (acquisition and direct['pass'] and control_ok and
                  public_audit['status'] == 'NO_PRIVATE_LEAK_DETECTED')
    impact_result = impact(analysis, spec)
    result = {
        'status': 'QUALIFICATION_SEED_PASS' if injection and observable
                  else 'QUALIFICATION_SEED_FAILED',
        'experiment_id': spec['id'], 'layer': spec['layer'],
        'family': spec['family'], 'seed': spec['seed'],
        'injection_validity': 'PASS' if injection else 'FAIL',
        'observability_validity': 'PASS' if observable else 'FAIL',
        'business_impact': impact_result,
        'root_observable': bool(injection and observable),
        'user_impactful': bool(injection and observable and
                               impact_result['label'] in ('STRONG', 'WEAK')),
        'direct_public_evidence': direct,
        'normal_control_evidence': control,
        'normal_control_validity': control_ok,
        'acquisition_validity': acquisition,
        'public_private_boundary': public_audit['status'],
        'private_injection_runtime': runtime,
        'post_health_status': health['status'],
        'post_residue_status': residue['status'],
        'source_sha256': {str(path): sha(path) for path in (
            args.analysis, args.registration, args.run / 'result.json',
            args.post_health, args.post_residue, args.public_boundary_audit)},
        'formal_case_created': False, 'rca_predictions_run': False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'status': result['status'], 'experiment_id': spec['id'],
                      'injection': result['injection_validity'],
                      'observability': result['observability_validity'],
                      'impact': impact_result['label']}))


if __name__ == '__main__':
    main()
