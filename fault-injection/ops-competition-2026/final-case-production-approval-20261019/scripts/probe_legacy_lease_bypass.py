#!/usr/bin/env python3
"""Execute only historical lease predicates with synthetic inputs, never effects."""

import argparse
import ast
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
SOURCES = {
    'service_worker3': (OPS / 'sf007-real-adapter-v7-20261018/scripts/fault_worker.py',
                        'validate_lease'),
    'pod_worker2': (OPS / 'sf012-pod300-adapter-v4-20261018/scripts/fault_worker.py',
                    'validate_lease'),
    'http_master': (OPS / 'sf011-http-adapter-v5-20261018/scripts/sf004_workload.py',
                     'lease_valid'),
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def predicate(path, name):
    source = ast.parse(path.read_text())
    function = next(node for node in source.body
                    if isinstance(node, ast.FunctionDef) and node.name == name)
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    scope = {'stamp': timestamp, 'timestamp': timestamp,
             'RuntimeError': RuntimeError}
    exec(compile(module, str(path), 'exec'), scope)
    return scope[name]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError('Immutable legacy predicate evidence exists')
    at = datetime.now(timezone.utc).timestamp()
    issued = datetime.fromtimestamp(at - .1, timezone.utc).isoformat()
    expires = datetime.fromtimestamp(at + 3, timezone.utc).isoformat()
    lease = {'run_id': 'SYNTHETIC-ONLY', 'action_id': 'SYNTHETIC-ONLY-POD',
             'manifest_sha256': 'a' * 64, 'permission': 'BOUND_QUALIFIED_FAULT',
             'issued_utc': issued, 'expires_utc': expires,
             'safety_pass': True, 'authorization_sha256': 'b' * 64,
             'clock': {'measured_at_master_utc': issued,
                       'worker_minus_master_seconds': 0,
                       'uncertainty_seconds': .01}}
    worker = {}
    for name in ('service_worker3', 'pod_worker2'):
        path, function = SOURCES[name]
        validator = predicate(path, function)
        validator({'run_id': lease['run_id']}, 'a' * 64,
                  {'action_id': lease['action_id']}, lease, at, False)
        worker[name] = {'local_lease_accepted_without_master_rpc': True,
                        'source_sha256': sha(path)}
    path, function = SOURCES['http_master']
    http = predicate(path, function)
    http_lease = {'run_id': 'SF-011-ATTEMPT-05',
                  'plan_sha256': 'c' * 64,
                  'permission': 'BOUND_HTTP_REQDELAY',
                  'safety_pass': True,
                  'issued_utc': issued, 'expires_utc': expires}
    worker['http_master'] = {
        'local_lease_accepted_without_controller_check':
            bool(http(http_lease, 'c' * 64, at)),
        'source_sha256': sha(path)}
    result = {'status': 'LEGACY_LOCAL_LEASE_BYPASS_CONFIRMED'
              if all(row.get('local_lease_accepted_without_master_rpc') or
                     row.get('local_lease_accepted_without_controller_check')
                     for row in worker.values()) else 'LEGACY_PREDICATE_UNEXPECTED',
              'at_utc': datetime.now(timezone.utc).isoformat(),
              'synthetic_only': True, 'real_faults_injected': 0,
              'no_network_request_sent': True,
              'predicates': worker}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': result['status'],
                      'predicates': list(worker)}))
    if result['status'] != 'LEGACY_LOCAL_LEASE_BYPASS_CONFIRMED':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
