"""Build a private Phase 1 plan from fresh no-fault evidence, before any fault."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics

import yaml


ROOT = Path('/root/stage5-v3-autonomous-preparation')
LAYERS = ('SERVICE', 'POD', 'NODE', 'NETWORK_PATH')
SEEDS = {'SERVICE': (852101, 852102), 'POD': (852201, 852202),
         'NODE': (852301, 852302), 'NETWORK_PATH': (852401, 852402)}
RELEVANT_SERVICES = {'frontend-proxy', 'frontend', 'recommendation',
                     'checkout', 'cart', 'image-provider', 'shipping',
                     'product-catalog'}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relevant_pods(analysis):
    return {uid: row for uid, row in
            analysis['pod_identity_by_phase']['before'].items()
            if row['service'] in RELEVANT_SERVICES}


def repeated_precondition(current, previous, key):
    rows = [current['mechanisms'][key], previous['mechanisms'][key]]
    return ('PASS' if all(row['status'] == 'PRECONDITION_PASS' for row in rows)
            else 'BLOCKED')


def pod_from_snapshot(pods, service, node=None):
    selected = []
    for item in pods:
        if item['metadata']['labels'].get('opentelemetry.io/name') != service:
            continue
        if node is not None and item['spec']['nodeName'] != node:
            continue
        containers = item['status'].get('containerStatuses', [])
        if len(containers) != 1 or not containers[0].get('ready') or (
                item['metadata'].get('deletionTimestamp')):
            continue
        selected.append({'name': item['metadata']['name'],
                         'uid': item['metadata']['uid'],
                         'service': service,
                         'node': item['spec']['nodeName'],
                         'ip': item['status']['podIP'],
                         'container_id': containers[0]['containerID']})
    return sorted(selected, key=lambda row: row['name'])


def one(rows, description):
    if len(rows) != 1:
        raise ValueError(description + ' is not one current Ready Pod')
    return rows[0]


def impact_envelope(analysis, operation):
    rows = analysis['state_machine_client']['state_machine/' + operation]
    baseline = statistics.median(row['client_p95_ms'] for row in rows[:10]
                                 if row['client_p95_ms'] is not None)
    if baseline <= 0:
        raise ValueError('Clean client p95 baseline is invalid: ' + operation)
    return {
        'operation': operation,
        'prefault_minute_p95_median_ms': baseline,
        'clean_max_minute_p95_ratio': max(
            row['client_p95_ms'] / baseline for row in rows
            if row['client_p95_ms'] is not None),
        'clean_max_minute_error_fraction': max(
            (row['attempted'] - row['successful']) / row['attempted']
            for row in rows if row['attempted']),
        'minimum_attempted_during_five_minutes': 150,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--analysis', type=Path, required=True)
    parser.add_argument('--decision', type=Path, required=True)
    parser.add_argument('--previous-analysis', type=Path, required=True)
    parser.add_argument('--previous-decision', type=Path, required=True)
    parser.add_argument('--resource-pressure', type=Path, required=True)
    parser.add_argument('--previous-resource-pressure', type=Path, required=True)
    parser.add_argument('--public-boundary-audit', type=Path, required=True)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--previous-run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output != ROOT / 'design/registration-v1.yaml':
        raise ValueError('Registration path must be new and independent')
    analysis = json.loads(args.analysis.read_text())
    decision = json.loads(args.decision.read_text())
    previous_analysis = json.loads(args.previous_analysis.read_text())
    previous_decision = json.loads(args.previous_decision.read_text())
    pressure = json.loads(args.resource_pressure.read_text())
    previous_pressure = json.loads(args.previous_resource_pressure.read_text())
    public_audit = json.loads(args.public_boundary_audit.read_text())
    if (analysis.get('fault_injected') is not False or
            previous_analysis.get('fault_injected') is not False or
            decision.get('status') != 'CLEAN_QUALIFICATION_PASS' or
            previous_decision.get('status') != 'CLEAN_QUALIFICATION_PASS' or
            pressure.get('status') != 'RESOURCE_PRESSURE_PASS' or
            previous_pressure.get('status') != 'RESOURCE_PRESSURE_PASS' or
            pressure.get('run_id') != analysis['run_id'] or
            previous_pressure.get('run_id') != previous_analysis['run_id'] or
            analysis['run_id'] == previous_analysis['run_id'] or
            decision.get('faults_injected') != 0 or
            previous_decision.get('faults_injected') != 0 or
            public_audit.get('status') != 'NO_PRIVATE_LEAK_DETECTED' or
            analysis['run_id'] != decision['run_id'] or
            previous_analysis['run_id'] != previous_decision['run_id'] or
            analysis['schedule']['plan_sha256'] !=
            previous_analysis['schedule']['plan_sha256'] or
            analysis['schedule']['state_machine_sha256'] !=
            previous_analysis['schedule']['state_machine_sha256'] or
            analysis['schedule']['control_generator_sha256'] !=
            previous_analysis['schedule']['control_generator_sha256'] or
            relevant_pods(analysis) != relevant_pods(previous_analysis)):
        raise ValueError('Two independent same-version complete Clean PASS windows are required')
    if (json.loads((args.run / 'load_pair/schedule.json').read_text())['run_id'] !=
            analysis['run_id'] or
            json.loads((args.previous_run / 'load_pair/schedule.json').read_text())['run_id'] !=
            previous_analysis['run_id']):
        raise ValueError('Clean archive paths do not match decisions')
    pods = json.loads((args.run / 'capture-pipeline/captures/before/kubernetes/pods.json').read_text())['items']
    route = json.loads((args.run / 'route-proof.json').read_text())
    if route['status'] != 'ROUTE_FLOW_PROOF_PASS':
        raise ValueError('Current route and packet proof is absent')
    rec = pod_from_snapshot(pods, 'recommendation', 'k8s-worker3')
    if len(rec) != 2:
        raise ValueError('Recommendation does not have two current Ready Worker3 Pods')
    target_rec, sibling_rec = rec
    front_w1 = one(pod_from_snapshot(pods, 'frontend', 'k8s-worker1'), 'Worker1 frontend')
    front_w2 = one(pod_from_snapshot(pods, 'frontend', 'k8s-worker2'), 'Worker2 frontend')
    shipping_w1 = one(pod_from_snapshot(pods, 'shipping', 'k8s-worker1'), 'Worker1 shipping')
    image = one(pod_from_snapshot(pods, 'image-provider', 'k8s-worker2'), 'Worker2 image')
    proxy = one(pod_from_snapshot(pods, 'frontend-proxy', 'k8s-worker2'), 'Worker2 proxy')
    cart = one(pod_from_snapshot(pods, 'cart', 'k8s-worker2'), 'Worker2 cart')
    checkout = one(pod_from_snapshot(pods, 'checkout', 'k8s-worker3'), 'Worker3 checkout')
    current = {row['pair']: row for row in route['rows']}
    path = current.get('checkout_cart_path')
    if (path is None or path['status'] != 'VERIFIED' or
            path['source_pod']['uid'] != checkout['uid'] or
            path['destination_pod']['uid'] != cart['uid']):
        raise ValueError('Captured checkout-to-cart packet does not match frozen UID')

    common = {
        'formal_case': False, 'fault_seconds': 300,
        'load_plan_sha256': sha(ROOT / 'design/load-plan-c3.yaml'),
        'business_impact_rule': 'STRONG if p95>=1.5 or errors+0.05 beyond clean envelope; WEAK positive below; NONE sufficient unchanged; UNMEASURABLE insufficient outcomes',
    }
    product_catalog = one(pod_from_snapshot(pods, 'product-catalog',
                                           'k8s-worker3'), 'Worker3 product catalog')
    controls = [proxy, image, front_w1, front_w2, cart]
    specifications = {
        'SERVICE': {
            'family': 'process_stall', 'target_entity': 'SERVICE/recommendation',
            'target_pods': rec, 'control_pods': controls,
            'node_business_pods': [], 'network_source_pod': None,
            'network_destination_pod': None,
            'intensity': {'mechanism': 'SIGSTOP', 'replicas': 2,
                          'continuous_seconds': 300, 'watchdog_seconds': 305},
            'controls': ['frontend-proxy->image-provider Worker2 local',
                         'frontend Worker1/2->cart Worker2'],
            'direct_evidence': ['replica CPU or process stall signal',
                                'service-attributed client completion/error/timeout'],
            'impact_envelope': impact_envelope(analysis, 'recommendations'),
            'admission_thresholds': {'prefault_business_requests_each_replica_per_minute': 30,
                'direct_cpu_drop_fraction_candidate': 0.5,
                'control_success_and_edge_spans_each_minute': 30,
                'metric_coverage_fraction_each_phase': 0.9},
            'precondition_status': repeated_precondition(
                decision, previous_decision, 'SERVICE_PROCESS_STALL'),
        },
        'POD': {
            'family': 'memory_pressure', 'target_entity': 'POD/otel-demo/' + target_rec['name'],
            'target_pods': [target_rec], 'control_pods': [sibling_rec],
            'node_business_pods': [], 'network_source_pod': None,
            'network_destination_pod': None,
            'intensity': {'max_cgroup_headroom_fraction': 0.55,
                          'max_cgroup_limit_fraction': 0.80,
                          'minimum_allocated_bytes': 33554432,
                          'watchdog_seconds': 305},
            'controls': ['same-service sibling Pod UID'],
            'direct_evidence': ['target container_memory_working_set_bytes increase',
                                'same-service sibling and node memory comparison'],
            'impact_envelope': impact_envelope(analysis, 'recommendations'),
            'admission_thresholds': {'prefault_business_requests_each_pod_per_minute': 30,
                'target_memory_increase_bytes_candidate': 134217728,
                'all_five_pod_cpu_memory_cfs_series_coverage_fraction': 0.9},
            'precondition_status': repeated_precondition(
                decision, previous_decision, 'POD_RECOMMENDATION_MEMORY'),
        },
        'NODE': {
            'family': 'cpu_contention', 'target_entity': 'NODE/k8s-worker1',
            'target_pods': [], 'control_pods': [proxy, image, front_w2,
                                               product_catalog],
            'node_business_pods': [front_w1, shipping_w1],
            'network_source_pod': None, 'network_destination_pod': None,
            'intensity': {'cpu_workers': 6, 'node_cpu_cores': 8,
                          'watchdog_seconds': 305, 'node_ready_during': True},
            'controls': ['Worker2 node', 'Worker3 node',
                         'frontend-proxy->image-provider Worker2 local'],
            'direct_evidence': ['k8s_node_cpu_usage increase',
                                'same-node multiple business Pod symptoms',
                                'k8s_node_condition_ready full during window'],
            'impact_envelope': impact_envelope(analysis, 'home'),
            'admission_thresholds': {'node_cpu_increase_cores_candidate': 2.0,
                'minimum_active_business_pods': 2,
                'same_node_two_pod_p95_ratio_min_candidate': 1.1,
                'singleton_shipping_pod_inference_allowed': True,
                'metric_coverage_fraction_each_phase': 0.9,
                'target_node_ready_during_and_after': True},
            'precondition_status': repeated_precondition(
                decision, previous_decision, 'NODE_CPU_WORKER1'),
        },
        'NETWORK_PATH': {
            'family': 'egress_filtered_netem_loss',
            'target_entity': 'NETWORK_PATH/k8s-worker3/k8s-worker2',
            'target_pods': [], 'control_pods': [proxy, image, front_w2,
                                               front_w1, product_catalog],
            'node_business_pods': [], 'network_source_pod': checkout,
            'network_destination_pod': cart,
            'intensity': {'loss_percent': 10, 'watchdog_seconds': 320,
                          'source_interface': 'ens160'},
            'controls': ['frontend-proxy Worker2->image-provider Worker2',
                         'frontend Worker2->cart Worker2',
                         'frontend Worker1->cart Worker2'],
            'direct_evidence': ['prefault UID+Calico route+business packet TRAVERSES',
                                'receiver UDP loss and independent ICMP loss/RTT'],
            'impact_envelope': impact_envelope(analysis, 'checkout'),
            'admission_thresholds': {'prefault_target_edge_spans_per_minute': 30,
                'during_valid_iperf_samples_min': 54,
                'during_max_consecutive_invalid': 2,
                'fault_median_receiver_loss_fraction_min_candidate': 0.05,
                'fault_mean_icmp_loss_fraction_min_candidate': 0.03,
                'before_after_receiver_loss_fraction_max_candidate': 0.01,
                'control_success_and_edge_spans_each_minute': 30,
                'control_client_p95_ratio_max': 1.25},
            'precondition_status': repeated_precondition(
                decision, previous_decision, 'NETWORK_W3W2_LOSS'),
        },
    }
    rows = []
    for repeat in (0, 1):
        for layer in LAYERS:
            prefix = 'NETWORK' if layer == 'NETWORK_PATH' else layer
            rows.append({'id': f'S5V3AP-{prefix}-S{repeat + 1}',
                         'layer': layer, 'seed': SEEDS[layer][repeat],
                         **common, **specifications[layer]})
    network = [row for row in rows if row['layer'] == 'NETWORK_PATH']
    result = {
        'schema_version': 1, 'status': 'REHEARSAL_REGISTRATION_DRAFT',
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'source_no_fault_analysis_sha256': sha(args.analysis),
        'source_no_fault_decision_sha256': sha(args.decision),
        'source_previous_no_fault_analysis_sha256': sha(args.previous_analysis),
        'source_previous_no_fault_decision_sha256': sha(args.previous_decision),
        'source_resource_pressure_sha256': sha(args.resource_pressure),
        'source_previous_resource_pressure_sha256': sha(args.previous_resource_pressure),
        'clean_evidence_paths': [str(path) for path in (
            args.previous_analysis, args.previous_decision,
            args.previous_resource_pressure, args.analysis, args.decision,
            args.resource_pressure)],
        'source_public_boundary_audit_sha256': sha(args.public_boundary_audit),
        'source_kubernetes_snapshot_sha256': sha(args.run /
            'capture-pipeline/captures/before/kubernetes/pods.json'),
        'source_route_proof_sha256': sha(args.run / 'route-proof.json'),
        'stage5_v3_protocol_frozen': False, 'formal_case_created': False,
        'faults_injected_before_registration': 0,
        'experiments': rows,
        'network_experiments': [
            {'id': row['id'], 'path': row['target_entity'],
             'mechanism': row['family'], 'loss_percent': 10,
             'fault_seconds': row['fault_seconds']} for row in network],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(yaml.safe_dump(result, sort_keys=False) + '\n')
    print(json.dumps({'status': result['status'], 'experiments': len(rows),
                      'eligible': sum(row['precondition_status'] == 'PASS' for row in rows),
                      'sha256': sha(args.output)}))


if __name__ == '__main__':
    main()
