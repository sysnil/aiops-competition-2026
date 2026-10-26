#!/usr/bin/env python3
"""Bind a passed independent rehearsal to one future formal Action identity."""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
FORMAL = OPS / 'final-case-formal-entry-v6-20261020/scripts'
sys.path.insert(0, str(FORMAL))
from control_contract import WORKER_ADAPTER, qualification_scope, sha
from run_qualification import CASES, save


def issue(decision_path, qualification_registration, action_path):
    decision = json.loads(decision_path.read_text())
    qualification = json.loads(qualification_registration.read_text())
    action = json.loads(action_path.read_text())
    case = action['run_id'].split('-ATTEMPT-', 1)[0]
    if case not in CASES or action['kind'] == 'HTTP':
        raise ValueError('WORKER_QUALIFICATION_CASE_REQUIRED')
    layer, worker, service, duration = CASES[case]
    if (decision.get('status') != 'QUALIFIED_FOR_THIS_VERSION' or
            decision.get('blockers') or
            decision.get('case') != case or
            decision.get('qualification_id') != qualification.get('id') or
            decision.get('registration_sha256') != sha(qualification_registration) or
            decision.get('adapter_sha256') != sha(WORKER_ADAPTER)):
        raise ValueError('QUALIFICATION_DECISION_NOT_BOUND_OR_NOT_PASSED')
    if (action.get('kind') != layer or action.get('worker') != worker or
            action.get('mechanism') != ('PodMemoryStress' if layer == 'POD'
                                      else 'ServicePause') or
            action.get('duration_seconds') != duration or
            action.get('action_id') != action['run_id'] + '-' + layer):
        raise ValueError('FORMAL_ACTION_DIFFERS_FROM_QUALIFICATION')
    formal_registration = Path(action['qualified_registration'])
    if (sha(formal_registration) != action['qualified_registration_sha256'] or
            formal_registration.is_symlink()):
        raise ValueError('FORMAL_TARGET_REGISTRATION_HASH')
    formal = json.loads(formal_registration.read_text())
    keys = ('service', 'uid', 'container_id', 'node', 'pid', 'start_ticks', 'cgroup')
    identity = lambda rows: sorted(tuple(row[key] for key in keys) for row in rows)
    if (formal.get('id') != action['action_id'] or
            formal.get('adapter_sha256') != sha(WORKER_ADAPTER) or
            identity(formal['targets']) != identity(qualification['targets'])):
        raise ValueError('FORMAL_TARGET_IDENTITY_NOT_QUALIFIED')
    return {'status': 'QUALIFIED_FOR_THIS_VERSION',
            'run_id': action['run_id'], 'action_id': action['action_id'],
            'scope_sha256': qualification_scope(action),
            'adapter_sha256': sha(WORKER_ADAPTER),
            'qualification_id': qualification['id'],
            'qualification_decision_sha256': sha(decision_path),
            'qualification_registration_sha256': sha(qualification_registration),
            'formal_registration_sha256': sha(formal_registration),
            'source': 'INDEPENDENT_REAL_BUSINESS_MECHANISM_QUALIFICATION',
            'issued_utc': datetime.now(timezone.utc).isoformat()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--decision', required=True, type=Path)
    parser.add_argument('--qualification-registration', required=True, type=Path)
    parser.add_argument('--action', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('QUALIFICATION_RECEIPT_IMMUTABLE')
    receipt = issue(args.decision, args.qualification_registration,
                    args.action)
    save(args.output, receipt)
    print(json.dumps({'status': receipt['status'],
        'action_id': receipt['action_id'], 'receipt_sha256': sha(args.output)}))


if __name__ == '__main__':
    main()
