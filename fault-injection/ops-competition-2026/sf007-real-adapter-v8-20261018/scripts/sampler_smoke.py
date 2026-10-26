#!/usr/bin/env python3
"""Read-only sixty-second five-host capacity forecast qualification."""

import hashlib
import json
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import five_host_heartbeat as monitor


OUTPUT = Path(__file__).resolve().parents[1] / 'results/five-host-sampler-smoke.json'


def now():
    return datetime.now(timezone.utc).isoformat()


def main():
    if OUTPUT.exists():
        raise RuntimeError('No overwrite of sampler evidence')
    started = time.monotonic()
    history = deque(maxlen=12)
    rows = []
    for index in range(5):
        time.sleep(max(0, started + index * 15 - time.monotonic()))
        at, snapshot, forecast, decision = monitor.sample(history, started, 2500)
        history.append((at, snapshot))
        rows.append({'at_utc': now(), 'elapsed_seconds': at-started,
                     'snapshot': snapshot, 'forecast': forecast,
                     'status': decision['status']})
        if decision['status'] != 'DYNAMIC_STORAGE_PASS':
            break
    payload = {'status': 'FIVE_HOST_SAMPLER_SMOKE_PASS' if len(rows) == 5 and
               all(row['status'] == 'DYNAMIC_STORAGE_PASS' for row in rows)
               else 'FIVE_HOST_SAMPLER_SMOKE_BLOCKED',
               'started_utc': rows[0]['at_utc'], 'completed_utc': now(),
               'minimum_measurement_window_seconds': 60,
               'source_sha256': hashlib.sha256(Path(monitor.__file__).read_bytes()).hexdigest(),
               'faults_injected': 0, 'formal_case_created': False,
               'samples': rows}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open('x') as stream:
        json.dump(payload, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': payload['status'],
                      'samples': len(rows),
                      'elapsed_seconds': rows[-1]['elapsed_seconds']}))
    if payload['status'] != 'FIVE_HOST_SAMPLER_SMOKE_PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
