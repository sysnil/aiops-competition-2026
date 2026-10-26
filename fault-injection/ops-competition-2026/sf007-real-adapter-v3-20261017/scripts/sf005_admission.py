#!/usr/bin/env python3
"""Evaluate frozen SF-007 Public evidence against its preregistered service contract."""

import argparse
from collections import defaultdict
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import urllib.parse


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def p95(values):
    values = sorted(values)
    return values[math.ceil(.95 * len(values)) - 1] if values else None


def main():
    import pyarrow.parquet as pq
    import yaml

    parser = argparse.ArgumentParser()
    for name in ('public', 'private-plan', 'capture-terminal', 'fault-terminal',
                 'contract', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError('Admission result already exists')
    contract = yaml.safe_load(args.contract.read_text())
    plan = json.loads(args.private_plan.read_text())
    capture = json.loads(args.capture_terminal.read_text())
    fault = json.loads(args.fault_terminal.read_text())
    manifest = json.loads((args.public / 'manifest.json').read_text())
    if (contract['schema'] != 'SF007_SERVICE_PAUSE30_ADMISSION_V1' or
            manifest['case_id'] != 'SF-007' or plan['id'] != 'SF-007-ATTEMPT-03' or
            sha(args.contract) != plan['service_admission_sha256'] or
            capture['status'] != 'SF007_FORMAL_CAPTURE_PASS_PENDING_EXPORT'):
        raise RuntimeError('Frozen admission input identity or contract mismatch')
    for name, entry in manifest['files'].items():
        if sha(args.public / name) != entry['sha256']:
            raise RuntimeError('Public file hash changed before admission')

    t0 = stamp(plan['start_utc'])
    windows = {name: (t0 + pair[0], t0 + pair[1])
               for name, pair in contract['window_seconds'].items()}
    targets = plan['actions'][0]['targets']
    uids = {target['uid'] for target in targets}
    if len(targets) != 2 or len(uids) != 2 or any(
            target['service'] != 'recommendation' for target in targets):
        raise RuntimeError('Registered service-wide targets changed')

    servers = defaultdict(lambda: defaultdict(set))
    callers = defaultdict(dict)
    controls = defaultdict(lambda: defaultdict(dict))
    for batch in pq.ParquetFile(args.public / 'traces.parquet').iter_batches(
            batch_size=5000, columns=['trace_id', 'service', 'operation_name',
                                      'start_time', 'duration_ms', 'attributes',
                                      'resource_attributes']):
        for row in batch.to_pylist():
            at = row['start_time'].timestamp()
            periods = [name for name, (left, right) in windows.items()
                       if left <= at < right]
            if not periods:
                continue
            attrs = json.loads(row['attributes'] or '{}')
            kind = attrs.get('span.kind')
            service = row['service']
            uid = json.loads(row['resource_attributes'] or '{}').get('k8s.pod.uid')
            for period in periods:
                if service == 'recommendation' and uid in uids and kind == 'server':
                    servers[uid][period].add(row['trace_id'])
                if (service == 'frontend' and kind == 'client' and
                        attrs.get('net.peer.name') == 'recommendation' and
                        attrs.get('rpc.method') == 'ListRecommendations'):
                    failed = (str(attrs.get('otel.status_code', '')).upper() == 'ERROR' or
                              int(attrs.get('rpc.grpc.status_code', 0)) != 0)
                    old = callers[period].get(row['trace_id'])
                    callers[period][row['trace_id']] = failed or bool(old)
                if service == 'frontend-proxy' and kind == 'server':
                    agent = attrs.get('user_agent')
                    path = urllib.parse.urlparse(attrs.get('http.url') or '').path
                    group = ('image' if agent == 'business-control/1.0' and
                             path.startswith('/images/products/') else
                             'cart' if agent == 'business-control/1.0' and
                             path == '/api/cart' else None)
                    if group:
                        previous = controls[group][period].get(row['trace_id'], 0)
                        controls[group][period][row['trace_id']] = max(
                            row['duration_ms'], previous)

    evidence = contract['public_root_evidence']
    checks = {}
    for uid in uids:
        checks[uid + '_before_business_coverage'] = len(servers[uid]['before']) >= \
            evidence['minimum_distinct_recommendation_traces_per_pod_before']
        checks[uid + '_after_business_coverage'] = len(servers[uid]['after']) >= \
            evidence['minimum_distinct_recommendation_traces_per_pod_after']
    during = callers['during']
    before = callers['before']
    checks['public_caller_samples_during'] = len(during) >= \
        evidence['minimum_recommendation_caller_spans_during']
    checks['public_caller_failures_during'] = sum(during.values()) >= \
        evidence['minimum_failed_recommendation_caller_spans_during']
    checks['clean_caller_baseline'] = bool(before) and sum(before.values()) / len(before) <= \
        evidence['maximum_failed_recommendation_caller_fraction_before']

    normal = contract['normal_controls']
    observed = {}
    for group in ('image', 'cart'):
        rows = controls[group]
        observed[group] = {period: {'trace_ids': len(rows[period]),
                           'p95_ms': p95(rows[period].values())}
                           for period in windows}
        checks[group + '_normal_coverage'] = all(
            observed[group][period]['trace_ids'] >= minimum
            for period, minimum in zip(('before', 'during', 'after'),
                                       normal[group + '_requests_before_during_after']))
        baseline = observed[group]['before']['p95_ms']
        checks[group + '_normal_latency'] = (
            baseline is not None and baseline > 0 and
            observed[group]['during']['p95_ms'] is not None and
            observed[group]['after']['p95_ms'] is not None and
            observed[group]['during']['p95_ms'] <= baseline *
                normal['maximum_during_to_before_client_p95_ratio'] and
            observed[group]['after']['p95_ms'] <= baseline *
                normal['maximum_after_to_before_client_p95_ratio'])

    snapshots = json.loads((args.public / 'topology.json').read_text())['snapshots']
    checks['public_uid_continuity'] = all(
        all(len([pod for pod in snapshot['pods'] if
                 pod['uid'] == target['uid'] and pod['name'] == target['name'] and
                 pod['node'] == target['node'] and all(
                     container['restart_count'] == 0 for container in pod['containers'])]) == 1
            for target in targets) for snapshot in snapshots)
    injection = fault.get('faults', {}).get(plan['id'] + '-SERVICE', {})
    checks['bounded_injection_and_cleanup'] = (
        fault['status'] == 'SF007_FAULT_SEQUENCE_COMPLETE_PENDING_CAPTURE' and
        fault.get('stages', {}).get('service_recovered') is True and
        abs(injection.get('duration_seconds', 0) - 30) <=
            contract['injection']['duration_tolerance_seconds'] and
        injection.get('cleanup', {}).get('status') ==
            'COMPETITION_CLEANUP_ACTIONS_VERIFIED' and
        fault.get('actual_onsets', {}).get(plan['id'] + '-SERVICE', {}).get('pass') is True)
    checks['complete_public_capture'] = (
        capture.get('all_chunks_pass') is True and
        capture.get('public_window_proxy_matched') == capture.get('completed_requests') and
        capture.get('business_error_requests') is not None)
    result = {'status': 'SF007_SERVICE_ADMISSION_PASS' if all(checks.values())
              else 'SF007_SERVICE_ADMISSION_FAILED',
              'case_id': 'SF-007', 'run_id': plan['id'], 'checks': checks,
              'recommendation_server_trace_ids': {
                  uid: {name: len(servers[uid][name]) for name in windows} for uid in uids},
              'recommendation_caller': {name: {'traces': len(callers[name]),
                  'failed': sum(callers[name].values())} for name in windows},
              'normal_controls': observed,
              'public_manifest_sha256': sha(args.public / 'manifest.json'),
              'admission_contract_sha256': sha(args.contract),
              'private_evidence_never_exported': True}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': result['status'], 'checks': checks}))
    if result['status'] != 'SF007_SERVICE_ADMISSION_PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
