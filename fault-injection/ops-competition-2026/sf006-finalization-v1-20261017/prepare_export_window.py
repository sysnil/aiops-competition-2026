"""Bind SF-006 Public export to the immutable registered 1800-second schedule."""

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--schedule', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError('Public export window already frozen')
    schedule = json.loads(args.schedule.read_text())
    start = datetime.fromisoformat(schedule['start_utc'].replace('Z', '+00:00'))
    end = datetime.fromisoformat(schedule['end_utc'].replace('Z', '+00:00'))
    if (schedule['id'] != 'SF-006-ATTEMPT-01' or
            schedule['formal_case'] is not True or
            schedule['duration_seconds'] != 1800 or
            (end - start).total_seconds() != 1800):
        raise RuntimeError('Formal SF-006 schedule is not a complete half-hour')
    result = {'case_id': 'SF-006', 'run_id': schedule['id'],
              'formal_case': True, 'start_utc': schedule['start_utc'],
              'end_utc': schedule['end_utc'],
              'schedule_sha256': hashlib.sha256(args.schedule.read_bytes()).hexdigest()}
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
