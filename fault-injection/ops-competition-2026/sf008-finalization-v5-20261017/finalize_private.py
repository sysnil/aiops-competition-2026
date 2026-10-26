"""Preserve actual Pod and HTTP execution evidence only in the Private package."""

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
RUN_ID = 'SF-008-ATTEMPT-05'
RAW = Path('/data/stage5-v3/ops-competition-2026/runs') / RUN_ID
PRIVATE = Path('/data/stage5-v3/ops-competition-2026/packages') / RUN_ID / 'private'
PLAN = OPS / 'sf008-real-adapter-v5-20261017/plans' / RUN_ID / 'run-plan.json'
ADMISSION = OPS / 'sf008-finalization-v5-20261017/pod-http-admission.json'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def main():
    plan = read(PLAN)
    capture_path = RAW / 'terminal.json'
    fault_path = RAW / 'control/fault-controller-terminal.json'
    worker_path = RAW / 'control/pod-worker-runtime/terminal.json'
    capture, fault, worker, admission = map(read,
        (capture_path, fault_path, worker_path, ADMISSION))
    if (plan['id'] != RUN_ID or
            capture['status'] != 'SF008_FORMAL_CAPTURE_PASS_PENDING_EXPORT' or
            fault['status'] != 'SF008_FAULT_SEQUENCE_COMPLETE_PENDING_CAPTURE' or
            worker['status'] != 'FORMAL_FAULT_COMPLETE' or
            admission['status'] != 'SF008_POD_HTTP_ADMISSION_PASS' or
            not all(admission['checks'].values())):
        raise RuntimeError('Formal two-stage capture or admission incomplete')
    pod = fault['faults'][RUN_ID + '-POD']
    start = stamp(plan['start_utc']) + plan['request_delay']['offset_seconds']
    end = start + plan['request_delay']['duration_seconds']
    headers = (RAW / 'state-machine/home-header-events.jsonl',
               RAW / 'controls/home-header-events.jsonl')
    marked = [row for path in headers for line in path.read_text().splitlines()
              if (row := json.loads(line))['delay_header_applied']]
    if (pod['cleanup']['status'] != 'COMPETITION_CLEANUP_ACTIONS_VERIFIED' or
            abs(pod['duration_seconds'] - 60) > 2 or
            not fault['actual_onsets'][RUN_ID + '-POD']['pass'] or
            fault['stages'].get('pod_recovered') is not True or
            len(marked) != fault['stages']['http_delay']['delayed_home_requests'] or
            len(marked) < 60 or
            any(not start <= stamp(row['at_utc']) < end or
                row['requested_delay_ms'] != 250 for row in marked)):
        raise RuntimeError('Bounded Pod/HTTP fault evidence changed')
    private_samples = PRIVATE / 'private-samples.jsonl'
    injection_result = PRIVATE / 'injection-result.json'
    if (not private_samples.is_file() or not injection_result.is_file() or
            (PRIVATE / 'injection-plan.json').exists() or
            (PRIVATE / 'injection-record.json').exists()):
        raise RuntimeError('Original private Pod evidence missing or already finalized')
    shutil.copy2(PLAN, PRIVATE / 'injection-plan.json')
    record = {
        'run_id': RUN_ID, 'case_id': 'SF-008',
        'status': 'ACTUAL_TWO_STAGE_FAULT_EVIDENCE_PRESERVED',
        'plan_sha256': sha(PLAN),
        'capture_terminal_sha256': sha(capture_path),
        'fault_terminal_sha256': sha(fault_path),
        'worker_terminal_sha256': sha(worker_path),
        'admission_sha256': sha(ADMISSION),
        'pod_fault': pod,
        'pod_onset': fault['actual_onsets'][RUN_ID + '-POD'],
        'http_delay': {'delayed_home_requests': len(marked),
                       'first_observed_utc': marked[0]['at_utc'],
                       'last_observed_utc': marked[-1]['at_utc'],
                       'header_events_sha256': {str(path): sha(path) for path in headers}},
        'private_samples_sha256': sha(private_samples),
        'injection_result_sha256': sha(injection_result),
        'anomaly_gt_status': 'DRAFT_PENDING_HUMAN_REVIEW',
        'public_created_separately': True,
    }
    with (PRIVATE / 'injection-record.json').open('x') as stream:
        json.dump(record, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': record['status'],
                      'pod_seconds': pod['duration_seconds'],
                      'http_delayed_requests': len(marked),
                      'injection_record_sha256': sha(PRIVATE / 'injection-record.json')}))


if __name__ == '__main__':
    main()
