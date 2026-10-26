#!/usr/bin/env python3
"""Bind a passed real HTTP rehearsal to one formal Master HTTP Action."""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
FORMAL = OPS / 'final-case-formal-entry-v7-20261020/scripts'
sys.path.insert(0, str(FORMAL))
from control_contract import MASTER_HTTP, qualification_scope, sha
from http_qual import CASES


def issue(decision_path, plan_path, action_path):
    decision = json.loads(decision_path.read_text())
    plan = json.loads(plan_path.read_text())
    action = json.loads(action_path.read_text())
    case = action['run_id'].split('-ATTEMPT-', 1)[0]
    if (case not in CASES or
            decision.get('status') != 'QUALIFIED_FOR_THIS_VERSION' or
            decision.get('blockers') or
            decision.get('case') != case or
            decision.get('qualification_id') != plan.get('run_id') or
            decision.get('plan_sha256') != sha(plan_path) or
            decision.get('adapter_sha256') != sha(MASTER_HTTP) or
            decision.get('duration_seconds') != CASES[case] or
            action.get('kind') != 'HTTP' or
            action.get('worker') != 'k8s-master' or
            action.get('mechanism') != 'HTTPReqDelay' or
            action.get('duration_seconds') != CASES[case] or
            action.get('action_id') != action['run_id'] + '-HTTP' or
            action.get('min_fault_requests', 0) <
                plan['action'].get('min_fault_requests', 1)):
        raise ValueError('HTTP_QUALIFICATION_DECISION_OR_ACTION_MISMATCH')
    return {'status': 'QUALIFIED_FOR_THIS_VERSION',
            'run_id': action['run_id'], 'action_id': action['action_id'],
            'scope_sha256': qualification_scope(action),
            'adapter_sha256': sha(MASTER_HTTP),
            'qualification_id': plan['run_id'],
            'qualification_decision_sha256': sha(decision_path),
            'qualification_plan_sha256': sha(plan_path),
            'source': 'INDEPENDENT_REAL_BUSINESS_HTTP_QUALIFICATION',
            'issued_utc': datetime.now(timezone.utc).isoformat()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--decision', required=True, type=Path)
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--action', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('HTTP_QUALIFICATION_RECEIPT_IMMUTABLE')
    result = issue(args.decision, args.plan, args.action)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
        stream.write('\n')
    args.output.chmod(0o600)
    print(json.dumps({'status': result['status'],
                      'action_id': result['action_id'],
                      'receipt_sha256': sha(args.output)}))


if __name__ == '__main__':
    main()
