#!/usr/bin/env python3
"""Bind the SF-008 converter smoke to a completed no-fault rehearsal."""

import json
from pathlib import Path


RUN = Path('/data/stage5-v3/ops-competition-2026/rehearsals/SF008-POD-HTTP-NOFAULT-02')


if __name__ == '__main__':
    schedule = json.loads((RUN/'schedule.json').read_text())
    capture = json.loads((RUN/'terminal.json').read_text())
    markers = json.loads((RUN/'marker-control/terminal.json').read_text())
    if (schedule['id'] != RUN.name or schedule['duration_seconds'] != 1800 or
            capture['status'] != 'NOFAULT_30MIN_CAPTURE_PASS' or
            capture['all_chunks_pass'] is not True or
            markers['status'] != 'SF008_TWO_EVENT_MARKER_SCHEDULER_PASS' or
            markers['business_faults_injected'] != 0):
        raise RuntimeError('Completed 30-minute no-fault evidence required')
    target = RUN/'public-smoke-window.json'
    with target.open('x') as stream:
        json.dump({'case_id': 'SF-008', 'run_id': RUN.name,
                   'formal_case': False, 'faults_injected': 0,
                   'start_utc': schedule['start_utc'],
                   'end_utc': schedule['end_utc']}, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': 'NOFAULT_SMOKE_WINDOW_READY', 'path': str(target)}))
