#!/usr/bin/env python3
"""Assess retained harmless terminal records without changing any attempt."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


root = Path(__file__).resolve().parents[1]
cases = {}
for case in ('SF-013', 'SF-014', 'SF-015', 'SF-016', 'SF-017'):
    path = root / 'results' / (case.lower() + '-harmless-control.json')
    row = json.loads(path.read_text())
    actions = row['actions']
    passed = (
        row['case'] == case and row['real_faults_injected'] == 0 and
        row['controller']['status'] == 'CONTROL_SEQUENCE_COMPLETE_PENDING_ADMISSION' and
        row['guard']['status'] == 'GUARD_CLEANUP_VERIFIED' and
        all(item['revoke'] is not None and item['worker_terminal'] is not None
            for item in actions.values()) and
        all(item['worker_terminal'].get('status') == 'ACTION_TERMINAL'
            for name, item in actions.items() if name != 'HTTP') and
        all(item['worker_terminal'].get('status') == 'HARMLESS_HTTP_WINDOW_COMPLETE'
            and item['worker_terminal'].get('fault_headers_sent') == 0
            for name, item in actions.items() if name == 'HTTP'))
    cases[case] = {'status': 'HARMLESS_CHAIN_PASS' if passed else 'INCOMPLETE',
                   'run_id': row['run_id'],
                   'manifest_sha256': row['manifest_sha256'],
                   'evidence_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}

abort_path = root / 'results/sf-017-abort-harmless-control.json'
abort = json.loads(abort_path.read_text())
abort_pass = (abort['controller'] is None and
              abort['guard']['status'] == 'GUARD_CLEANUP_VERIFIED' and
              all(item['fired_after_abort'] is None and item['revoked'] is not None
                  for item in abort['actions'].values()))
result = {'status': 'HARMLESS_INTEGRATION_PASS' if
          all(item['status'] == 'HARMLESS_CHAIN_PASS' for item in cases.values())
          and abort_pass else 'HARMLESS_INTEGRATION_INCOMPLETE',
          'production_status': 'PRODUCTION_BLOCKED',
          'real_faults_injected': 0, 'formal_attempts_created': 0,
          'created_utc': datetime.now(timezone.utc).isoformat(),
          'cases': cases,
          'sf017_controller_exit': {
              'status': 'GUARD_CANCEL_PASS' if abort_pass else 'INCOMPLETE',
              'run_id': abort['run_id'],
              'evidence_sha256': hashlib.sha256(abort_path.read_bytes()).hexdigest()}}
output = root / 'results/integration-assessment.json'
output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
print(output)
if result['status'] != 'HARMLESS_INTEGRATION_PASS':
    raise SystemExit(1)
