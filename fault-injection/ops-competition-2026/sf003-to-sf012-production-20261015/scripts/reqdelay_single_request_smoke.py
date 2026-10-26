#!/usr/bin/env python3
"""One bounded request-header delay probe. No persistent fault is installed."""

import hashlib
import json
import secrets
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path('/root/ops-competition-2026/sf003-to-sf012-production-20261015')
OUT = ROOT / 'results/reqdelay-single-request-smoke.json'
BASE = 'http://10.104.177.128:8080'
PROM = 'http://10.97.14.138:9090'
OS = 'http://10.111.114.255:9200'
KUBE = ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '-n', 'otel-demo']
HEADER = 'x-envoy-fault-delay-request: 250'


def now():
    return datetime.now(timezone.utc).isoformat()


def collector():
    query = '{__name__="otelcol_receiver_refused_spans_total",k8s_pod_name="otel-collector-845fdc8d87-gk2gh"}'
    url = PROM + '/api/v1/query?' + urllib.parse.urlencode({'query': query})
    with urllib.request.urlopen(url, timeout=10) as stream:
        body = json.load(stream)
    if body.get('status') != 'success':
        raise RuntimeError('Collector refusal metric query failed')
    rows = body['data']['result']
    values = {row['metric'].get('transport'): float(row['value'][1]) for row in rows}
    if set(values) != {'grpc', 'http'}:
        raise RuntimeError('Collector refused counters incomplete')
    return values


def request(path, delay=False):
    trace_id = secrets.token_hex(16)
    span_id = secrets.token_hex(8)
    args = ['curl', '--silent', '--show-error', '--max-time', '5',
            '--output', '/dev/null', '--write-out', '%{http_code} %{time_total}',
            '--header', 'traceparent: 00-' + trace_id + '-' + span_id + '-01']
    if delay:
        args += ['--header', HEADER]
    args.append(BASE + path)
    began = now()
    result = subprocess.run(args, capture_output=True, text=True, timeout=7)
    fields = result.stdout.strip().split()
    return {
        'path': path, 'delay_header_present': delay, 'requested_delay_ms': 250 if delay else None,
        'started_utc': began, 'completed_utc': now(), 'trace_id': trace_id,
        'curl_exit': result.returncode,
        'http_code': fields[0] if len(fields) == 2 else None,
        'duration_seconds': float(fields[1]) if len(fields) == 2 else None,
        'stderr': result.stderr[-500:],
    }


def main():
    if OUT.exists():
        raise RuntimeError('No overwrite of probe evidence')
    pods = json.loads(subprocess.check_output(KUBE + ['get', 'pods', '-o', 'json'], text=True, timeout=20))['items']
    proxy = [pod for pod in pods if pod['metadata']['name'].startswith('frontend-proxy-')]
    if len(proxy) != 1 or not all(x.get('ready') for x in proxy[0]['status']['containerStatuses']):
        raise RuntimeError('Proxy not uniquely ready')
    units = subprocess.check_output(['systemctl', 'list-units', '--type=service',
                                     '--state=running', '--no-legend', '--no-pager'],
                                    text=True, timeout=10)
    if any(token in units for token in ('ops-sf30-', 'ops-sf002-', 'ops-b8-')):
        raise RuntimeError('Another competition run is active')
    cm = json.loads(subprocess.check_output(
        KUBE + ['get', 'configmap', 's5v3ap-frontend-proxy-idle-c2', '-o', 'json'],
        text=True, timeout=20))
    template = cm['data']['envoy.tmpl.yaml']
    if template.count('envoy.filters.http.fault') != 1 or 'header_delay: {}' not in template:
        raise RuntimeError('Live Envoy request-delay primitive changed')
    initial = collector()
    rows = [request('/'), request('/', delay=True), request('/'),
            request('/images/products/OpticalTubeAssembly.jpg'), request('/api/cart')]
    final = collector()
    baseline, fault, recovered, image, cart = rows
    checks = {
        'all_http_200': all(row['curl_exit'] == 0 and row['http_code'] == '200' for row in rows),
        'single_request_delay_observed': (
            fault['duration_seconds'] is not None and baseline['duration_seconds'] is not None
            and recovered['duration_seconds'] is not None
            and fault['duration_seconds'] - max(baseline['duration_seconds'],
                                                 recovered['duration_seconds']) >= 0.18
            and fault['duration_seconds'] < 2),
        'controls_not_fault_header_targeted': not image['delay_header_present'] and not cart['delay_header_present'],
        'collector_refused_unchanged': initial == final,
    }
    payload = {
        'status': 'HTTP_REQDELAY_SINGLE_REQUEST_PRIMITIVE_PASS_PENDING_MECHANISM_QUALIFICATION'
        if all(checks.values()) else 'HTTP_REQDELAY_PRIMITIVE_NOT_VERIFIED',
        'at_utc': now(),
        'proxy_uid': proxy[0]['metadata']['uid'],
        'envoy_template_sha256': hashlib.sha256(template.encode()).hexdigest(),
        'request_scope': 'one ordinary HTTP frontend request; only this request carried delay header',
        'bounded_client_timeout_seconds': 5,
        'requests': rows,
        'collector_refused_initial': initial,
        'collector_refused_final': final,
        'checks': checks,
        'persistent_fault_installed': False,
        'watchdog_for_multi_request_fault_qualified': False,
        'formal_execution_authorized': False,
        'faults_injected_as_formal_case': 0,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open('x') as stream:
        json.dump(payload, stream, indent=2)
    print(json.dumps({'status': payload['status'], 'durations_seconds': [
        row['duration_seconds'] for row in rows], 'checks': checks}))


if __name__ == '__main__':
    main()
