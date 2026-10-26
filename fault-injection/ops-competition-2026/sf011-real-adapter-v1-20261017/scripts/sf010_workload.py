#!/usr/bin/env python3
"""SF-011-only LOW-STABLE business load with a time-bound home-route delay header."""

import argparse
import hashlib
import json
import random
import threading
import time
import types
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


SOURCE = Path('/root/cross-layer-observability-control-rehearsal/scripts/workload_generator_rehearsal.py')
MACHINE = Path('/root/stage5-v3-observability-diagnostics/snapshots/reference/workload-state-machine-c3.yaml')
PROFILES = Path('/root/ops-competition-2026/low-stable-v4-20261017/design/load-profiles-v4b.yaml')
PEOPLE = Path('/root/cross-layer-workload-generation/design/people.json')
BASE = 'http://10.104.177.128:8080'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def should_delay(url, method, at, start, end):
    parsed = urllib.parse.urlparse(url)
    return (method == 'GET' and parsed.path == '/' and not parsed.query
            and start <= at < end)


def neutralize_user_agent(source):
    old = "'User-Agent': 'stage5-workload-rehearsal/1.0'"
    if source.count(old) != 1:
        raise RuntimeError('Qualified workload User-Agent anchor changed')
    return source.replace(old, "'User-Agent': 'business-workload/1.0'")


def lease_valid(lease, plan_sha, at):
    try:
        issued,expires=timestamp(lease['issued_utc']),timestamp(lease['expires_utc'])
    except (KeyError, TypeError, ValueError):
        return False
    return (lease.get('run_id')=='SF-011-ATTEMPT-01' and
            lease.get('plan_sha256')==plan_sha and
            lease.get('permission')=='BOUND_HTTP_REQDELAY' and
            lease.get('safety_pass') is True and
            0<expires-issued<=4.01 and issued-.5<=at<=expires)


def execute_with_start(original, *args, **kwargs):
    started = datetime.now(timezone.utc).isoformat()
    row = original(*args, **kwargs)
    if 'request_started_utc' in row:
        raise RuntimeError('Qualified workload start-time schema changed')
    row['request_started_utc'] = started
    return row


def run(plan, output, plan_sha):
    if (output.exists() or plan.get('id') != 'SF-011-ATTEMPT-01' or
            plan.get('formal_case') is not True or
            plan['duration_seconds'] != 1800 or plan['profile'] != 'LOW'):
        raise RuntimeError('Wrong SF-011 run identity, duration, profile or output')
    fault = plan['request_delay']
    if fault != {'operation': 'home', 'offset_seconds': 960,
                 'duration_seconds': 60, 'delay_ms': 250,
                 'lease_path': str(Path(plan['control_root'])/'http-reqdelay-lease.json')}:
        raise RuntimeError('HTTP delay contract changed')
    expected = plan['source_sha256']
    for path in (SOURCE, MACHINE, PROFILES, PEOPLE, Path(__file__)):
        if sha(path) != expected.get(str(path)):
            raise RuntimeError('Registered source changed: ' + str(path))
    start = timestamp(plan['start_utc'])
    fault_start = start + fault['offset_seconds']
    fault_end = fault_start + fault['duration_seconds']
    if (fault_start - start, fault_end - fault_start,
            start + plan['duration_seconds'] - fault_end) != (960, 60, 780):
        raise RuntimeError('Expected immutable SF-011 time window')
    if not 1 <= start - time.time() <= 180:
        raise RuntimeError('Stale scheduled start')
    source = neutralize_user_agent(SOURCE.read_text())
    module = types.ModuleType('qualified_business_workload')
    module.__file__ = str(SOURCE)
    exec(compile(source, str(SOURCE), 'exec'), module.__dict__)
    lock = threading.Lock()

    def capture_request(url, data=None, headers=None, method=None, **kwargs):
        headers = dict(headers or {})
        at = time.time()
        delayed = should_delay(url, method, at, fault_start, fault_end)
        if delayed:
            try:
                lease_path=Path(fault['lease_path'])
                if lease_path.is_symlink():
                    raise RuntimeError('HTTP fault lease path became a symlink')
                lease=json.loads(lease_path.read_text())
                if not lease_valid(lease,plan_sha,at):
                    raise RuntimeError('HTTP fault lease expired or not authorized')
            except (OSError, json.JSONDecodeError, RuntimeError) as exc:
                with lock:
                    with (output / 'lease-violations.jsonl').open('a') as stream:
                        stream.write(json.dumps({'at_utc': datetime.fromtimestamp(
                            at, timezone.utc).isoformat(), 'traceparent': headers.get('traceparent'),
                            'error': type(exc).__name__}) + '\n')
                raise RuntimeError('HTTP fault lease missing, invalid or expired') from exc
            headers['x-envoy-fault-delay-request'] = str(fault['delay_ms'])
        if urllib.parse.urlparse(url).path == '/':
            row = {'at_utc': datetime.fromtimestamp(at, timezone.utc).isoformat(),
                   'traceparent': headers.get('traceparent'),
                   'business_path': '/', 'delay_header_applied': delayed,
                   'requested_delay_ms': fault['delay_ms'] if delayed else None}
            with lock:
                with (output / 'home-header-events.jsonl').open('a') as stream:
                    stream.write(json.dumps(row) + '\n')
        return urllib.request.Request(url, data=data, headers=headers,
                                      method=method, **kwargs)

    module.urllib = types.SimpleNamespace(
        request=types.SimpleNamespace(Request=capture_request,
                                      urlopen=urllib.request.urlopen),
        parse=urllib.parse, error=urllib.error)
    original = module.execute_request

    def recorded(*args, **kwargs):
        row = execute_with_start(original, *args, **kwargs)
        with lock:
            with (output / 'request-events-live.jsonl').open('a') as stream:
                stream.write(json.dumps(row) + '\n')
        return row

    module.execute_request = recorded
    args = argparse.Namespace(machine=str(MACHINE), profiles=str(PROFILES),
        people=str(PEOPLE), profile='LOW', seed=plan['seed'],
        start_utc=plan['start_utc'], duration=1800, base_url=BASE,
        allow_errors=True, output=str(output))
    result = module.run(args)
    if (output / 'lease-violations.jsonl').exists():
        raise RuntimeError('HTTP fault lease violation; formal attempt invalid')
    headers = [json.loads(line) for line in (output / 'home-header-events.jsonl').read_text().splitlines()]
    if not headers or not any(row['delay_header_applied'] for row in headers):
        raise RuntimeError('No real state-machine home request received the fault header')
    if any(row['delay_header_applied'] !=
           (fault_start <= timestamp(row['at_utc']) < fault_end) for row in headers):
        raise RuntimeError('Header applied outside the registered interval')
    summary = {'status': 'SF011_WORKLOAD_COMPLETE_PENDING_CASE_ADMISSION',
               'generator_status': result['status'],
               'session_rate_per_second': result['arrival_rate_sessions_per_second'],
               'planned_sessions': result['planned_sessions'],
               'request_count': result['request_count'],
               'home_requests': len(headers),
               'delayed_home_requests': sum(row['delay_header_applied'] for row in headers),
               'home_header_events_sha256': sha(output / 'home-header-events.jsonl'),
               'request_events_sha256': sha(output / 'request-events-live.jsonl'),
               'formal_case_collection_attempted': True,
               'public_created': False, 'private_gt_created': False}
    with (output / 'delay-rehearsal-result.json').open('x') as stream:
        json.dump(summary, stream, indent=2)
        stream.write('\n')
    print(json.dumps(summary), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--registration', required=True, type=Path)
    parser.add_argument('--registration-sha256', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if sha(args.registration) != args.registration_sha256:
        raise RuntimeError('Registration hash mismatch')
    run(json.loads(args.registration.read_text()), args.output,
        args.registration_sha256)


if __name__ == '__main__':
    main()
