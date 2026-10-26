#!/usr/bin/env python3
"""Assess real HTTP delay against public Proxy spans and normal controls."""

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


from http_qual import DATA, ROOT, CASES, sha, stamp, save


def read(path):
    return json.loads(path.read_text())


def p95(values):
    ordered = sorted(values)
    return ordered[math.ceil(.95 * len(ordered)) - 1] if ordered else None


def spans(root):
    for file in sorted(root.glob('*.json')):
        for hit in read(file)['hits']['hits']:
            yield hit['_source']


def assess(plan_path, digest):
    plan = read(plan_path)
    run = DATA / plan['run_id']
    action = plan['action']
    case = plan['case']
    if case not in CASES or sha(plan_path) != digest:
        raise ValueError('HTTP_QUALIFICATION_PLAN_CHANGED')
    terminal = read(run / 'terminal.json')
    posthealth = read(run / 'posthealth.json')
    collector = read(run / 'collector-counters.json')
    controls = read(run / 'controls/result-manifest.json')
    schedule = read(run / 'schedule.json')
    runtime = ROOT / 'runtime' / plan['run_id'] / action['action_id']
    events = [json.loads(line) for line in
              (runtime / 'header-events.jsonl').read_text().splitlines()]
    observed = {}
    checks = {}
    controls_with_fault_flag = 0
    for phase, window in schedule['windows'].items():
        first = run / 'capture/captures' / phase
        final = run / 'capture/trace-final' / phase
        audit = run / 'capture/trace-late-audit' / phase
        fm = read(first / 'capture-manifest.json')
        final_manifest = read(final / 'final-manifest.json')
        late_manifest = read(audit / 'late-audit-manifest.json')
        checks[phase + '_modality_and_hashes'] = (
            all(fm['window'].get(key) == window[key]
                for key in ('start_utc', 'end_utc')) and
            fm['trace_source'] == 'JAEGER_OPENSEARCH_STORAGE' and
            fm['log_records'] > 0 and fm['span_records'] > 0 and
            len(list((first / 'metrics').glob('*.json'))) >= 11 and
            final_manifest['first_manifest_sha256'] ==
                sha(first / 'capture-manifest.json') and
            late_manifest['final_manifest_sha256'] ==
                sha(final / 'final-manifest.json'))
        ids = {}
        begin, end = stamp(window['start_utc']), stamp(window['end_utc'])
        for span in spans(audit / 'trace-storage-pages'):
            if (span.get('process', {}).get('serviceName') != 'frontend-proxy' or
                    span.get('tag', {}).get('span@kind') != 'server'):
                continue
            when = span['startTime'] / 1e6
            if not begin <= when < end:
                continue
            tags = {row['key']: row.get('value') for row in span.get('tags', [])}
            if tags.get('user_agent') == 'business-control/1.0' and \
                    tags.get('response_flags') == 'DI':
                controls_with_fault_flag += 1
            if (tags.get('user_agent') != 'business-workload/1.0' or
                    urlparse(tags.get('http.url') or '').path != '/'):
                continue
            ids[span['traceID']] = max(ids.get(span['traceID'], 0),
                                       span['duration'] / 1000)
        observed[phase] = {'trace_ids': len(ids), 'p95_ms': p95(ids.values()),
                           'example_trace_ids': sorted(ids)[:8]}
    min_count = 30
    checks['public_home_proxy_samples'] = all(
        observed[phase]['trace_ids'] >= min_count
        for phase in ('before', 'during', 'after'))
    before = observed['before']['p95_ms']
    during = observed['during']['p95_ms']
    after = observed['after']['p95_ms']
    checks['public_home_delay'] = (
        before is not None and during is not None and during - before >= 100)
    checks['public_home_recovery'] = (
        before is not None and after is not None and after - before <= 100)
    applied = [item for item in events if item['status'] == 'AUTHORIZED_HEADER']
    denied = [item for item in events if item['status'] != 'AUTHORIZED_HEADER']
    start = stamp(action['planned_start_utc'])
    end = start + action['duration_seconds']
    checks['private_bounded_header_proof'] = (
        len(applied) >= action['min_fault_requests'] and not denied and
        applied and abs(stamp(applied[0]['at_utc']) - start) <= 2 and
        end - stamp(applied[-1]['at_utc']) <= 5 and
        sha(runtime / 'header-events.jsonl') ==
        terminal.get('header_events_sha256'))
    checks['normal_control_without_fault_flag'] = controls_with_fault_flag == 0
    checks['normal_control_success'] = all(
        controls['streams'][name]['successful_requests'] >=
        controls['streams'][name]['planned_requests'] * .99
        for name in ('image_asset', 'cart_view'))
    checks['collector_no_refusal'] = collector['refused_delta'] == 0
    checks['recovery_and_five_host_health'] = (
        terminal['status'] ==
        'HTTP_QUALIFICATION_CAPTURE_COMPLETE_PENDING_ADMISSION' and
        posthealth['status'] == 'DYNAMIC_STORAGE_AND_BASIC_HEALTH_PASS' and
        (runtime / 'REVOKED.json').is_file() and
        read(ROOT / 'runs' / plan['run_id'] / 'guard-terminal.json')['status'] ==
        'HTTP_QUALIFICATION_GUARD_FINALIZED')
    return {'status': 'QUALIFIED_FOR_THIS_VERSION' if all(checks.values()) else
            'QUALIFICATION_NOT_PASSED',
            'qualification_id': plan['run_id'], 'case': case,
            'mechanism': 'HTTP', 'duration_seconds': action['duration_seconds'],
            'adapter_sha256': sha(Path('/root/ops-competition-2026/final-case-formal-entry-v7-20261020/scripts/http_workload.py')),
            'plan_sha256': digest, 'checks': checks,
            'blockers': [key for key, okay in checks.items() if not okay],
            'public_home_proxy': observed,
            'private_authorized_headers': len(applied),
            'normal_control_fault_flags': controls_with_fault_flag,
            'private_header_not_used_as_public_anomaly_feature': True,
            'formal_case_created': False,
            'assessed_utc': datetime.now(timezone.utc).isoformat()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--sha256', required=True)
    args = parser.parse_args()
    run = DATA / read(args.plan)['run_id']
    if (run / 'qualification-decision.json').exists():
        raise ValueError('HTTP_QUALIFICATION_DECISION_IMMUTABLE')
    result = assess(args.plan, args.sha256)
    save(run / 'qualification-decision.json', result)
    print(json.dumps({'status': result['status'],
                      'blockers': result['blockers']}))
    if result['status'] != 'QUALIFIED_FOR_THIS_VERSION':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
