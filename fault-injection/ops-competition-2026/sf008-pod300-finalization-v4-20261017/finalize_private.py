#!/usr/bin/env python3
"""Preserve the registered plan and actual fault evidence in Private only."""

import hashlib
import json
import shutil
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
RUN_ID = 'SF-008-ATTEMPT-09'
RAW = Path('/data/stage5-v3/ops-competition-2026/runs') / RUN_ID
PRIVATE = Path('/data/stage5-v3/ops-competition-2026/packages') / RUN_ID / 'private'
PLAN = OPS / 'sf008-pod300-adapter-v4-20261017/plans' / RUN_ID / 'run-plan.json'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def main():
    plan = read(PLAN)
    capture_path = RAW / 'terminal.json'
    fault_path = RAW / 'control/fault-controller-terminal.json'
    worker_path = RAW / 'control/pod-worker-runtime/terminal.json'
    capture, fault, worker = map(read, (capture_path, fault_path, worker_path))
    if (plan['id'] != RUN_ID or
            capture['status'] != 'SF008_FORMAL_CAPTURE_PASS_PENDING_EXPORT' or
            fault['status'] != 'SF008_FAULT_SEQUENCE_COMPLETE_PENDING_CAPTURE' or
            worker['status'] != 'FORMAL_FAULT_COMPLETE'):
        raise RuntimeError('Formal Pod capture or fault evidence incomplete')
    pod = fault['faults'][RUN_ID + '-POD']
    if (pod['cleanup']['status'] != 'COMPETITION_CLEANUP_ACTIONS_VERIFIED' or
            abs(pod['duration_seconds'] - 300) > 2 or
            not fault['actual_onsets'][RUN_ID + '-POD']['pass'] or
            fault['stages']['pod_recovered'] is not True):
        raise RuntimeError('Pod injection or cleanup did not satisfy contract')
    private_samples = PRIVATE / 'private-samples.jsonl'
    adapter_result = PRIVATE / 'injection-result.json'
    if not private_samples.is_file() or not adapter_result.is_file():
        raise RuntimeError('Original Worker2 private fault samples missing')
    if ((PRIVATE / 'injection-plan.json').exists() or
            (PRIVATE / 'injection-record.json').exists()):
        raise RuntimeError('Private injection evidence already finalized')
    shutil.copy2(PLAN, PRIVATE / 'injection-plan.json')
    record = {'run_id': RUN_ID, 'case_id': 'SF-008',
              'status': 'ACTUAL_FAULT_EVIDENCE_PRESERVED',
              'plan_sha256': sha(PLAN),
              'capture_terminal_sha256': sha(capture_path),
              'fault_terminal_sha256': sha(fault_path),
              'worker_terminal_sha256': sha(worker_path),
              'pod': pod,
              'pod_onset': fault['actual_onsets'][RUN_ID + '-POD'],
              'private_samples_sha256': sha(private_samples),
              'adapter_injection_result_sha256': sha(adapter_result),
              'recovery_precheck_passed': fault['stages']['pod_recovered'],
              'public_created_separately': True,
              'anomaly_gt_status': 'DRAFT_PENDING_HUMAN_REVIEW'}
    with (PRIVATE / 'injection-record.json').open('x') as stream:
        json.dump(record, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': record['status'],
                      'pod_seconds': pod['duration_seconds'],
                      'injection_plan_sha256': sha(PRIVATE / 'injection-plan.json'),
                      'injection_record_sha256': sha(PRIVATE / 'injection-record.json')}))


if __name__ == '__main__':
    main()
