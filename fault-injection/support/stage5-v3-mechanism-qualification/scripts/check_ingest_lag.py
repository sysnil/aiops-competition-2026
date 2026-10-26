#!/usr/bin/env python3
"""Measure trace/log visibility after a window without rewriting first capture."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import urllib.request


BASE = 'http://10.111.114.255:9200'


def count(index, query):
    request = urllib.request.Request(BASE + '/' + index + '/_count',
        data=json.dumps({'query': query}).encode(),
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.load(response)
    if result.get('_shards', {}).get('failed'):
        raise ValueError('OpenSearch count has failed shards')
    return result['count']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refusing overwrite')
    start = datetime.fromisoformat(args.start.replace('Z', '+00:00'))
    end = datetime.fromisoformat(args.end.replace('Z', '+00:00'))
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise ValueError('Invalid window')
    start_us = int(start.timestamp() * 1_000_000)
    end_us = int(end.timestamp() * 1_000_000)
    trace_query = {'range': {'startTime': {'gte': start_us, 'lt': end_us}}}
    log_query = {'range': {'@timestamp': {'gte': start.astimezone(timezone.utc).isoformat(),
                                          'lt': end.astimezone(timezone.utc).isoformat()}}}
    rows = []
    for offset in (20, 40, 60, 90, 120):
        target = end.timestamp() + offset
        if target > time.time():
            time.sleep(target - time.time())
        observed = datetime.now(timezone.utc)
        rows.append({'planned_offset_seconds': offset,
                     'actual_offset_seconds': (observed - end).total_seconds(),
                     'observed_utc': observed.isoformat(),
                     'trace_span_count': count('jaeger-jaeger-span-*', trace_query),
                     'log_count': count('otel-logs-*', log_query)})
    result = {'window_start': start.isoformat(), 'window_end': end.isoformat(),
              'samples': rows, 'trace_count_changed': len({r['trace_span_count'] for r in rows}) > 1,
              'log_count_changed': len({r['log_count'] for r in rows}) > 1}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'trace_count_changed': result['trace_count_changed'],
                      'log_count_changed': result['log_count_changed'],
                      'samples': rows}))


if __name__ == '__main__':
    main()
