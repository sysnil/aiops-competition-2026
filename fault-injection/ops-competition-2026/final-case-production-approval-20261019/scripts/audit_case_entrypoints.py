#!/usr/bin/env python3
"""Read-only inventory of actual draft targets and legacy effect entrypoints."""

import argparse
import ast
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
PREREG = OPS / 'final-cases-20261019/design/SF-013-to-SF-017-preregistration-draft.json'
POD_AUTHORITY = OPS / 'final-cases-20261019/design/SF-013-plan-authority-att02.json'
LEGACY = {
    'SERVICE_PAUSE_30': OPS / 'sf007-real-adapter-v7-20261018/scripts/fault_worker.py',
    'POD_MEMORY_32M_300': OPS / 'sf012-pod300-adapter-v4-20261018/scripts/fault_worker.py',
    'HTTP_HOME_DELAY_250MS_60': OPS / 'sf011-http-adapter-v5-20261018/scripts/sf004_workload.py',
}
HTTP_REGISTRY = OPS / 'sf011-http-adapter-v5-20261018/scripts/run_registry.py'
W2_GATE = OPS / 'sf013-adapter-v4-20261019/scripts/fault_worker.py'
W3_MARKER = OPS / 'final-case-production-approval-20261019/worker3-auth/scripts/marker_worker.py'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def code_flags(path):
    tree = ast.parse(path.read_text())
    imported_live_auth = any(isinstance(node, ast.ImportFrom) and
        node.module == 'worker_auth' and
        any(alias.name == 'execute_once' for alias in node.names)
        for node in ast.walk(tree))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    text = path.read_text()
    return {'source_sha256': sha(path),
            'imports_live_execute_once': imported_live_auth,
            'uses_local_lease': 'validate_lease' in names or
                                'lease_valid' in names or
                                'lease_path' in text,
            'sets_http_fault_header': 'x-envoy-fault-delay-request' in text}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError('Immutable entrypoint inventory already exists')
    prereg = json.loads(PREREG.read_text())
    authority = json.loads(POD_AUTHORITY.read_text())
    pods = json.loads(subprocess.check_output(
        ['kubectl', '-n', 'otel-demo', 'get', 'pods', '-o', 'json'],
        text=True, timeout=20))
    placement = {}
    for item in pods['items']:
        name = item['metadata']['name']
        for prefix in ('frontend-', 'frontend-proxy-', 'recommendation-'):
            if name.startswith(prefix):
                placement[name] = {'uid': item['metadata']['uid'],
                                   'node': item['spec'].get('nodeName'),
                                   'phase': item['status'].get('phase')}
    mechanisms = {key: {'legacy_source': str(path), **code_flags(path)}
                  for key, path in LEGACY.items()}
    http_registry = HTTP_REGISTRY.read_text()
    cases = []
    for case in prereg['cases']:
        ident = case['case_id']
        if ident == 'SF-013':
            required = ['POD_MEMORY_32M_300']
            worker = ['k8s-worker2']
            detail = 'Authority selects Worker2 PodMemory300 fallback; CPU stress blocked.'
        elif ident == 'SF-014':
            required = ['SERVICE_PAUSE_30']
            worker = ['k8s-worker3']
            detail = 'Two current recommendation replicas are on Worker3.'
        elif ident == 'SF-015':
            required = ['HTTP_HOME_DELAY_250MS_60']
            worker = []
            detail = 'Master-local workload header/lease; frontend-proxy Pod placement is not an effect Timer.'
        elif ident == 'SF-016':
            required = ['POD_MEMORY_32M_300']
            worker = ['k8s-worker2']
            detail = 'Worker2 PodMemory300 requires its own SF-016 binding; SF-013 backend is case-locked.'
        else:
            required = ['HTTP_HOME_DELAY_250MS_60', 'SERVICE_PAUSE_30']
            worker = ['k8s-worker3']
            detail = 'Two separate effects: Master-local HTTP lease and Worker3 ServicePause Timer.'
        cases.append({'case_id': ident, 'mechanisms': required,
                      'effect_workers': worker,
                      'legacy_entrypoint_bypass_present': any(
                          not mechanisms[mechanism]['imports_live_execute_once']
                          for mechanism in required),
                      'formal_live_auth_case_binding_complete': False,
                      'status': 'BLOCKED_BEFORE_REAL_INJECTION',
                      'notes': detail})
    result = {
        'status': 'FIVE_CASE_ENTRYPOINTS_AUDITED_PRODUCTION_BLOCKED',
        'at_utc': datetime.now(timezone.utc).isoformat(),
        'prereg_sha256': sha(PREREG),
        'sf013_authority_sha256': sha(POD_AUTHORITY),
        'current_placement': placement,
        'legacy_mechanisms': mechanisms,
        'http_registry_no_worker_timer': "m['actions']!=[]" in http_registry and
                                         'NO_WORKER_TIMER_SCOPE_REQUIRED' in http_registry,
        'worker2_v4_gate_sha256': sha(W2_GATE),
        'worker3_marker_only_sha256': sha(W3_MARKER),
        'cases': cases,
        'real_faults_injected': 0,
        'historical_code_modified': False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': result['status'],
                      'cases': [(row['case_id'], row['status'])
                                for row in cases],
                      'http_has_worker_timer': not result['http_registry_no_worker_timer']}))


if __name__ == '__main__':
    main()
