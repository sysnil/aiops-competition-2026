#!/usr/bin/env python3
"""Public-only Pod identity and baseline telemetry gate before fault injection."""

import argparse
import hashlib
import json
from pathlib import Path


FAMILIES = ('container_cpu_usage_seconds_total',
            'container_memory_working_set_bytes',
            'container_cpu_cfs_periods_total',
            'container_cpu_cfs_throttled_periods_total',
            'container_cpu_cfs_throttled_seconds_total')
MIN_BEFORE_SAMPLES = 20
MIN_DURING_SAMPLES = 10
MAX_MISSING_FRACTION = 0.25


def validate(snapshot):
    if snapshot.get('schema_version') != 1 or snapshot.get('public_only') is not True:
        raise ValueError('Expected public pre-fault snapshot schema v1')
    before = snapshot['before_window']
    duration = before['end_unix_ns'] - before['start_unix_ns']
    if duration < 600 * 1_000_000_000:
        raise ValueError('Pod clean baseline must cover at least 10 minutes')
    interval = snapshot['scrape_interval_seconds']
    if not 0 < interval <= 30:
        raise ValueError('Unsupported scrape interval')
    target = snapshot['target_pod']
    entity = f"POD/{target['namespace']}/{target['name']}"
    if entity not in snapshot['candidate_entities']:
        raise ValueError('Target Pod is absent from pre-fault public candidates')
    if snapshot.get('rollout_pending'):
        raise ValueError('A rollout is pending before the Pod fault')
    observations = snapshot['pod_observations']
    if len(observations) < 2:
        raise ValueError('Need beginning and ending pre-fault Pod observations')
    for item in observations:
        if (item['namespace'], item['name'], item['uid'], item['node'], item['container_id']) != (
                target['namespace'], target['name'], target['uid'], target['node'],
                target['container_id']):
            raise ValueError('Pod identity or placement changed during clean baseline')
        if item.get('ready') is not True:
            raise ValueError('Target Pod was not Ready during clean baseline')
    if observations[0]['observed_unix_ns'] > before['start_unix_ns'] + interval * 1_000_000_000:
        raise ValueError('First Pod observation is too late')
    if observations[-1]['observed_unix_ns'] < before['end_unix_ns'] - interval * 1_000_000_000:
        raise ValueError('Last Pod observation is too early')
    expected = int(duration / (interval * 1_000_000_000)) + 1
    counts = {}
    for family in FAMILIES:
        series = snapshot['metric_series'].get(family)
        if not series or series.get('container_id') != target['container_id']:
            raise ValueError('Missing or misattributed Pod metric family: ' + family)
        samples = series['samples']
        stamps = [item['time_unix_ns'] for item in samples]
        if len(stamps) != len(set(stamps)):
            raise ValueError('Duplicate Pod metric timestamps: ' + family)
        if any(not before['start_unix_ns'] <= stamp <= before['end_unix_ns'] for stamp in stamps):
            raise ValueError('Pod metric outside clean baseline: ' + family)
        if len(samples) < MIN_BEFORE_SAMPLES or (1 - len(samples) / expected) > MAX_MISSING_FRACTION:
            raise ValueError('Insufficient Pod metric samples: ' + family)
        counts[family] = len(samples)
    return {'status': 'POD_CANDIDATE_PRECHECK_PASS', 'candidate': entity,
            'pod_uid': target['uid'], 'node': target['node'],
            'container_id': target['container_id'],
            'baseline_seconds': duration / 1_000_000_000, 'sample_counts': counts,
            'during_uid_status': 'PENDING_POST_FAULT_ADMISSION'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--public-snapshot', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('Refusing to overwrite Pod precheck')
    snapshot = json.loads(args.public_snapshot.read_text())
    result = validate(snapshot)
    result['input_sha256'] = hashlib.sha256(args.public_snapshot.read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(result['status'])


if __name__ == '__main__':
    main()
