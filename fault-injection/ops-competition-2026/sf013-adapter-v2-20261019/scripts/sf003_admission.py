#!/usr/bin/env python3
"""Evaluate registered Pod direct evidence after the complete public freeze."""

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import statistics
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def median(values):
    return statistics.median(values) if values else None


def longest_consecutive(times, maximum_gap_seconds=30):
    longest = current = 0
    previous = None
    for value in sorted(times):
        if value == previous:
            continue
        current = (current + 1 if previous is not None and
                   0 < value - previous <= maximum_gap_seconds else 1)
        longest = max(longest, current)
        previous = value
    return longest


def main():
    import pyarrow as pa
    import pyarrow.parquet as pq
    import yaml

    parser = argparse.ArgumentParser()
    for key in ('public', 'private-plan', 'capture-terminal', 'fault-terminal',
                'contract', 'output'):
        parser.add_argument('--' + key, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError('Pod admission output already exists')
    contract = yaml.safe_load(args.contract.read_text())
    plan = json.loads(args.private_plan.read_text())
    capture = json.loads(args.capture_terminal.read_text())
    fault = json.loads(args.fault_terminal.read_text())
    manifest = json.loads((args.public / 'manifest.json').read_text())
    if (manifest['case_id'] != 'SF-013' or plan['id'] != 'SF-013-ATTEMPT-02' or
            sha(args.contract) != plan['pod_admission_sha256'] or
            capture['status'] != 'SF013_FORMAL_CAPTURE_PASS_PENDING_EXPORT'):
        raise RuntimeError('Pod admission input identity or freeze mismatch')
    for name, row in manifest['files'].items():
        if sha(args.public / name) != row['sha256']:
            raise RuntimeError('Public source hash mismatch before admission')
    t0 = stamp(plan['start_utc'])
    windows = {'before': (t0 + 300, t0 + 600),
               'during': (t0 + 600, t0 + 900),
               'after': (t0 + 1080, t0 + 1380)}
    target = plan['actions'][0]['targets'][0]
    siblings = [row for row in plan['targets_and_siblings']
                if row['service'] == 'frontend' and row['uid'] != target['uid']]
    if len(siblings) != 1:
        raise RuntimeError('Qualified normal frontend replica missing')
    sibling = siblings[0]
    memory = defaultdict(lambda: defaultdict(list))
    file = args.public / 'metrics.parquet'
    for batch in pq.ParquetFile(file).iter_batches(batch_size=5000,
            columns=['timestamp', 'metric_name', 'value', 'labels']):
        for row in batch.to_pylist():
            if row['metric_name'] != 'container_memory_working_set_bytes':
                continue
            labels = json.loads(row['labels'])
            for entity in (target, sibling):
                if (labels.get('pod') != entity['name'] or
                        entity['uid'].replace('-', '_') not in labels.get('id', '')):
                    continue
                moment = row['timestamp'].timestamp()
                for label, (left, right) in windows.items():
                    if left <= moment < right:
                        memory[entity['uid']][label].append((moment, row['value']))
    evidence = contract['public_root_evidence']
    checks = {}
    values = {}
    for entity in (target, sibling):
        rows = memory[entity['uid']]
        values[entity['uid']] = {key: {'samples': len(rows[key]),
            'median_bytes': median([v for _, v in rows[key]])} for key in windows}
        checks[entity['uid'] + '_metric_coverage'] = (
            len(rows['before']) >= evidence['before_min_samples'] and
            len(rows['during']) >= evidence['during_min_samples'] and
            len(rows['after']) >= evidence['after_min_samples'])
    baseline = [v for _, v in memory[target['uid']]['before']]
    center = median(baseline)
    mad = median([abs(value - center) for value in baseline]) if baseline else None
    threshold = max(evidence['minimum_target_increase_bytes'],
                    evidence['baseline_noise_multiplier_mad'] * mad) if mad is not None else None
    abnormal = [t for t, value in memory[target['uid']]['during']
                if threshold is not None and value >= center + threshold]
    checks['public_target_memory_signal'] = (threshold is not None and
        longest_consecutive(abnormal) >= evidence['consecutive_abnormal_samples'])
    target_after = values[target['uid']]['after']['median_bytes']
    sibling_before = values[sibling['uid']]['before']['median_bytes']
    sibling_during = values[sibling['uid']]['during']['median_bytes']
    checks['public_target_memory_recovery'] = (center is not None and
        target_after is not None and abs(target_after - center) <=
        evidence['recovery_max_difference_from_baseline_bytes'])
    checks['normal_replica_stability'] = (sibling_before is not None and
        sibling_during is not None and abs(sibling_during - sibling_before) <=
        evidence['sibling_max_median_increase_bytes'])
    calls = defaultdict(lambda: defaultdict(set))
    for batch in pq.ParquetFile(args.public / 'traces.parquet').iter_batches(
            batch_size=5000, columns=['start_time', 'service', 'trace_id',
                                      'attributes', 'resource_attributes']):
        for row in batch.to_pylist():
            if row['service'] != 'frontend':
                continue
            attributes = json.loads(row['attributes'] or '{}')
            resource = json.loads(row['resource_attributes'] or '{}')
            uid = resource.get('k8s.pod.uid')
            if (uid not in (target['uid'], sibling['uid']) or
                    attributes.get('span.kind') != 'server' or
                    attributes.get('http.user_agent') not in
                    ('business-workload/1.0', 'business-control/1.0')):
                continue
            moment = row['start_time'].timestamp()
            for label, (left, right) in windows.items():
                if left <= moment < right:
                    calls[uid][label].add(row['trace_id'])
    for entity in (target, sibling):
        uid = entity['uid']
        checks[uid + '_ordinary_business_coverage'] = all(
            len(calls[uid][label]) >= contract['ordinary_business'][
                'minimum_frontend_trace_ids_per_replica_' + label]
            for label in windows)
    topo = json.loads((args.public / 'topology.json').read_text())
    for entity in (target, sibling):
        checks[entity['uid'] + '_identity_continuity'] = all(
            len([pod for pod in snapshot['pods'] if pod['uid'] == entity['uid'] and
                 pod['name'] == entity['name'] and pod['node'] == entity['node'] and
                 all(c['restart_count'] == 0 for c in pod['containers'])]) == 1
            for snapshot in topo['snapshots'])
    injection = fault.get('faults', {}).get(plan['id'] + '-POD', {})
    checks['injection_and_cleanup'] = (
        fault['status'] == 'SF013_FAULT_SEQUENCE_COMPLETE_PENDING_CAPTURE' and
        abs(injection.get('duration_seconds', 0) - 300) <= 2 and
        injection.get('cleanup', {}).get('status') ==
        'COMPETITION_CLEANUP_ACTIONS_VERIFIED' and
        fault.get('stages', {}).get('pod_recovered') is True)
    result = {'status': 'SF013_POD_MEMORY_ADMISSION_PASS' if all(checks.values())
              else 'SF013_POD_MEMORY_ADMISSION_FAILED',
              'case_id': 'SF-013', 'run_id': plan['id'], 'checks': checks,
              'target_uid': target['uid'], 'normal_uid': sibling['uid'],
              'metric_windows': values, 'baseline_mad_bytes': mad,
              'registered_increase_threshold_bytes': threshold,
              'longest_abnormal_run': longest_consecutive(abnormal),
              'normal_trace_counts': {uid: {name: len(ids) for name, ids in periods.items()}
                                      for uid, periods in calls.items()},
              'public_manifest_sha256': sha(args.public / 'manifest.json'),
              'admission_contract_sha256': sha(args.contract),
              'private_evidence_never_exported': True}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': result['status'], 'checks': checks}))
    if result['status'] != 'SF013_POD_MEMORY_ADMISSION_PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
