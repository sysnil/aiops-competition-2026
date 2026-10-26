#!/usr/bin/env python3
"""Localhost-only request-header authorization and revocation rehearsal."""

import json
import os
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SHARED = Path('/root/ops-competition-2026/final-case-unified-execution-v1-20261019/scripts')
FORMAL = Path('/root/ops-competition-2026/final-case-formal-entry-v3-20261019/scripts')
ROOT = Path('/root/ops-competition-2026/final-case-qualification-prep-20261019/results')
sys.path[:0] = [str(FORMAL), str(SHARED)]
from action_decision import revoke
from http_workload import request_factory


def ticks(pid):
    return int(Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19])


def main():
    run = 'SF-015-ATTEMPT-MARKERTEST-HTTPL001'
    action_id = run + '-HTTP'
    runtime = ROOT / run
    runtime.mkdir(parents=True, mode=0o700, exist_ok=False)
    action = {'run_id': run, 'action_id': action_id, 'kind': 'HTTP',
              'worker': 'k8s-master', 'mechanism': 'HTTPReqDelay',
              'duration_seconds': 60, 'manifest_sha256': 'a' * 64,
              'capability': 'BOUND_QUALIFIED_FAULT',
              'entrypoint': str(SHARED / 'http_gate.py')}
    current = time.time()
    process = os.getpid()
    state = {'status': 'REGISTERED', 'run_id': run, 'action_id': action_id,
             'manifest_sha256': action['manifest_sha256'],
             'planned_start_epoch': current - 1, 'planned_end_epoch': current + 20,
             'requester_pid': process, 'requester_start_ticks': ticks(process),
             'controller_pid': process, 'controller_start_ticks': ticks(process),
             'controller_unit': 'ops-sf015-localhost-control',
             'guard_unit': 'ops-sf015-localhost-guard'}
    permit = {'status': 'PERMITTED', 'run_id': run, 'action_id': action_id,
              'manifest_sha256': action['manifest_sha256'],
              'issued_epoch': current - 2, 'expires_epoch': current + 20}
    (runtime / 'state.json').write_text(json.dumps(state) + '\n')
    (runtime / 'permit.json').write_text(json.dumps(permit) + '\n')
    observed = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            observed.append({'path': self.path,
                             'fault_header': self.headers.get('x-envoy-fault-delay-request')})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'OK')

        def log_message(self, *args):
            pass

    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    audit = []
    try:
        request = request_factory(action, runtime, urllib.request.Request,
            start=current - 1, end=current + 20, audit=audit.append,
            units=lambda name: {'ActiveState': 'active', 'MainPID': str(process)},
            ticks=lambda pid: ticks(pid))
        base = 'http://127.0.0.1:' + str(server.server_port)
        for route in ('/images/products/OpticalTubeAssembly.jpg', '/api/cart', '/'):
            with urllib.request.urlopen(request(base + route, method='GET'), timeout=3) as response:
                response.read()
        revoke(runtime, {'run_id': run, 'action_id': action_id,
                         'manifest_sha256': action['manifest_sha256']}, 'LOCALHOST_TEST')
        with urllib.request.urlopen(request(base + '/', method='GET'), timeout=3) as response:
            response.read()
        passed = ([row['fault_header'] for row in observed] ==
                  [None, None, '250', None] and
                  audit[-1]['status'] == 'DENIED_NORMAL_REQUEST')
        result = {'status': 'LOCALHOST_HTTP_GATE_PASS' if passed else 'LOCALHOST_HTTP_GATE_FAIL',
                  'localhost_only': True, 'business_service_contacted': False,
                  'request_headers': observed, 'audit': audit}
        (runtime / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result))
        if not passed:
            raise SystemExit(1)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == '__main__':
    main()
