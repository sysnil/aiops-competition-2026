#!/usr/bin/env python3
"""Assess final-case injection and public root evidence after Public freeze."""

import argparse
import hashlib
import json
import math
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


OPS = Path('/root/ops-competition-2026')
DATA = Path('/data/stage5-v3/ops-competition-2026')
POLICY = OPS / 'final-case-production-v3-20261020/design/admission-policy-draft.json'


def read(path):
    return json.loads(path.read_text())


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024**2), b''):
            digest.update(block)
    return digest.hexdigest()


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def p95(values):
    values = sorted(values)
    return values[math.ceil(.95 * len(values)) - 1] if values else None


def median(values):
    return statistics.median(values) if values else None


def windows(manifest):
    start = stamp(manifest['start_utc'])
    actions = sorted(manifest['actions'], key=lambda a: stamp(a['planned_start_utc']))
    result = {}
    for index, action in enumerate(actions):
        at = stamp(action['planned_start_utc'])
        end = at + action['duration_seconds']
        before = (start + 300, start + 600) if index == 0 else (at - 120, at)
        after_end = (stamp(actions[index + 1]['planned_start_utc'])
                     if index + 1 < len(actions) else min(end + 420, start + 1800))
        after = (end, after_end) if index + 1 < len(actions) else \
                (end + 120, after_end)
        if min(before[1] - before[0], after[1] - after[0]) < 120:
            raise ValueError('ADMISSION_NORMAL_WINDOWS_TOO_SHORT')
        result[action['action_id']] = {'before': before,
                                       'during': (at, end), 'after': after}
    return result


def period(moment, windows):
    return next((name for name, (left, right) in windows.items()
                 if left <= moment < right), None)


def trace_evidence(public, action_windows, targets):
    import pyarrow.parquet as pq
    data = {action: {'service_servers': defaultdict(lambda: defaultdict(set)),
                     'rec_callers': defaultdict(set),
                     'rec_proxy': defaultdict(set),
                     'home_proxy': defaultdict(dict)}
            for action in action_windows}
    for batch in pq.ParquetFile(public / 'traces.parquet').iter_batches(
            batch_size=5000, columns=['trace_id', 'service', 'start_time',
                                      'duration_ms', 'attributes',
                                      'resource_attributes']):
        for row in batch.to_pylist():
            moment = row['start_time'].timestamp()
            attrs = json.loads(row['attributes'] or '{}')
            resource = json.loads(row['resource_attributes'] or '{}')
            kind = attrs.get('span.kind')
            for action, windows_ in action_windows.items():
                label = period(moment, windows_)
                if label is None:
                    continue
                value = data[action]
                if (row['service'] in ('frontend', 'recommendation') and
                        kind == 'server' and resource.get('k8s.pod.uid')):
                    value['service_servers'][resource['k8s.pod.uid']][label].add(
                        row['trace_id'])
                if (row['service'] == 'frontend' and kind == 'client' and
                        attrs.get('net.peer.name') == 'recommendation' and
                        attrs.get('rpc.method') == 'ListRecommendations'):
                    value['rec_callers'][label].add(row['trace_id'])
                if row['service'] == 'frontend-proxy' and kind == 'server':
                    agent = attrs.get('user_agent') or attrs.get('http.user_agent')
                    path = urlparse(attrs.get('http.url') or '').path
                    if path == '/api/recommendations':
                        value['rec_proxy'][label].add(row['trace_id'])
                    if path == '/' and agent == 'business-workload/1.0':
                        previous = value['home_proxy'][label].get(row['trace_id'], 0)
                        value['home_proxy'][label][row['trace_id']] = max(
                            previous, row['duration_ms'])
    return data


def pod_memory(public, windows_, target, sibling, rule):
    import pyarrow.parquet as pq
    memory = defaultdict(lambda: defaultdict(list))
    for batch in pq.ParquetFile(public / 'metrics.parquet').iter_batches(
            batch_size=5000, columns=['timestamp', 'metric_name', 'value', 'labels']):
        for row in batch.to_pylist():
            if row['metric_name'] != 'container_memory_working_set_bytes':
                continue
            labels = json.loads(row['labels'])
            for entity in (target, sibling):
                if (labels.get('pod') != entity['name'] or
                        entity['uid'].replace('-', '_') not in labels.get('id', '')):
                    continue
                label = period(row['timestamp'].timestamp(), windows_)
                if label:
                    memory[entity['uid']][label].append(
                        (row['timestamp'].timestamp(), row['value']))
    result = {}
    checks = {}
    for entity in (target, sibling):
        uid = entity['uid']
        result[uid] = {label: {'samples': len(memory[uid][label]),
            'median_bytes': median([value for _, value in memory[uid][label]])}
            for label in windows_}
        checks[uid + '_metric_samples'] = all(
            result[uid][label]['samples'] >=
            max(4, round(rule['minimum_samples_per_300s_window'] *
                         (right - left) / 300))
            for label, (left, right) in windows_.items())
    baseline = [value for _, value in memory[target['uid']]['before']]
    center = median(baseline)
    mad = median([abs(value - center) for value in baseline]) if baseline else None
    threshold = max(rule['minimum_target_increase_bytes'],
                    rule['baseline_noise_multiplier_mad'] * mad) if mad is not None else None
    abnormal = [when for when, value in memory[target['uid']]['during']
                if threshold is not None and value >= center + threshold]
    consecutive = longest_consecutive(abnormal)
    target_during = result[target['uid']]['during']['median_bytes']
    sibling_before = result[sibling['uid']]['before']['median_bytes']
    sibling_during = result[sibling['uid']]['during']['median_bytes']
    target_after = result[target['uid']]['after']['median_bytes']
    checks['public_target_memory_signal'] = (
        threshold is not None and
        consecutive >= rule['consecutive_abnormal_samples'] and
        target_during is not None and sibling_before is not None and
        sibling_during is not None and
        (target_during - center) - (sibling_during - sibling_before) >=
        rule['minimum_target_minus_sibling_increase_bytes'])
    checks['public_target_memory_recovery'] = (
        center is not None and target_after is not None and
        abs(target_after - center) <= rule['maximum_recovery_difference_bytes'])
    return checks, {'metric_windows': result, 'baseline_mad_bytes': mad,
                    'increase_threshold_bytes': threshold,
                    'consecutive_abnormal_samples': consecutive,
                    'source': 'public/metrics.parquet'}


def longest_consecutive(times, *, max_gap=30):
    current = longest = 0
    previous = None
    for value in sorted(set(times)):
        current = current + 1 if previous is not None and \
                  0 < value - previous <= max_gap else 1
        longest = max(longest, current)
        previous = value
    return longest


def service_pause(trace, windows_, targets, rule):
    servers = trace['service_servers']
    uids = {item['uid'] for item in targets}
    observed = {uid: {name: len(servers[uid][name]) for name in windows_}
                for uid in uids}
    rates = {name: sum(observed[uid][name] for uid in uids) /
             (windows_[name][1] - windows_[name][0]) for name in windows_}
    before = rates['before']
    checks = {uid + '_business_before_after':
        observed[uid]['before'] >=
        rule['minimum_server_trace_ids_per_pod_before_and_after'] and
        observed[uid]['after'] >=
        rule['minimum_server_trace_ids_per_pod_before_and_after'] for uid in uids}
    checks['public_caller_or_proxy_during'] = (
        len(trace['rec_callers']['during'] |
            trace['rec_proxy']['during']) >=
        rule['minimum_public_caller_or_proxy_spans_during'])
    checks['public_server_completion_drop'] = (
        before > 0 and rates['during'] / before <=
        rule['maximum_during_to_before_server_completion_rate_ratio'])
    checks['public_server_completion_recovered'] = (
        before > 0 and rates['after'] / before >=
        rule['minimum_after_to_before_server_completion_rate_ratio'])
    return checks, {'server_trace_counts_by_uid': observed,
                    'server_completion_rates_per_second': rates,
                    'caller_or_proxy_trace_ids_during':
                        len(trace['rec_callers']['during'] |
                            trace['rec_proxy']['during']),
                    'source': 'public/traces.parquet'}


def http_delay(trace, rule):
    values = trace['home_proxy']
    observed = {name: {'trace_ids': len(rows), 'p95_ms': p95(rows.values())}
                for name, rows in values.items()}
    for name in ('before', 'during', 'after'):
        observed.setdefault(name, {'trace_ids': 0, 'p95_ms': None})
    before = observed['before']['p95_ms']
    during = observed['during']['p95_ms']
    after = observed['after']['p95_ms']
    checks = {
        'public_home_proxy_samples': all(
            observed[name]['trace_ids'] >= rule['minimum_home_proxy_spans_each_window']
            for name in ('before', 'during', 'after')),
        'public_home_p95_increase': before is not None and during is not None and
            during - before >= rule['minimum_p95_increase_milliseconds'],
        'public_home_p95_recovery': before is not None and after is not None and
            after - before <= rule['maximum_after_minus_before_p95_milliseconds'],
    }
    return checks, {'home_proxy': observed, 'source': 'public/traces.parquet'}


def assess(run_id, *, policy_path=POLICY):
    case = run_id.split('-ATTEMPT-', 1)[0]
    source = DATA / 'runs' / run_id
    public = DATA / 'exports' / run_id / 'public'
    policy = read(policy_path)
    schedule = read(source / 'schedule.json')
    terminal = read(source / 'capture-terminal.json')
    quality = read(source / 'quality-audit.json')
    manifest_path = Path(read(source / 'control/freeze-reference.json')['manifest_path'])
    manifest = read(manifest_path)
    exported = read(public / 'manifest.json')
    if (case not in {f'SF-{index:03d}' for index in range(13, 18)} or
            manifest['run_id'] != run_id or exported['case_id'] != case or
            schedule['run_id'] != run_id or
            terminal['status'] != 'FORMAL_CAPTURE_COMPLETE_PENDING_ADMISSION' or
            quality['status'] != 'DATA_COLLECTION_ACCEPTED_FOR_ADMISSION' or
            policy['window_seconds'] != 1800):
        raise RuntimeError('ADMISSION_INPUT_IDENTITY_OR_COLLECTION')
    for name, entry in exported['files'].items():
        if sha(public / name) != entry['sha256']:
            raise RuntimeError('ADMISSION_PUBLIC_HASH_MISMATCH:' + name)
    topology = read(public / 'topology.json')
    action_windows = windows(manifest)
    trace = trace_evidence(public, action_windows, manifest['actions'])
    results = read(source / 'control/action-results.json')
    checks = {'complete_public_collection': True}
    evidence = {}
    for action in manifest['actions']:
        ident = action['action_id']
        observed = results[ident]
        rule = policy['pod_memory' if action['kind'] == 'POD' else
                      'service_pause' if action['kind'] == 'SERVICE' else
                      'http_delay']
        if action['kind'] == 'HTTP':
            checks[ident + '_injection'] = (
                observed['status'] == 'HTTP_ACTION_COMPLETE' and
                observed['authorized_headers'] >= action['min_fault_requests'])
            extra, detail = http_delay(trace[ident], rule)
        else:
            effect = observed['effect']
            checks[ident + '_injection'] = (
                observed['status'] == 'ACTION_TERMINAL' and
                effect['status'] ==
                'COMPETITION_BOUNDED_INJECTION_COMPLETED_NOT_YET_QUALIFIED' and
                effect['cleanup']['status'] ==
                'COMPETITION_CLEANUP_ACTIONS_VERIFIED' and
                abs(effect['duration_seconds'] - action['duration_seconds']) <=
                policy['injection_duration_tolerance_seconds'] and
                abs(stamp(effect['actual_start_utc']) -
                    stamp(action['planned_start_utc'])) <=
                policy['injection_start_tolerance_seconds'])
            registration = read(Path(action['qualified_registration']))
            targets = registration['targets']
            same = all(all(any(pod['uid'] == target['uid'] and
                            pod['node'] == target['node'] and
                            pod['name'] == target['name'] and
                            all(container['restart_count'] == target['restart_count']
                                for container in pod['containers'])
                            for pod in snapshot['pods'])
                           for target in targets)
                       for snapshot in topology['snapshots'])
            checks[ident + '_public_target_uid_continuity'] = same
            if action['kind'] == 'POD':
                sibling = [item for snapshot in topology['snapshots'][:1]
                           for item in snapshot['pods']
                           if item['service'] == 'frontend' and
                           item['uid'] != targets[0]['uid']]
                if len(sibling) != 1:
                    raise RuntimeError('ADMISSION_POD_SIBLING_NOT_UNIQUE')
                extra, detail = pod_memory(public, action_windows[ident],
                                           targets[0], sibling[0], rule)
                servers = trace[ident]['service_servers']
                extra['target_ordinary_traces_before_after'] = all(
                    len(servers[targets[0]['uid']][name]) >= 10
                    for name in ('before', 'after'))
                extra['sibling_ordinary_traces_all_windows'] = all(
                    len(servers[sibling[0]['uid']][name]) >= 10
                    for name in ('before', 'during', 'after'))
                detail['public_server_trace_ids'] = {
                    uid: {name: len(periods[name])
                          for name in ('before', 'during', 'after')}
                    for uid, periods in servers.items()
                    if uid in (targets[0]['uid'], sibling[0]['uid'])}
            else:
                extra, detail = service_pause(trace[ident],
                                              action_windows[ident], targets, rule)
        checks.update({ident + '_' + key: value for key, value in extra.items()})
        evidence[ident] = detail
    return {'status': 'ADMITTED_FOR_GT_DRAFT' if all(checks.values()) else
            'INVALID_CASE', 'case_id': case, 'run_id': run_id,
            'checks': checks,
            'blockers': [key for key, value in checks.items() if not value],
            'public_evidence': evidence,
            'public_manifest_sha256': sha(public / 'manifest.json'),
            'admission_policy_sha256': sha(policy_path),
            'private_injection_used_as_public_feature': False,
            'assessed_utc': datetime.now(timezone.utc).isoformat()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    result = assess(args.run_id)
    path = DATA / 'runs' / args.run_id / 'admission.json'
    if path.exists():
        raise ValueError('ADMISSION_RESULT_IMMUTABLE')
    with path.open('x') as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
        stream.write('\n')
    path.chmod(0o600)
    print(json.dumps({'status': result['status'],
        'run_id': args.run_id, 'blockers': result['blockers']}))
    if result['status'] != 'ADMITTED_FOR_GT_DRAFT':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
