#!/usr/bin/env python3
"""Preserve the registered plan and actual fault evidence in Private only."""

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
RUN_ID = 'SF-004-ATTEMPT-03'
RAW = Path('/data/stage5-v3/ops-competition-2026/runs') / RUN_ID
PRIVATE = Path('/data/stage5-v3/ops-competition-2026/packages') / RUN_ID / 'private'
PLAN = OPS / 'sf004-real-adapter-v4-20261017/plans' / RUN_ID / 'run-plan.json'
ADMISSION = OPS / 'sf004-finalization-v4-20261017/http-admission.json'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def main():
    plan = read(PLAN)
    capture_path = RAW / 'terminal.json'
    fault_path = RAW / 'control/fault-controller-terminal.json'
    capture, fault = map(read, (capture_path, fault_path))
    if (plan['id'] != RUN_ID or
            capture['status'] != 'SF004_FORMAL_CAPTURE_PASS_PENDING_EXPORT' or
            fault['status'] != 'SF004_FAULT_SEQUENCE_COMPLETE_PENDING_CAPTURE' or
            fault['faults'] or plan['actions'] or
            fault['stages'].get('http_recovered') is not True):
        raise RuntimeError('Formal HTTP-only capture or recovery incomplete')
    t0 = stamp(plan['start_utc'])
    start, end = t0 + 900, t0 + 960
    header_paths = (RAW / 'state-machine/home-header-events.jsonl',
                    RAW / 'controls/home-header-events.jsonl')
    headers = [json.loads(line) for path in header_paths
               for line in path.read_text().splitlines()]
    applied = [row for row in headers if row['delay_header_applied']]
    if (len(applied) != fault['stages']['http_delay']['delayed_requests'] or
            len(applied) < 60 or
            any(not start <= stamp(row['at_utc']) < end or
                row['requested_delay_ms'] != 250 for row in applied) or
            any(start <= stamp(row['at_utc']) < end and
                not row['delay_header_applied'] for row in headers)):
        raise RuntimeError('HTTP delay execution evidence differs from registered window')
    if ((PRIVATE / 'injection-plan.json').exists() or
            (PRIVATE / 'injection-record.json').exists()):
        raise RuntimeError('Private injection evidence already finalized')
    admission = read(ADMISSION)
    if admission['case_id'] != 'SF-004' or admission['status'] not in (
            'SF004_HTTP_HOME_ADMISSION_PASS', 'SF004_HTTP_HOME_ADMISSION_FAILED'):
        raise RuntimeError('Independent observability admission absent')
    shutil.copy2(PLAN, PRIVATE / 'injection-plan.json')
    record = {'run_id': RUN_ID, 'case_id': 'SF-004',
              'status': 'ACTUAL_FAULT_EVIDENCE_PRESERVED',
              'plan_sha256': sha(PLAN),
              'capture_terminal_sha256': sha(capture_path),
              'fault_terminal_sha256': sha(fault_path),
              'no_worker_fault_or_timer': True,
              'observability_admission_status': admission['status'],
              'observability_admission_sha256': sha(ADMISSION),
              'ready_for_import': admission['status'] == 'SF004_HTTP_HOME_ADMISSION_PASS',
              'http_request_delay': {
                  'planned_start_utc': datetime.fromtimestamp(start, timezone.utc).isoformat(),
                  'planned_end_utc': datetime.fromtimestamp(end, timezone.utc).isoformat(),
                  'configured_delay_ms': 250,
                  'observed_delayed_request_count': len(applied),
                  'first_observed_utc': applied[0]['at_utc'],
                  'last_observed_utc': applied[-1]['at_utc'],
                  'header_events_sha256': {str(path): sha(path) for path in header_paths}},
              'recovery_precheck_passed': fault['stages']['http_recovered'],
              'public_created_separately': True,
              'anomaly_gt_status': 'DRAFT_PENDING_HUMAN_REVIEW'}
    with (PRIVATE / 'injection-record.json').open('x') as stream:
        json.dump(record, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': record['status'],
                      'http_delayed_requests': len(applied),
                      'injection_plan_sha256': sha(PRIVATE / 'injection-plan.json'),
                      'injection_record_sha256': sha(PRIVATE / 'injection-record.json')}))


if __name__ == '__main__':
    main()
