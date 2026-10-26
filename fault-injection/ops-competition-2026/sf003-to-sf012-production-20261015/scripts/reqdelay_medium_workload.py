#!/usr/bin/env python3
"""Run the existing state machine with a bounded, home-only delay header."""

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
PROFILES = Path('/root/stage5-v3-autonomous-preparation/design/load-profiles-c3.yaml')
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


def run(plan, output):
    if output.exists() or plan['duration_seconds'] != 420 or plan['profile'] != 'MEDIUM':
        raise RuntimeError('Wrong qualification duration, profile or output')
    expected = plan['source_sha256']
    for name, path in [('generator', SOURCE), ('machine', MACHINE),
                       ('profiles', PROFILES), ('people', PEOPLE),
                       ('wrapper', Path(__file__))]:
        if sha(path) != expected[name]:
            raise RuntimeError('Qualification source changed: ' + name)
    start = timestamp(plan['start_utc'])
    fault_start, fault_end = timestamp(plan['fault_start_utc']), timestamp(plan['fault_end_utc'])
    if (fault_start - start, fault_end - fault_start,
            start + plan['duration_seconds'] - fault_end) != (180, 60, 180):
        raise RuntimeError('Expected immutable 180+60+180 seconds')
    if not 1 <= start - time.time() <= 180:
        raise RuntimeError('Stale scheduled start')
    source = SOURCE.read_text()
    module = types.ModuleType('qualified_business_workload')
    module.__file__ = str(SOURCE)
    exec(compile(source, str(SOURCE), 'exec'), module.__dict__)
    lock = threading.Lock()

    def capture_request(url, data=None, headers=None, method=None, **kwargs):
        headers = dict(headers or {})
        at = time.time()
        delayed = should_delay(url, method, at, fault_start, fault_end)
        if delayed:
            headers['x-envoy-fault-delay-request'] = str(plan['delay_ms'])
        if urllib.parse.urlparse(url).path == '/':
            row = {'at_utc': datetime.fromtimestamp(at, timezone.utc).isoformat(),
                   'traceparent': headers.get('traceparent'),
                   'business_path': '/', 'delay_header_applied': delayed,
                   'requested_delay_ms': plan['delay_ms'] if delayed else None}
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
        row = original(*args, **kwargs)
        with lock:
            with (output / 'request-events-live.jsonl').open('a') as stream:
                stream.write(json.dumps(row) + '\n')
        return row

    module.execute_request = recorded
    args = argparse.Namespace(machine=str(MACHINE), profiles=str(PROFILES),
        people=str(PEOPLE), profile='MEDIUM', seed=plan['seed'],
        start_utc=plan['start_utc'], duration=420, base_url=BASE,
        allow_errors=True, output=str(output))
    result = module.run(args)
    headers = [json.loads(line) for line in (output / 'home-header-events.jsonl').read_text().splitlines()]
    if not headers or not any(row['delay_header_applied'] for row in headers):
        raise RuntimeError('No real state-machine home request received the fault header')
    if any(row['delay_header_applied'] !=
           (fault_start <= timestamp(row['at_utc']) < fault_end) for row in headers):
        raise RuntimeError('Header applied outside the registered interval')
    summary = {'status': 'MEDIUM_WORKLOAD_DELAY_REHEARSAL_COMPLETE',
               'generator_status': result['status'],
               'session_rate_per_second': result['arrival_rate_sessions_per_second'],
               'planned_sessions': result['planned_sessions'],
               'request_count': result['request_count'],
               'home_requests': len(headers),
               'delayed_home_requests': sum(row['delay_header_applied'] for row in headers),
               'home_header_events_sha256': sha(output / 'home-header-events.jsonl'),
               'request_events_sha256': sha(output / 'request-events-live.jsonl'),
               'formal_case_created': False}
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
    run(json.loads(args.registration.read_text()), args.output)


if __name__ == '__main__':
    main()
