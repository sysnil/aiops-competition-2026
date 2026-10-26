#!/usr/bin/env python3
"""Derive a public export window from the completed, frozen capture."""

import hashlib
import json
from pathlib import Path


RUN_ID = 'SF-011-ATTEMPT-02'
RUN = Path('/data/stage5-v3/ops-competition-2026/runs') / RUN_ID
PLAN = Path('/root/ops-competition-2026/sf011-real-adapter-v2-20261018/plans') / RUN_ID


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == '__main__':
    schedule = json.loads((RUN / 'schedule.json').read_text())
    terminal = json.loads((RUN / 'terminal.json').read_text())
    fault = json.loads((RUN / 'control/fault-controller-terminal.json').read_text())
    plan = json.loads((PLAN / 'run-plan.json').read_text())
    if (schedule['id'] != RUN_ID or plan['id'] != RUN_ID or
            schedule['duration_seconds'] != 1800 or
            terminal['status'] != 'SF011_FORMAL_CAPTURE_PASS_PENDING_EXPORT' or
            terminal.get('all_chunks_pass') is not True or
            fault['status'] != 'SF011_FAULT_SEQUENCE_COMPLETE_PENDING_CAPTURE' or
            fault['stages'].get('service_recovered') is not True or
            fault['stages'].get('http_delay', {}).get('delayed_home_requests', 0) < 60 or
            plan['capture_schedule_sha256'] != sha(RUN / 'schedule.json')):
        raise RuntimeError('Completed, hash-bound 30-minute capture required')
    window = {'case_id': 'SF-011', 'run_id': RUN_ID,
              'start_utc': schedule['start_utc'],
              'end_utc': schedule['end_utc'],
              'schedule_sha256': sha(RUN / 'schedule.json'),
              'capture_terminal_sha256': sha(RUN / 'terminal.json')}
    target = PLAN / 'public-window.json'
    with target.open('x') as stream:
        json.dump(window, stream, indent=2)
        stream.write('\n')
    target.chmod(0o600)
    print(json.dumps({'status': 'PUBLIC_WINDOW_DERIVED_FROM_COMPLETED_CAPTURE',
                      'path': str(target), 'sha256': sha(target)}))
