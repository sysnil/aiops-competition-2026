#!/usr/bin/env python3
"""Summarize observed business responses around the two SF-010 events."""

import json
import math
import statistics
from datetime import datetime
from pathlib import Path


RUN = Path('/data/stage5-v3/ops-competition-2026/runs/SF-010-ATTEMPT-07')
OUTPUT = Path('/root/ops-competition-2026/sf010-finalization-20261016/attempt07-business-effects.json')


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def summarize(rows):
    latencies = sorted(row['latency_ms'] for row in rows if row.get('latency_ms') is not None)
    return {'requests': len(rows), 'successes': sum(row['success'] for row in rows),
            'errors': sum(not row['success'] for row in rows),
            'p50_latency_ms': round(statistics.median(latencies), 3) if latencies else None,
            'p95_latency_ms': round(latencies[math.ceil(.95 * len(latencies)) - 1], 3)
            if latencies else None}


def main():
    if OUTPUT.exists():
        raise RuntimeError('Business summary already exists')
    schedule = json.loads((RUN / 'schedule.json').read_text())
    start = stamp(schedule['start_utc'])
    rows = [json.loads(line) for line in
            (RUN / 'state-machine/request-events-live.jsonl').read_text().splitlines()]
    if not rows or any('request_started_utc' not in row for row in rows):
        raise RuntimeError('Actual business dispatch timestamps missing')
    windows = {
        'recommendations': {'before': (start + 480, start + 600),
                            'during': (start + 600, start + 630),
                            'after': (start + 630, start + 750)},
        'home': {'before': (start + 840, start + 960),
                 'during': (start + 960, start + 1020),
                 'after': (start + 1020, start + 1140)},
    }
    result = {'case_id': 'SF-010', 'run_id': 'SF-010-ATTEMPT-07',
              'source': 'state-machine/request-events-live.jsonl',
              'time_basis': 'ordinary business request_started_utc',
              'windows': {}}
    for operation, sections in windows.items():
        result['windows'][operation] = {}
        for label, (left, right) in sections.items():
            selection = [row for row in rows if row['operation'] == operation and
                         left <= stamp(row['request_started_utc']) < right]
            result['windows'][operation][label] = summarize(selection)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps(result['windows']))


if __name__ == '__main__':
    main()
