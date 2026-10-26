#!/usr/bin/env python3
"""Gate Phase 4 fault attempts on one complete no-fault 10+5 workload cycle."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import yaml


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--controls', type=Path, required=True)
    parser.add_argument('--probes', type=Path, required=True)
    parser.add_argument('--resource', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refusing overwrite')
    plan = yaml.safe_load(args.plan.read_text())
    schedule = read(args.run / 'load_pair/schedule.json')
    lifecycle = read(args.run / 'result.json')
    load = read(args.run / 'load_pair/result.json')
    capture = read(args.run / 'capture-pipeline/pipeline-result.json')
    route = read(args.run / 'route-proof.json')
    target = read(args.target)
    controls = read(args.controls)
    probes = read(args.probes)
    resource = read(args.resource)
    blockers = []
    if schedule['plan_sha256'] != sha(args.plan) or lifecycle['status'] != 'CLEAN_LIFECYCLE_PASS':
        blockers.append('WORKLOAD_VERSION_OR_LIFECYCLE')
    if load['status'] != 'LOAD_PAIR_COMPLETE' or capture['status'] != 'CAPTURE_PIPELINE_PASS':
        blockers.append('LOAD_OR_LIVE_CAPTURE')
    if route['status'] != 'ROUTE_FLOW_PROOF_PASS' or len(route['rows']) != 4:
        blockers.append('ROUTE_FLOW_PROOF')
    counts = [row['verified_call_edge_trace_ids'] for row in target['minutes']]
    minimum = plan['network_path']['minimum_valid_target_edge_spans_per_60s']
    if len(counts) != 5 or min(counts) < minimum:
        blockers.append('TARGET_EDGE_COVERAGE')
    if any(row['checkout_business_errors'] for row in target['minutes']):
        blockers.append('NORMAL_CHECKOUT_ERRORS')
    normal = {}
    for stream in ('image_asset', 'cart_view'):
        status = controls['stability'][stream]
        bins = [row for row in controls['bins'] if row['stream'] == stream]
        good = (status['all_five_request_bins_pass'] and
                status['all_five_trace_bins_pass'] and status['within_1_25'] and
                len(bins) == 5 and all(row['errors'] == 0 for row in bins))
        normal[stream] = good
        if not good:
            blockers.append('CONTROL_' + stream.upper())
    probe_ok = True
    for phase, expected in (('before', 24), ('during', 12), ('after', 24)):
        row = probes['phases'][phase]
        if (row['planned'] != expected or row['valid_iperf'] != expected or
                row['timeout'] or row['schedule_miss'] or
                row.get('control_connection_failure', 0) or
                row['median_udp_loss_fraction'] is None or
                row['median_udp_loss_fraction'] >
                plan['network_path']['maximum_before_after_probe_udp_loss_fraction']):
            probe_ok = False
    if not probe_ok:
        blockers.append('NO_FAULT_FULL_PROBE_WINDOWS')
    cpu_ok = resource['conservative_combined_cpu_delta_below_0_25_cores_per_worker']
    if not cpu_ok:
        blockers.append('NORMAL_RESOURCE_PRESSURE')
    result = {'status': 'PHASE4_NO_FAULT_READINESS_PASS' if not blockers else
              'PHASE4_NO_FAULT_READINESS_BLOCKED',
              'at_utc': datetime.now(timezone.utc).isoformat(),
              'run_id': schedule['run_id'], 'plan_sha256': sha(args.plan),
              'source_sha256': {str(path): sha(path) for path in
                 (args.target, args.controls, args.probes, args.resource,
                  args.run / 'route-proof.json',
                  args.run / 'capture-pipeline/pipeline-result.json')},
              'target_edge_spans_per_minute': counts,
              'controls_qualified': all(normal.values()),
              'probe_windows_complete': probe_ok,
              'workload_health_pass': not any(key in blockers for key in
                  ('LOAD_OR_LIVE_CAPTURE', 'NORMAL_CHECKOUT_ERRORS',
                   'NORMAL_RESOURCE_PRESSURE')),
              'blockers': blockers, 'faults_injected': 0,
              'formal_cases_created': 0}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'status': result['status'], 'counts': counts,
                      'blockers': blockers}))
    if blockers:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
