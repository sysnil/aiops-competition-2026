#!/usr/bin/env python3
"""Isolated, request-scoped HTTP delay qualification; never creates a formal case."""

import argparse
import hashlib
import json
import os
import secrets
import signal
import statistics
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


BASE = 'http://10.104.177.128:8080'
PROM = 'http://10.97.14.138:9090'
KUBE = ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf']
RUN = Path('/data/stage5-v3/ops-competition-2026/rehearsals/SF30-REQDELAY-QUAL-20261015')
SELF = Path(__file__)
PHASE_SECONDS = {'before': 180, 'during': 60, 'after': 180}
TARGET_PATH = '/'
CONTROL_PATHS = ('/images/products/OpticalTubeAssembly.jpg', '/api/cart')
DELAY_MS = 250


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('x') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(0o600)


def phase_at(offset):
    if offset < 0 or offset >= sum(PHASE_SECONDS.values()):
        return None
    if offset < PHASE_SECONDS['before']:
        return 'before'
    if offset < PHASE_SECONDS['before'] + PHASE_SECONDS['during']:
        return 'during'
    return 'after'


def collector():
    query = 'otelcol_receiver_refused_spans_total'
    url = PROM + '/api/v1/query?' + urllib.parse.urlencode({'query': query})
    with urllib.request.urlopen(url, timeout=10) as response:
        rows = json.load(response)['data']['result']
    values = {row['metric'].get('transport'): float(row['value'][1]) for row in rows}
    if set(values) != {'grpc', 'http'}:
        raise RuntimeError('Collector refusal counters incomplete')
    return values


def health():
    nodes = json.loads(subprocess.check_output(KUBE + ['get', 'nodes', '-o', 'json'],
                                               text=True, timeout=20))['items']
    ready = [node['metadata']['name'] for node in nodes if any(
        c['type'] == 'Ready' and c['status'] == 'True' for c in node['status']['conditions'])]
    if len(nodes) != 4 or len(ready) != 4:
        raise RuntimeError('Four-node Ready gate failed')
    pods = json.loads(subprocess.check_output(KUBE + ['-n', 'otel-demo', 'get', 'pods', '-o', 'json'],
                                              text=True, timeout=20))['items']
    proxies = [p for p in pods if p['metadata']['name'].startswith('frontend-proxy-')]
    if len(proxies) != 1 or not all(c['ready'] for c in proxies[0]['status']['containerStatuses']):
        raise RuntimeError('Frontend proxy is not uniquely Ready')
    if any(os.stat('/').st_dev == os.stat('/data/stage5-v3').st_dev for _ in [0]):
        raise RuntimeError('Master data mount missing')
    for mount in ('/', '/data/stage5-v3'):
        stat = os.statvfs(mount)
        if stat.f_bavail * stat.f_frsize < 5_000_000_000:
            raise RuntimeError('Five GB storage floor: ' + mount)
    return {'ready_nodes': ready, 'proxy_uid': proxies[0]['metadata']['uid'],
            'collector_refused': collector()}


def request(path, delayed, phase):
    trace_id, span_id = secrets.token_hex(16), secrets.token_hex(8)
    headers = {'User-Agent': 'business-workload/1.0',
               'traceparent': f'00-{trace_id}-{span_id}-01'}
    if delayed:
        headers['x-envoy-fault-delay-request'] = str(DELAY_MS)
    start = time.monotonic()
    status, error = None, None
    try:
        req = urllib.request.Request(BASE + path, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as response:
            status = response.status
            response.read(128)
    except urllib.error.HTTPError as exc:
        status, error = exc.code, 'HTTPError'
    except Exception as exc:
        error = type(exc).__name__
    return {'at_utc': now(), 'phase': phase, 'path': path, 'delay_header': delayed,
            'trace_id': trace_id, 'status': status, 'error': error,
            'latency_ms': round((time.monotonic() - start) * 1000, 3)}


def register(audit_path):
    if RUN.exists():
        raise RuntimeError('Qualification directory exists; no overwrite')
    audit = json.loads(Path(audit_path).read_text())
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(audit['at_utc'])).total_seconds()
    if audit['status'] != 'HOST_RESIDUE_PASS' or not 0 <= age <= 300:
        raise RuntimeError('Fresh five-host residue gate missing')
    initial = health()
    cm = json.loads(subprocess.check_output(KUBE + ['-n', 'otel-demo', 'get', 'cm',
        's5v3ap-frontend-proxy-idle-c2', '-o', 'json'], text=True, timeout=20))
    template = cm['data']['envoy.tmpl.yaml']
    if template.count('envoy.filters.http.fault') != 1 or 'header_delay: {}' not in template:
        raise RuntimeError('Verified Envoy request-header fault interface changed')
    units = subprocess.check_output(['systemctl', 'list-units', '--state=running',
        '--type=service', '--plain', '--no-legend'], text=True, timeout=10)
    if any(token in units for token in ('ops-sf30-', 'ops-sf002-', 'ops-b8-')):
        raise RuntimeError('Competition run already active')
    RUN.mkdir(parents=True, mode=0o700)
    plan = {'id': RUN.name, 'formal_case': False, 'case_created': False,
            'fault_scope': 'one ordinary frontend GET / per second in during only',
            'start_epoch_seconds': time.time() + 20,
            'phase_seconds': PHASE_SECONDS, 'request_rate_per_second': 1,
            'delay_ms': DELAY_MS, 'control_paths': CONTROL_PATHS,
            'qualification_threshold_ms': 180,
            'source_sha256': sha(SELF), 'residue_audit_sha256': sha(audit_path),
            'envoy_template_sha256': hashlib.sha256(template.encode()).hexdigest(),
            'initial_health': initial, 'registered_utc': now(),
            'independent_systemd_runtime_max_seconds': 500,
            'request_timeout_seconds': 5,
            'no_persistent_fault_configuration': True}
    write_new(RUN / 'registration.json', plan)
    print(json.dumps({'status': 'REGISTERED_FOR_INDEPENDENT_QUALIFICATION',
                      'registration': str(RUN / 'registration.json'),
                      'sha256': sha(RUN / 'registration.json')}))


def run(plan_path, plan_sha):
    if sha(plan_path) != plan_sha:
        raise RuntimeError('Registration hash mismatch')
    plan = json.loads(Path(plan_path).read_text())
    if plan['source_sha256'] != sha(SELF) or plan['id'] != RUN.name:
        raise RuntimeError('Source or run identity changed')
    if os.getenv('INVOCATION_ID') is None:
        raise RuntimeError('Independent systemd RuntimeMaxSec watchdog required')
    start = plan['start_epoch_seconds']
    if not 0 < start - time.time() < 30:
        raise RuntimeError('Start window expired')
    initial = health()
    if initial['proxy_uid'] != plan['initial_health']['proxy_uid']:
        raise RuntimeError('Proxy identity changed after registration')
    rows = []
    total = sum(PHASE_SECONDS.values())
    aborted = False
    def stop(signum, frame):
        nonlocal aborted
        aborted = True
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, stop)
    with (RUN / 'requests.jsonl').open('x') as stream:
        for second in range(total):
            if aborted:
                break
            time.sleep(max(0, start + second - time.time()))
            if time.time() - (start + second) > 1:
                raise RuntimeError('Request schedule missed; fail closed')
            phase = phase_at(second)
            row = request(TARGET_PATH, phase == 'during', phase)
            rows.append(row)
            stream.write(json.dumps(row, separators=(',', ':')) + '\n')
            if second % 5 == 0:
                for control in CONTROL_PATHS:
                    control_row = request(control, False, phase)
                    rows.append(control_row)
                    stream.write(json.dumps(control_row, separators=(',', ':')) + '\n')
            stream.flush()
            if second % 15 == 0:
                check = health()
                if any(check['collector_refused'][k] > initial['collector_refused'][k]
                       for k in ('grpc', 'http')):
                    raise RuntimeError('Collector refusal increased')
    if aborted:
        raise RuntimeError('Systemd or operator stopped request-scoped fault')
    time.sleep(max(0, start + total - time.time()))
    final = health()
    root = {phase: [r for r in rows if r['path'] == TARGET_PATH and r['phase'] == phase]
            for phase in PHASE_SECONDS}
    median = {phase: statistics.median(r['latency_ms'] for r in group)
              for phase, group in root.items()}
    checks = {'all_planned_target_requests_recorded': all(
        len(root[phase]) == PHASE_SECONDS[phase] for phase in PHASE_SECONDS),
        'root_requests_successful': all(r['status'] == 200 for group in root.values() for r in group),
        'delay_isolated_to_registered_window': all(
            r['delay_header'] == (phase == 'during') for phase, group in root.items() for r in group),
        'controls_unmodified_and_successful': all(not r['delay_header'] and r['status'] == 200
            for r in rows if r['path'] in CONTROL_PATHS),
        'direct_latency_signal': median['during'] - max(median['before'], median['after']) >= 180,
        'collector_refused_unchanged': final['collector_refused'] == initial['collector_refused'],
        'proxy_identity_stable': final['proxy_uid'] == initial['proxy_uid']}
    result = {'status': 'HTTP_REQDELAY_REHEARSAL_PASS_PENDING_TRACE_AND_FORMAL_ADAPTER'
              if all(checks.values()) else 'HTTP_REQDELAY_REHEARSAL_NOT_QUALIFIED',
              'formal_case_created': False, 'actual_end_utc': now(),
              'counts': {phase: len(group) for phase, group in root.items()},
              'median_latency_ms': median, 'checks': checks,
              'initial_health': initial, 'final_health': final,
              'requests_sha256': sha(RUN / 'requests.jsonl'),
              'root_cause_evidence_from_public_latency_only': True,
              'formal_production_authorized': False}
    write_new(RUN / 'result.json', result)
    print(json.dumps(result))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('register')
    p.add_argument('--residue-audit', required=True)
    p = sub.add_parser('run')
    p.add_argument('--registration', required=True)
    p.add_argument('--registration-sha256', required=True)
    args = parser.parse_args()
    if args.command == 'register':
        register(args.residue_audit)
    else:
        run(args.registration, args.registration_sha256)


if __name__ == '__main__':
    main()
