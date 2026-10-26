#!/usr/bin/env python3
"""Preserve the registered plan and actual fault evidence in Private only."""

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
RUN_ID = 'SF-010-ATTEMPT-07'
RAW = Path('/data/stage5-v3/ops-competition-2026/runs') / RUN_ID
PRIVATE = Path('/data/stage5-v3/ops-competition-2026/exports') / RUN_ID / 'private'
PLAN = OPS / 'sf010-real-adapter-v7-20261016/plans' / RUN_ID / 'run-plan.json'


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
    worker_path = RAW / 'control/service-worker-runtime/terminal.json'
    capture, fault, worker = map(read, (capture_path, fault_path, worker_path))
    if (plan['id'] != RUN_ID or
            capture['status'] != 'SF010_FORMAL_CAPTURE_PASS_PENDING_EXPORT' or
            fault['status'] != 'SF010_FAULT_SEQUENCE_COMPLETE_PENDING_CAPTURE' or
            worker['status'] != 'FORMAL_FAULT_COMPLETE'):
        raise RuntimeError('Formal capture or both-fault evidence incomplete')
    service = fault['faults'][RUN_ID + '-SERVICE']
    if (service['cleanup']['status'] != 'COMPETITION_CLEANUP_ACTIONS_VERIFIED' or
            abs(service['duration_seconds'] - 30) > 2 or
            not fault['actual_onsets'][RUN_ID + '-SERVICE']['pass']):
        raise RuntimeError('Service injection or cleanup did not satisfy contract')
    t0 = stamp(plan['start_utc'])
    start, end = t0 + 960, t0 + 1020
    headers = [json.loads(line) for line in
               (RAW / 'state-machine/home-header-events.jsonl').read_text().splitlines()]
    applied = [row for row in headers if row['delay_header_applied']]
    if (len(applied) != fault['stages']['http_delay']['delayed_home_requests'] or
            len(applied) < 60 or
            any(not start <= stamp(row['at_utc']) < end or
                row['requested_delay_ms'] != 250 for row in applied) or
            any(start <= stamp(row['at_utc']) < end and
                not row['delay_header_applied'] for row in headers)):
        raise RuntimeError('HTTP delay execution evidence differs from registered window')
    if ((PRIVATE / 'injection-plan.json').exists() or
            (PRIVATE / 'injection-record.json').exists()):
        raise RuntimeError('Private injection evidence already finalized')
    shutil.copy2(PLAN, PRIVATE / 'injection-plan.json')
    record = {'run_id': RUN_ID, 'case_id': 'SF-010',
              'status': 'ACTUAL_FAULT_EVIDENCE_PRESERVED',
              'plan_sha256': sha(PLAN),
              'capture_terminal_sha256': sha(capture_path),
              'fault_terminal_sha256': sha(fault_path),
              'worker_terminal_sha256': sha(worker_path),
              'service': service,
              'service_onset': fault['actual_onsets'][RUN_ID + '-SERVICE'],
              'http_request_delay': {
                  'planned_start_utc': datetime.fromtimestamp(start, timezone.utc).isoformat(),
                  'planned_end_utc': datetime.fromtimestamp(end, timezone.utc).isoformat(),
                  'configured_delay_ms': 250,
                  'observed_delayed_request_count': len(applied),
                  'first_observed_utc': applied[0]['at_utc'],
                  'last_observed_utc': applied[-1]['at_utc'],
                  'header_events_sha256': sha(RAW / 'state-machine/home-header-events.jsonl')},
              'recovery_precheck_passed': fault['stages']['service_recovered'],
              'public_created_separately': True,
              'anomaly_gt_status': 'DRAFT_PENDING_HUMAN_REVIEW'}
    with (PRIVATE / 'injection-record.json').open('x') as stream:
        json.dump(record, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': record['status'],
                      'service_seconds': service['duration_seconds'],
                      'http_delayed_requests': len(applied),
                      'injection_plan_sha256': sha(PRIVATE / 'injection-plan.json'),
                      'injection_record_sha256': sha(PRIVATE / 'injection-record.json')}))


if __name__ == '__main__':
    main()
