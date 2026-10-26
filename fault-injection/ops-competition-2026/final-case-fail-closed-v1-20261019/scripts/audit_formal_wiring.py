#!/usr/bin/env python3
"""AST audit that formal and Marker entries share the live authorization core."""

import argparse
import ast
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
ROOT = OPS / 'final-case-fail-closed-v1-20261019'
FORMAL = OPS / 'sf013-adapter-v4-20261019/scripts'
FILES = {
    'marker_worker': ROOT / 'scripts/marker_worker.py',
    'fault_worker': FORMAL / 'fault_worker.py',
    'adapter': FORMAL / 'qualified_adapter.py',
    'controller': FORMAL / 'sf003_controller.py',
    'guard': FORMAL / 'timer_guard.py',
    'registry': FORMAL / 'run_registry.py',
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def imports(tree, module, symbol):
    return any(isinstance(node, ast.ImportFrom) and node.module == module and
               symbol in [alias.name for alias in node.names]
               for node in ast.walk(tree))


def calls(tree, name):
    return sum(isinstance(node, ast.Call) and
               isinstance(node.func, ast.Name) and node.func.id == name
               for node in ast.walk(tree))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError('Immutable wiring audit output exists')
    source = {name: path.read_text() for name, path in FILES.items()}
    trees = {name: ast.parse(value) for name, value in source.items()}
    adapter_run = next(node for node in trees['adapter'].body
                       if isinstance(node, ast.FunctionDef) and node.name == 'run')
    checks = {
        'marker_uses_shared_execute_once': imports(trees['marker_worker'],
                                                    'worker_auth', 'execute_once') and
            calls(trees['marker_worker'], 'execute_once') == 1,
        'formal_uses_shared_execute_once': imports(trees['fault_worker'],
                                                    'worker_auth', 'execute_once') and
            calls(trees['fault_worker'], 'execute_once') == 1,
        'same_exact_timer_revoke_module': all(
            imports(trees[name], 'timer_lifecycle', 'revoke')
            for name in ('marker_worker', 'fault_worker')),
        'adapter_requires_effect_callback': 'before_effect' in
            [arg.arg for arg in adapter_run.args.args] and
            calls(adapter_run, 'before_effect') >= 2,
        'direct_adapter_run_rejected':
            'DIRECT_RUN_FORBIDDEN_USE_BOUND_WORKER_TIMER_ENTRY' in source['adapter'],
        'master_controller_permits_and_revokes':
            calls(trees['controller'], 'permit_live_auth') == 1 and
            calls(trees['controller'], 'revoke_live_auth') >= 1,
        'master_guard_revokes_before_worker_cancel':
            calls(trees['guard'], 'revoke_live_auth') >= 1,
        'formal_timer_targets_new_entry':
            'scripts/fault_worker.py' in source['registry'] and
            'sf013-adapter-v4-20261019' in source['registry'],
        'no_legacy_v3_in_formal_sources': all(
            'sf013-adapter-v3-20261019' not in value and
            'sf013-formal-v3-20261019' not in value
            for value in source.values()),
    }
    result = {'status': 'FORMAL_MARKER_AUTH_WIRING_STATIC_PASS'
              if all(checks.values()) else 'FORMAL_MARKER_AUTH_WIRING_STATIC_BLOCKED',
              'at_utc': datetime.now(timezone.utc).isoformat(),
              'checks': checks,
              'source_sha256': {name: sha(path) for name, path in FILES.items()},
              'real_faults_injected': 0,
              'scope': 'AST/source wiring only; no formal case or adapter run'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': result['status'], 'checks': checks}))
    if result['status'] != 'FORMAL_MARKER_AUTH_WIRING_STATIC_PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
