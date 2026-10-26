#!/usr/bin/env python3
"""Evaluate frozen Public evidence for the Pod-memory then HTTP case."""

import argparse
from collections import defaultdict
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import statistics
from urllib.parse import urlparse


def sha(path):
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024**2), b''):
            checksum.update(block)
    return checksum.hexdigest()


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def median(values):
    return statistics.median(values) if values else None


def p95(values):
    ordered = sorted(values)
    return ordered[math.ceil(.95 * len(ordered)) - 1] if ordered else None


def longest_consecutive(times, maximum_gap_seconds=30):
    longest = current = 0
    previous = None
    for value in sorted(set(times)):
        current = current + 1 if previous is not None and 0 < value-previous <= maximum_gap_seconds else 1
        longest = max(longest, current)
        previous = value
    return longest


def interval(moment, windows):
    return next((label for label,(left,right) in windows.items() if left <= moment < right), None)


def verify_formal_public_window(manifest, run_id):
    if (manifest.get('case_id') != 'SF-008' or
            manifest.get('window',{}).get('run_id') != run_id or
            manifest['window'].get('formal_case') is not True):
        raise RuntimeError('Formal SF-008 Public window required')


def evaluate(args):
    import pyarrow.parquet as pq
    import yaml

    if args.output.exists():
        raise RuntimeError('Admission result already exists')
    pod_contract = yaml.safe_load(args.pod_contract.read_text())
    http_contract = yaml.safe_load(args.http_contract.read_text())
    plan = json.loads(args.private_plan.read_text())
    capture = json.loads(args.capture_terminal.read_text())
    fault = json.loads(args.fault_terminal.read_text())
    manifest = json.loads((args.public/'manifest.json').read_text())
    run_id = 'SF-008-ATTEMPT-05'
    verify_formal_public_window(manifest, run_id)
    if (plan['id'] != run_id or
            pod_contract['case_id'] != 'SF-008' or http_contract['case_id'] != 'SF-008' or
            sha(args.pod_contract) != plan['pod_admission_sha256'] or
            sha(args.http_contract) != plan['http_admission_sha256'] or
            capture['status'] != 'SF008_FORMAL_CAPTURE_PASS_PENDING_EXPORT'):
        raise RuntimeError('Frozen plan, contracts, capture or Public identity mismatch')
    for name,item in manifest['files'].items():
        if sha(args.public/name) != item['sha256']:
            raise RuntimeError('Public file changed after freeze: ' + name)
    t0 = stamp(plan['start_utc'])
    action = plan['actions'][0]
    if (action['mechanism_id'] != 'POD' or action['worker'] != 'k8s-worker2' or
            action['offset_seconds'] != 600 or action['duration_seconds'] != 60 or
            plan['request_delay'] != {'operation': 'home', 'offset_seconds': 960,
                'duration_seconds': 60, 'delay_ms': 250,
                'lease_path': str(Path(plan['control_root'])/'http-reqdelay-lease.json')}):
        raise RuntimeError('Fault mechanisms, order or no-overlap gap changed')
    target = action['targets'][0]
    siblings = [row for row in plan['targets_and_siblings']
                if row['service'] == 'frontend' and row['uid'] != target['uid']]
    if len(siblings) != 1:
        raise RuntimeError('Single normal Frontend sibling required')
    sibling = siblings[0]
    pod_windows = {'before': (t0+300,t0+600),
                   'during': (t0+600,t0+660),
                   'after': (t0+720,t0+900)}
    http_windows = {'before': (t0+840,t0+960),
                    'during': (t0+960,t0+1020),
                    'after': (t0+1020,t0+1140)}
    memory = defaultdict(lambda: defaultdict(list))
    for batch in pq.ParquetFile(args.public/'metrics.parquet').iter_batches(
            batch_size=5000, columns=['timestamp','metric_name','value','labels']):
        for row in batch.to_pylist():
            if row['metric_name'] != pod_contract['public_root_evidence']['metric']:
                continue
            labels = json.loads(row['labels'])
            for entity in (target,sibling):
                if (labels.get('pod') != entity['name'] or
                        entity['uid'].replace('-','_') not in labels.get('id','')):
                    continue
                label = interval(row['timestamp'].timestamp(), pod_windows)
                if label:
                    memory[entity['uid']][label].append((row['timestamp'].timestamp(),row['value']))
    checks = {}
    evidence = pod_contract['public_root_evidence']
    pod_values = {}
    for entity in (target,sibling):
        rows = memory[entity['uid']]
        pod_values[entity['uid']] = {label: {'samples': len(rows[label]),
            'median_bytes': median([value for _,value in rows[label]])}
            for label in pod_windows}
        checks[entity['uid']+'_memory_coverage'] = all(
            len(rows[label]) >= evidence[label+'_min_samples'] for label in pod_windows)
    baseline = [value for _,value in memory[target['uid']]['before']]
    center = median(baseline)
    mad = median([abs(value-center) for value in baseline]) if baseline else None
    threshold = max(evidence['minimum_target_increase_bytes'],
                    evidence['baseline_noise_multiplier_mad']*mad) if mad is not None else None
    abnormal = [time for time,value in memory[target['uid']]['during']
                if threshold is not None and value >= center+threshold]
    checks['pod_direct_memory_signal'] = threshold is not None and longest_consecutive(abnormal) >= evidence['consecutive_abnormal_samples']
    after = pod_values[target['uid']]['after']['median_bytes']
    sibling_before = pod_values[sibling['uid']]['before']['median_bytes']
    sibling_during = pod_values[sibling['uid']]['during']['median_bytes']
    checks['pod_memory_recovery'] = center is not None and after is not None and abs(after-center) <= evidence['recovery_max_difference_from_baseline_bytes']
    checks['normal_replica_stability'] = (sibling_before is not None and sibling_during is not None and
        abs(sibling_during-sibling_before) <= evidence['sibling_max_median_increase_bytes'])

    ordinary = defaultdict(lambda: defaultdict(set))
    proxy = defaultdict(lambda: defaultdict(dict))
    for batch in pq.ParquetFile(args.public/'traces.parquet').iter_batches(
            batch_size=5000, columns=['start_time','service','trace_id','duration_ms',
                                      'attributes','resource_attributes']):
        for row in batch.to_pylist():
            at = row['start_time'].timestamp()
            if row['service'] == 'frontend':
                label = interval(at, pod_windows)
                if not label:
                    continue
                attrs = json.loads(row['attributes'] or '{}')
                resource = json.loads(row['resource_attributes'] or '{}')
                uid = resource.get('k8s.pod.uid')
                if (uid in (target['uid'],sibling['uid']) and
                        attrs.get('span.kind') == 'server' and
                        attrs.get('http.user_agent') in
                        ('business-workload/1.0','business-control/1.0')):
                    ordinary[uid][label].add(row['trace_id'])
            elif row['service'] == 'frontend-proxy':
                label = interval(at, http_windows)
                if not label:
                    continue
                attrs = json.loads(row['attributes'] or '{}')
                if attrs.get('span.kind') != 'server':
                    continue
                agent = attrs.get('user_agent')
                path = urlparse(attrs.get('http.url') or '').path
                if agent in ('business-workload/1.0','business-control/1.0') and path == '/':
                    group = 'home'
                elif agent == 'business-control/1.0' and path == '/api/cart':
                    group = 'cart'
                elif agent == 'business-control/1.0' and path.startswith('/images/products/'):
                    group = 'image'
                else:
                    continue
                previous = proxy[group][label].get(row['trace_id'],0)
                proxy[group][label][row['trace_id']] = max(previous,row['duration_ms'])
    for entity in (target,sibling):
        uid = entity['uid']
        checks[uid+'_ordinary_business_coverage'] = all(
            len(ordinary[uid][label]) >= pod_contract['ordinary_business'][
                'minimum_frontend_trace_ids_per_replica_'+label]
            for label in pod_windows)
    topology = json.loads((args.public/'topology.json').read_text())
    for entity in (target,sibling):
        checks[entity['uid']+'_identity_continuity'] = all(
            len([pod for pod in snapshot['pods'] if pod['uid']==entity['uid'] and
                 pod['name']==entity['name'] and pod['node']==entity['node'] and
                 all(container['restart_count']==0 for container in pod['containers'])])==1
            for snapshot in topology['snapshots'])
    observed = {name: {label: {'trace_ids': len(proxy[name][label]),
        'p95_ms': p95(proxy[name][label].values())} for label in http_windows}
        for name in ('home','image','cart')}
    home = observed['home']
    effect = http_contract['public_effect']
    checks['home_samples'] = all(home[label]['trace_ids'] >= effect['minimum_'+label+'_trace_ids']
                                 for label in http_windows)
    before_p95 = home['before']['p95_ms']
    during_p95 = home['during']['p95_ms']
    after_p95 = home['after']['p95_ms']
    checks['home_latency_effect'] = (before_p95 is not None and during_p95 is not None and
        during_p95 >= before_p95 + effect['minimum_p95_increase_ms'] and
        during_p95 >= before_p95 * effect['minimum_p95_ratio'])
    checks['home_latency_recovery'] = (before_p95 is not None and after_p95 is not None and
        after_p95 <= max(before_p95*effect['recovery_max_p95_ratio'],
                         before_p95+effect['recovery_max_absolute_increase_ms']))
    controls = http_contract['normal_controls']
    for name in ('image','cart'):
        rows = observed[name]
        minimum = controls['require_'+name+'_successes_before_after']
        checks[name+'_coverage'] = (rows['before']['trace_ids'] >= minimum and
            rows['during']['trace_ids'] >= controls['require_'+name+'_successes_during'] and
            rows['after']['trace_ids'] >= minimum)
        baseline_p95 = rows['before']['p95_ms']
        checks[name+'_latency'] = (baseline_p95 is not None and
            all(rows[label]['p95_ms'] is not None and rows[label]['p95_ms'] <=
                baseline_p95 * controls['maximum_'+label+'_to_before_p95_ratio']
                for label in ('during','after')))
    events = [json.loads(line) for path in (args.header_events,args.fixed_header_events)
              for line in path.read_text().splitlines()]
    fixed_events = [json.loads(line) for line in args.fixed_header_events.read_text().splitlines()]
    marked = [row for row in events if row['delay_header_applied']]
    header_start,header_end = http_windows['during']
    checks['http_bounded_execution'] = (
        len(marked) >= http_contract['private_execution']['minimum_delayed_home_requests'] and
        sum(row['delay_header_applied'] for row in fixed_events) >=
            http_contract['private_execution']['minimum_fixed_stream_delayed_home_requests'] and
        len(marked) == fault['stages']['http_delay']['delayed_home_requests'] and
        all(header_start <= stamp(row['at_utc']) < header_end and
            row['requested_delay_ms'] == 250 for row in marked) and
        all(row['delay_header_applied'] for row in events
            if header_start <= stamp(row['at_utc']) < header_end))
    injection = fault.get('faults',{}).get(run_id+'-POD',{})
    checks['pod_bounded_execution_and_cleanup'] = (
        fault['status']=='SF008_FAULT_SEQUENCE_COMPLETE_PENDING_CAPTURE' and
        abs(injection.get('duration_seconds',0)-60) <= 2 and
        injection.get('cleanup',{}).get('status')=='COMPETITION_CLEANUP_ACTIONS_VERIFIED' and
        fault['stages'].get('pod_recovered') is True and
        action['offset_seconds']+action['duration_seconds'] < plan['request_delay']['offset_seconds'])
    checks['complete_public_capture'] = (
        manifest['private_leak_scan']=='PASS' and
        all(len(manifest['minute_counts'][signal])==30 and
            all(int(value)>0 for value in manifest['minute_counts'][signal].values())
            for signal in ('metrics','logs','traces')))
    result = {'status': 'SF008_POD_HTTP_ADMISSION_PASS' if all(checks.values())
              else 'SF008_POD_HTTP_ADMISSION_FAILED',
              'case_id': 'SF-008', 'run_id': run_id, 'checks': checks,
              'pod_target_uid': target['uid'], 'pod_normal_uid': sibling['uid'],
              'pod_memory_windows': pod_values, 'pod_baseline_mad_bytes': mad,
              'pod_registered_increase_threshold_bytes': threshold,
              'pod_longest_abnormal_run': longest_consecutive(abnormal),
              'pod_normal_trace_counts': {uid: {label:len(ids) for label,ids in periods.items()}
                   for uid,periods in ordinary.items()},
              'http_proxy_windows': observed, 'http_delayed_requests': len(marked),
              'public_manifest_sha256': sha(args.public/'manifest.json'),
              'pod_contract_sha256': sha(args.pod_contract),
              'http_contract_sha256': sha(args.http_contract),
              'private_evidence_never_exported': True}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': result['status'], 'checks': checks}))
    if result['status'] != 'SF008_POD_HTTP_ADMISSION_PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for key in ('public','private-plan','capture-terminal','fault-terminal',
                'header-events','fixed-header-events','pod-contract','http-contract','output'):
        parser.add_argument('--'+key, required=True, type=Path)
    evaluate(parser.parse_args())
