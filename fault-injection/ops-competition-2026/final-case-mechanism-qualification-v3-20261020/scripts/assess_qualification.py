#!/usr/bin/env python3
"""Assess a real qualification; private injection verifies validity, not public signal."""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
sys.path.insert(0, str(OPS / 'scripts'))
from competition_qualification import evaluate, read, stamp

from run_qualification import CASES, sha, save


def success_fraction(rows):
    actual = [row for row in rows if row.get('trace_id')]
    return sum(row.get('success') is True for row in actual) / len(actual) if actual else 0


def read_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def minimum_control_samples(window):
    seconds = round(stamp(window['end_utc']) - stamp(window['start_utc']))
    if seconds not in (180, 240, 300):
        raise ValueError('QUALIFICATION_CONTROL_WINDOW_DURATION')
    return seconds * 9 // 10


def assess(run, registration, posthealth):
    plan = read(registration)
    case = plan['id'].split('-')[1] + '-' + plan['id'].split('-')[2]
    if case not in CASES:
        raise ValueError('QUALIFICATION_CASE_UNKNOWN')
    schedule = read(run / 'schedule.json')
    injection = read(run / 'private-injection-result.json')
    if posthealth['status'] != 'DYNAMIC_STORAGE_AND_BASIC_HEALTH_PASS':
        raise RuntimeError('QUALIFICATION_POSTHEALTH_BLOCKED')
    # The historical evaluator's post-health enum predates the current five-host gate.
    historical_health = {'status': 'COMPETITION_ENVIRONMENT_PASS_WATCHDOG_PENDING',
                         'current_environment_blockers': []}
    original = evaluate(run, plan, injection, historical_health)
    checks = dict(original['checks'])
    request_rows = []
    for line in (run / 'state-machine/sessions.jsonl').read_text().splitlines():
        request_rows.extend(json.loads(line)['steps'])
    before = schedule['windows']['before']
    after = schedule['windows']['after']
    baseline = [row for row in request_rows if
                stamp(before['start_utc']) <= stamp(row['time_utc']) -
                float(row.get('latency_ms') or 0) / 1000 < stamp(before['end_utc'])]
    tail = [row for row in request_rows if
            stamp(after['end_utc']) - 60 <= stamp(row['time_utc']) -
            float(row.get('latency_ms') or 0) / 1000 < stamp(after['end_utc'])]
    checks['baseline_no_client_errors'] = success_fraction(baseline) >= .99
    controls = {name: read_lines(run / 'controls' / (name + '.jsonl'))
                for name in ('image_asset', 'cart_view')}
    checks['independent_image_business_control'] = all(
        success_fraction(rows) >= .99 for rows in controls.values())
    recovery_business = success_fraction(tail) >= .99
    if plan['layer'] == 'POD':
        recovery_business &= original['public_anomaly_evidence']['memory_recovered']
    checks['recovery_requests_and_resources'] = recovery_business
    checks['postfault_actual_environment'] = True
    final_spans = sum(row['final_spans'] for row in original['capture_counts'].values())
    late_spans = len(original['late_audit'])
    checks['no_critical_or_unclassified_late_spans'] = (
        final_spans > 0 and late_spans / final_spans <= .005 and
        all(row['classification'] == 'VERIFIED_NONCRITICAL_CONTROL_PLANE'
            for row in original['late_audit']))
    for name, window in schedule['windows'].items():
        total = sum(bool(row.get('request_started_utc')) and
                    stamp(window['start_utc']) <= stamp(row['request_started_utc']) <
                    stamp(window['end_utc']) and row.get('success') is True
                    for row in controls['image_asset'])
        required = minimum_control_samples(window)
        checks['image_control_' + name] = total >= required
    collector = read(run / 'collector-counters.json')
    checks['collector_zero_refused_delta'] = collector['refused_delta'] == 0
    checks['worker_recovery_proof'] = (
        injection['status'] == 'COMPETITION_BOUNDED_INJECTION_COMPLETED_NOT_YET_QUALIFIED'
        and injection.get('cleanup', {}).get('status') ==
        'COMPETITION_CLEANUP_ACTIONS_VERIFIED')
    result = {'status': 'QUALIFIED_FOR_THIS_VERSION' if all(checks.values()) else
              'QUALIFICATION_NOT_PASSED',
              'qualification_id': plan['id'], 'case': case,
              'mechanism': plan['layer'], 'duration_seconds': plan['duration_seconds'],
              'adapter_sha256': plan['adapter_sha256'],
              'registration_sha256': sha(registration),
              'schedule_sha256': sha(run / 'schedule.json'),
              'injection_result_sha256': sha(run / 'private-injection-result.json'),
              'posthealth_sha256': sha(run / 'posthealth.json'),
              'collector_counters_sha256': sha(run / 'collector-counters.json'),
              'checks': checks, 'blockers': [key for key, okay in checks.items()
                                            if not okay],
              'public_anomaly_evidence': original['public_anomaly_evidence'],
              'per_pod_business_server_span_coverage':
                  original['per_pod_business_server_span_coverage'],
              'capture_counts': original['capture_counts'],
              'late_audit': original['late_audit'],
              'business_baseline_success_fraction': success_fraction(baseline),
              'business_recovery_success_fraction': success_fraction(tail),
              'fault_target_from_private_injection_as_public_feature': False,
              'formal_case_created': False,
              'assessed_utc': datetime.now(timezone.utc).isoformat()}
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--registration', required=True, type=Path)
    args = parser.parse_args()
    if (args.run / 'qualification-decision.json').exists():
        raise ValueError('QUALIFICATION_DECISION_ALREADY_FROZEN')
    result = assess(args.run, args.registration, read(args.run / 'posthealth.json'))
    save(args.run / 'qualification-decision.json', result)
    print(json.dumps({'status': result['status'],
        'qualification_id': result['qualification_id'],
        'blockers': result['blockers']}, sort_keys=True))
    if result['status'] != 'QUALIFIED_FOR_THIS_VERSION':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
