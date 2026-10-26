#!/usr/bin/env python3
"""Evaluate both SF-011 stages after immutable Public export."""

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


def periods(start, during_seconds, before_seconds, after_seconds):
    return {'before': (start - before_seconds, start),
            'during': (start, start + during_seconds),
            'after': (start + during_seconds,
                      start + during_seconds + after_seconds)}


def main():
    import pyarrow.parquet as pq
    import yaml

    parser = argparse.ArgumentParser()
    for name in ('public', 'private-plan', 'capture-terminal', 'fault-terminal',
                 'header-events', 'fixed-header-events', 'contract', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError('Admission result already exists')
    contract = yaml.safe_load(args.contract.read_text())
    plan = json.loads(args.private_plan.read_text())
    capture = json.loads(args.capture_terminal.read_text())
    fault = json.loads(args.fault_terminal.read_text())
    manifest = json.loads((args.public / 'manifest.json').read_text())
    events = contract['ordered_events']
    if (contract['schema'] != 'SF011_FORWARD_TWO_EVENT_ADMISSION_V1' or
            [event['mechanism'] for event in events] !=
                ['SERVICE_PAUSE_30', 'HTTP_HOME_DELAY_250MS_60'] or
            [event['offset_seconds'] for event in events] != [600, 960] or
            sha(args.contract) != plan['forward_admission_sha256'] or
            manifest['case_id'] != 'SF-011' or plan['id'] != 'SF-011-ATTEMPT-01' or
            capture['status'] != 'SF011_FORMAL_CAPTURE_PASS_PENDING_EXPORT'):
        raise RuntimeError('Public, plan or contract freeze identity mismatch')
    for name, row in manifest['files'].items():
        if sha(args.public / name) != row['sha256']:
            raise RuntimeError('Public file hash changed before admission')

    t0 = stamp(plan['start_utc'])
    service = events[0]
    http = events[1]
    home_window = periods(t0 + http['offset_seconds'], http['duration_seconds'],
        http['public_home_proxy']['before_window_seconds'],
        http['public_home_proxy']['after_window_seconds'])
    service_window = {name: (t0 + pair[0], t0 + pair[1]) for name, pair in (
        ('before', service['public_recommendation']['before_window_offsets']),
        ('during', service['public_recommendation']['during_window_offsets']),
        ('after', service['public_recommendation']['after_window_offsets']))}
    targets = plan['actions'][0]['targets']
    uids = {target['uid'] for target in targets}
    if len(targets) != 2 or len(uids) != 2 or any(
            target['service'] != 'recommendation' for target in targets):
        raise RuntimeError('Service target scope changed')
    home = defaultdict(dict)
    controls = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
    servers = defaultdict(lambda: defaultdict(set))
    callers = defaultdict(dict)
    for batch in pq.ParquetFile(args.public / 'traces.parquet').iter_batches(
            batch_size=5000, columns=['trace_id', 'service', 'start_time',
                                      'duration_ms', 'attributes', 'resource_attributes']):
        for row in batch.to_pylist():
            at = row['start_time'].timestamp()
            attrs = json.loads(row['attributes'] or '{}')
            kind = attrs.get('span.kind')
            uid = json.loads(row['resource_attributes'] or '{}').get('k8s.pod.uid')
            for stage, windows in (('http', home_window), ('service', service_window)):
                for name, (left, right) in windows.items():
                    if not left <= at < right:
                        continue
                    if row['service'] == 'frontend-proxy' and kind == 'server':
                        agent = attrs.get('user_agent')
                        path = urllib.parse.urlparse(attrs.get('http.url') or '').path
                        if stage == 'http' and agent in ('business-workload/1.0','business-control/1.0') and path == '/':
                            home[name][row['trace_id']] = max(row['duration_ms'],
                                home[name].get(row['trace_id'], 0))
                        group = ('image' if agent == 'business-control/1.0' and
                                 path.startswith('/images/products/') else
                                 'cart' if agent == 'business-control/1.0' and
                                 path == '/api/cart' else None)
                        if group:
                            existing = controls[stage][group][name].get(row['trace_id'], 0)
                            controls[stage][group][name][row['trace_id']] = max(
                                row['duration_ms'], existing)
                    if stage == 'service':
                        if row['service'] == 'recommendation' and uid in uids and kind == 'server':
                            servers[uid][name].add(row['trace_id'])
                        if (row['service'] == 'frontend' and kind == 'client' and
                                attrs.get('net.peer.name') == 'recommendation' and
                                attrs.get('rpc.method') == 'ListRecommendations'):
                            failed = (str(attrs.get('otel.status_code', '')).upper() == 'ERROR' or
                                      int(attrs.get('rpc.grpc.status_code', 0)) != 0)
                            callers[name][row['trace_id']] = failed or bool(
                                callers[name].get(row['trace_id']))

    checks = {}
    observed_home = {name: {'trace_ids': len(home[name]),
                     'p95_ms': p95(home[name].values())} for name in home_window}
    effect = http['public_home_proxy']
    checks['home_samples'] = all(observed_home[name]['trace_ids'] >= minimum
        for name, minimum in effect['minimum_trace_ids'].items())
    before = observed_home['before']['p95_ms']
    during = observed_home['during']['p95_ms']
    after = observed_home['after']['p95_ms']
    checks['home_latency_effect'] = (before is not None and during is not None and
        during >= before + effect['minimum_p95_increase_ms'] and
        during >= before * effect['minimum_p95_ratio'])
    checks['home_latency_recovery'] = (before is not None and after is not None and
        after <= max(before * effect['recovery_max_p95_ratio'],
                     before + effect['recovery_max_absolute_increase_ms']))
    header_rows = [json.loads(line) for path in (args.header_events,args.fixed_header_events)
                   for line in path.read_text().splitlines()]
    fixed_header_rows = [json.loads(line) for line in args.fixed_header_events.read_text().splitlines()]
    marked = [row for row in header_rows if row['delay_header_applied']]
    hs, he = home_window['during']
    checks['http_bounded_execution'] = (
        len(marked) >= http['private_minimum_delayed_home_requests'] and
        sum(row['delay_header_applied'] for row in fixed_header_rows) >=
            http['private_minimum_fixed_stream_delayed_home_requests'] and
        len(marked) == fault['stages']['http_delay']['delayed_home_requests'] and
        all(hs <= stamp(row['at_utc']) < he and row['requested_delay_ms'] == 250
            for row in marked) and
        all(row['delay_header_applied'] for row in header_rows
            if hs <= stamp(row['at_utc']) < he) and
        fault['stages'].get('http_recovered') is True)

    public_service = service['public_recommendation']
    for uid in uids:
        checks[uid + '_before_coverage'] = len(servers[uid]['before']) >= \
            public_service['minimum_distinct_server_traces_per_pod_before']
        checks[uid + '_after_coverage'] = len(servers[uid]['after']) >= \
            public_service['minimum_distinct_server_traces_per_pod_after']
    checks['service_caller_samples'] = len(callers['during']) >= \
        public_service['minimum_caller_spans_during']
    checks['service_caller_failures'] = sum(callers['during'].values()) >= \
        public_service['minimum_failed_caller_spans_during']
    checks['service_clean_baseline'] = bool(callers['before']) and \
        sum(callers['before'].values()) / len(callers['before']) <= \
        public_service['maximum_failed_caller_fraction_before']
    injected = fault.get('faults', {}).get(plan['id'] + '-SERVICE', {})
    checks['service_bounded_execution'] = (
        fault['status'] == 'SF011_FAULT_SEQUENCE_COMPLETE_PENDING_CAPTURE' and
        fault['stages'].get('service_recovered') is True and
        abs(injected.get('duration_seconds', 0) - 30) <=
            service['duration_tolerance_seconds'] and
        injected.get('cleanup', {}).get('status') ==
            'COMPETITION_CLEANUP_ACTIONS_VERIFIED' and
        fault.get('actual_onsets', {}).get(plan['id'] + '-SERVICE', {}).get('pass') is True)
    topo = json.loads((args.public / 'topology.json').read_text())['snapshots']
    checks['public_uid_continuity'] = all(all(len([pod for pod in snap['pods']
        if pod['uid'] == target['uid'] and pod['node'] == target['node'] and
           all(item['restart_count'] == 0 for item in pod['containers'])]) == 1
        for target in targets) for snap in topo)
    normal = contract['normal_controls']
    observed_controls = {}
    for stage, windows in (('http', home_window), ('service', service_window)):
        observed_controls[stage] = {}
        for group in ('image', 'cart'):
            rows = controls[stage][group]
            observed_controls[stage][group] = {name: {'trace_ids': len(rows[name]),
                'p95_ms': p95(rows[name].values())} for name in windows}
            periods_seen = observed_controls[stage][group]
            before_min = normal[group + '_requests_per_120s_window']
            during_min = (normal[group + '_requests_per_120s_window'] // 2
                if stage == 'http' else normal[group + '_requests_per_30s_window'])
            checks[stage + '_' + group + '_coverage'] = (
                periods_seen['before']['trace_ids'] >= before_min and
                periods_seen['during']['trace_ids'] >= during_min and
                periods_seen['after']['trace_ids'] >= before_min)
            baseline = periods_seen['before']['p95_ms']
            checks[stage + '_' + group + '_latency'] = (
                baseline is not None and baseline > 0 and
                periods_seen['during']['p95_ms'] is not None and
                periods_seen['after']['p95_ms'] is not None and
                periods_seen['during']['p95_ms'] <= baseline *
                    normal['maximum_during_to_before_client_p95_ratio'] and
                periods_seen['after']['p95_ms'] <= baseline *
                    normal['maximum_after_to_before_client_p95_ratio'])
    checks['complete_public_capture'] = (
        capture.get('all_chunks_pass') is True and
        capture.get('public_window_proxy_matched') == capture.get('completed_requests'))
    result = {'status': 'SF011_FORWARD_ADMISSION_PASS' if all(checks.values())
              else 'SF011_FORWARD_ADMISSION_FAILED',
              'case_id': 'SF-011', 'run_id': plan['id'], 'checks': checks,
              'home_proxy': observed_home,
              'recommendation_server_trace_ids': {uid: {
                  name: len(servers[uid][name]) for name in service_window} for uid in uids},
              'recommendation_callers': {name: {'traces': len(callers[name]),
                  'failed': sum(callers[name].values())} for name in service_window},
              'normal_controls': observed_controls,
              'public_manifest_sha256': sha(args.public / 'manifest.json'),
              'contract_sha256': sha(args.contract),
              'private_evidence_never_exported': True}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': result['status'], 'checks': checks}))
    if result['status'] != 'SF011_FORWARD_ADMISSION_PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
