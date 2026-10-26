#!/usr/bin/env python3
"""Preserve the registered plan and actual fault evidence in Private only."""

import hashlib
import json
import shutil
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
RUN_ID = 'SF-007-ATTEMPT-04'
RAW = Path('/data/stage5-v3/ops-competition-2026/runs') / RUN_ID
PRIVATE = Path('/data/stage5-v3/ops-competition-2026/packages') / RUN_ID / 'private'
PLAN = OPS / 'sf007-real-adapter-v4-20261017/plans' / RUN_ID / 'run-plan.json'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def main():
    plan = read(PLAN)
    capture_path = RAW / 'terminal.json'
    fault_path = RAW / 'control/fault-controller-terminal.json'
    worker_path = RAW / 'control/service-worker-runtime/terminal.json'
    capture, fault, worker = map(read, (capture_path, fault_path, worker_path))
    if (plan['id'] != RUN_ID or
            capture['status'] != 'SF007_FORMAL_CAPTURE_PASS_PENDING_EXPORT' or
            fault['status'] != 'SF007_FAULT_SEQUENCE_COMPLETE_PENDING_CAPTURE' or
            worker['status'] != 'FORMAL_FAULT_COMPLETE'):
        raise RuntimeError('Formal capture or service fault evidence incomplete')
    service = fault['faults'][RUN_ID + '-SERVICE']
    if (service['cleanup']['status'] != 'COMPETITION_CLEANUP_ACTIONS_VERIFIED' or
            abs(service['duration_seconds'] - 30) > 2 or
            not fault['actual_onsets'][RUN_ID + '-SERVICE']['pass'] or
            fault['stages'].get('service_recovered') is not True):
        raise RuntimeError('Service injection or cleanup did not satisfy contract')
    if ((PRIVATE / 'injection-plan.json').exists() or
            (PRIVATE / 'injection-record.json').exists()):
        raise RuntimeError('Private injection evidence already finalized')
    shutil.copy2(PLAN, PRIVATE / 'injection-plan.json')
    record = {'run_id': RUN_ID, 'case_id': 'SF-007',
              'status': 'ACTUAL_FAULT_EVIDENCE_PRESERVED',
              'plan_sha256': sha(PLAN),
              'capture_terminal_sha256': sha(capture_path),
              'fault_terminal_sha256': sha(fault_path),
              'worker_terminal_sha256': sha(worker_path),
              'service': service,
              'service_onset': fault['actual_onsets'][RUN_ID + '-SERVICE'],
              'recovery_precheck_passed': fault['stages']['service_recovered'],
              'public_created_separately': True,
              'anomaly_gt_status': 'DRAFT_PENDING_HUMAN_REVIEW'}
    with (PRIVATE / 'injection-record.json').open('x') as stream:
        json.dump(record, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': record['status'],
                      'service_seconds': service['duration_seconds'],
                      'injection_plan_sha256': sha(PRIVATE / 'injection-plan.json'),
                      'injection_record_sha256': sha(PRIVATE / 'injection-record.json')}))


if __name__ == '__main__':
    main()
