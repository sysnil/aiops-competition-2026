#!/usr/bin/env python3
"""Capture an immutable bounded-delay trace supplement after live first read."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

from capture_live import OPENSEARCH, request_json, write_json
from trace_storage_reader import read_window


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', required=True)
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--first-capture', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refusing overwrite')
    start = datetime.fromisoformat(args.start.replace('Z', '+00:00'))
    end = datetime.fromisoformat(args.end.replace('Z', '+00:00'))
    wait = end.timestamp() + 60 - time.time()
    if wait > 0:
        time.sleep(wait)
    observed = datetime.now(timezone.utc)
    if not 0 <= (observed - end).total_seconds() <= 120:
        raise ValueError('Supplement is outside registered freshness bound')
    first_path = args.first_capture / 'capture-manifest.json'
    if not first_path.is_file():
        raise ValueError('First live capture did not complete')
    first = json.loads(first_path.read_text())
    if first['window']['start_utc'] != start.isoformat() or first['window']['end_utc'] != end.isoformat():
        raise ValueError('Supplement window differs from first capture')
    args.output.mkdir(parents=True)
    metadata = read_window(start, end, args.output, OPENSEARCH, request_json, write_json)
    if metadata['unique_spans'] < first['span_records']:
        raise ValueError('Delayed trace view lost spans from first capture')
    result = {'phase': args.phase, 'status': 'LATE_SPANS_ADDED' if
              metadata['unique_spans'] > first['span_records'] else 'NO_LATE_SPANS',
              'first_capture_sha256': hashlib.sha256(first_path.read_bytes()).hexdigest(),
              'first_span_count': first['span_records'],
              'supplement_span_count': metadata['unique_spans'],
              'late_span_count': metadata['unique_spans'] - first['span_records'],
              'observed_utc': observed.isoformat(),
              'start_lag_seconds': (observed - end).total_seconds(),
              'retrospective': False,
              'original_capture_overwritten': False}
    write_json(args.output / 'completion-manifest.json', result)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
