#!/usr/bin/env python3
"""Bounded, seeded OpenTelemetry Demo business-session load generator."""

import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import statistics
import time
import urllib.error
import urllib.request
import uuid

import yaml


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def choose(edges, rng):
    draw = rng.random()
    cumulative = 0.0
    for edge in edges:
        cumulative += edge['probability']
        if draw < cumulative:
            return edge['to']
    return edges[-1]['to']


def validate_machine(machine):
    operations = machine['operations']
    for name, spec in machine['workflows'].items():
        assert spec['start'] in operations, name
        seen = {spec['start']}
        for source, edges in spec['transitions'].items():
            assert source in operations and edges, (name, source)
            assert abs(sum(edge['probability'] for edge in edges) - 1) < 1e-8
            for edge in edges:
                assert edge['to'] == 'end' or edge['to'] in operations
                seen.add(edge['to'])
        assert all(node == 'end' or node in spec['transitions'] for node in seen)
    assert sum(item['weight'] for item in machine['workflows'].values()) > 0


def operation_payload(operation, state, people):
    if operation == 'cart_add':
        return {'item': {'productId': state['product'], 'quantity': state['quantity']},
                'userId': state['user_id']}
    if operation == 'checkout':
        return {**people[state['person_index']], 'userId': state['user_id']}
    return None


def execute_request(base_url, operation, spec, state, people, rng, timeout):
    trace_id = f'{rng.getrandbits(128):032x}'
    span_id = f'{rng.getrandbits(64):016x}'
    if trace_id == '0' * 32 or span_id == '0' * 16:
        raise ValueError('Invalid generated trace context')
    path = spec['path'].format(**state)
    body = operation_payload(operation, state, people)
    headers = {'User-Agent': 'stage5-workload-rehearsal/1.0',
               'baggage': 'synthetic_request=true,session.id=' + state['user_id'],
               'traceparent': f'00-{trace_id}-{span_id}-01'}
    if body is not None:
        headers['Content-Type'] = 'application/json'
    request = urllib.request.Request(base_url.rstrip('/') + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers, method=spec['method'])
    start = time.monotonic()
    status, error = None, None
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            response.read(256)
    except urllib.error.HTTPError as exc:
        status, error = exc.code, 'HTTPError'
    except Exception as exc:
        error = type(exc).__name__
    return {'operation': operation, 'method': spec['method'], 'path': path,
            'time_utc': utcnow(), 'trace_id': trace_id,
            'status_code': status, 'error_type': error,
            'latency_ms': round((time.monotonic() - start) * 1000, 3),
            'success': status is not None and 200 <= status < 400}


def run_session(index, workflow_name, machine, people, base_url, seed, timeout):
    rng = random.Random((seed << 32) + index)
    flow = machine['workflows'][workflow_name]
    state = {'user_id': str(uuid.uuid5(uuid.NAMESPACE_URL, f'{seed}:{index}')),
             'product': rng.choice(machine['products']),
             'category': rng.choice(machine['categories']),
             'quantity': rng.choice([1, 2, 3, 4, 5, 10]),
             'person_index': rng.randrange(len(people)),
             'product_selected': False, 'cart_nonempty': False}
    steps, node = [], flow['start']
    for _ in range(12):
        required = machine['operations'][node].get('requires', [])
        if any(not state.get(key) for key in required):
            return {'index': index, 'workflow': workflow_name, 'steps': steps,
                    'completed': False, 'failure': 'PRECONDITION_' + node}
        result = execute_request(base_url, node, machine['operations'][node],
                                 state, people, rng, timeout)
        steps.append(result)
        if not result['success']:
            return {'index': index, 'workflow': workflow_name, 'steps': steps,
                    'completed': False, 'failure': result['error_type'] or str(result['status_code'])}
        if node == 'product':
            state['product_selected'] = True
        if node == 'cart_add':
            state['cart_nonempty'] = True
        node = choose(flow['transitions'][node], rng)
        if node == 'end':
            return {'index': index, 'workflow': workflow_name, 'steps': steps,
                    'completed': True, 'failure': None,
                    'branch': '>'.join(step['operation'] for step in steps)}
    return {'index': index, 'workflow': workflow_name, 'steps': steps,
            'completed': False, 'failure': 'MAX_TRANSITIONS'}


def run(args):
    config_path = Path(args.machine)
    profiles_path = Path(args.profiles)
    people_path = Path(args.people)
    machine = yaml.safe_load(config_path.read_text())
    validate_machine(machine)
    profiles = yaml.safe_load(profiles_path.read_text())['profiles']
    profile = dict(profiles[args.profile])
    duration = args.duration if args.duration is not None else profile['duration_seconds']
    seed = args.seed if args.seed is not None else profile['seed']
    people = json.loads(people_path.read_text())
    if not people or not all({'email', 'address', 'userCurrency', 'creditCard'} <= row.keys() for row in people):
        raise ValueError('People fixture does not match deployed checkout payload')
    if duration <= 0 or duration > 3600 or profile['arrival_rate_rps'] <= 0:
        raise ValueError('Invalid bounded profile')
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    manifest = {'profile': args.profile, 'seed': seed,
                'duration_seconds': duration, 'arrival_rate_sessions_per_second': profile['arrival_rate_rps'],
                'concurrency': profile['concurrency'], 'timeout_seconds': profile['timeout_seconds'],
                'base_url': args.base_url, 'machine_sha256': digest(config_path),
                'profiles_sha256': digest(profiles_path), 'people_sha256': digest(people_path),
                'started_utc': utcnow(), 'fault_injected_by_generator': False}
    (output / 'input-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    weights = [(name, flow['weight']) for name, flow in machine['workflows'].items()]
    chooser = random.Random(seed)
    start = time.monotonic()
    end = start + duration
    futures, rows = set(), []
    next_index = 0
    with ThreadPoolExecutor(max_workers=profile['concurrency']) as pool:
        while True:
            target = start + next_index / profile['arrival_rate_rps']
            if target >= end:
                break
            time.sleep(max(0, target - time.monotonic()))
            if time.monotonic() - target > 1.0:
                raise RuntimeError('Arrival schedule lag exceeded 1 second')
            while len(futures) >= profile['concurrency']:
                done, futures = wait(futures, return_when=FIRST_COMPLETED)
                rows.extend(f.result() for f in done)
            workflow = chooser.choices([row[0] for row in weights],
                                       weights=[row[1] for row in weights])[0]
            futures.add(pool.submit(run_session, next_index, workflow, machine,
                                    people, args.base_url, seed, profile['timeout_seconds']))
            next_index += 1
        for future in futures:
            rows.append(future.result())
    rows.sort(key=lambda row: row['index'])
    with (output / 'sessions.jsonl').open('x') as stream:
        for row in rows:
            stream.write(json.dumps(row, separators=(',', ':')) + '\n')
    steps = [step for row in rows for step in row['steps']]
    successes = sum(row['completed'] for row in rows)
    latencies = sorted(step['latency_ms'] for step in steps)
    def quantile(p):
        return latencies[min(len(latencies)-1, int(p*(len(latencies)-1)))] if latencies else None
    manifest.update({'ended_utc': utcnow(), 'planned_sessions': next_index,
                     'completed_sessions': len(rows), 'successful_sessions': successes,
                     'session_success_fraction': successes / len(rows) if rows else 0,
                     'request_count': len(steps), 'request_qps': len(steps) / duration,
                     'request_success_fraction': sum(step['success'] for step in steps) / len(steps) if steps else 0,
                     'latency_ms': {'p50': quantile(.5), 'p95': quantile(.95), 'p99': quantile(.99)},
                     'sessions_sha256': digest(output / 'sessions.jsonl')})
    manifest['status'] = ('LOAD_PASS' if len(rows) == next_index and
        manifest['session_success_fraction'] >= 1-profile['max_error_rate'] else 'LOAD_FAILED')
    (output / 'result-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--machine', required=True)
    parser.add_argument('--profiles', required=True)
    parser.add_argument('--people', required=True)
    parser.add_argument('--profile', choices=['LOW', 'MEDIUM', 'HIGH'], required=True)
    parser.add_argument('--seed', type=int)
    parser.add_argument('--duration', type=int)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = run(args)
    print(json.dumps({k: result[k] for k in ('status','profile','planned_sessions','successful_sessions','request_count','request_qps')}))
    if result['status'] != 'LOAD_PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
