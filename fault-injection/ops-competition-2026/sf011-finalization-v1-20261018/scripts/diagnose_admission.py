"""Explain frozen SF-011 admission failures without rewriting its verdict."""

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def main():
    import pyarrow.parquet as pq

    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--public', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError('Admission diagnostic already exists')
    schedule = json.loads((args.run / 'schedule.json').read_text())
    start = timestamp(schedule['start_utc'])
    http_start, http_end = start + 960, start + 1020
    service_start, service_end = start + 600, start + 630
    state = [json.loads(line) for line in (args.run / 'state-machine/home-header-events.jsonl').read_text().splitlines()]
    fixed = [json.loads(line) for line in (args.run / 'controls/home-header-events.jsonl').read_text().splitlines()]
    rows = state + fixed
    marked = [row for row in rows if row['delay_header_applied']]
    in_window = [row for row in rows if http_start <= timestamp(row['at_utc']) < http_end]
    caller_codes = Counter()
    caller_samples = []
    for batch in pq.ParquetFile(args.public / 'traces.parquet').iter_batches(
            batch_size=5000, columns=['trace_id', 'service', 'start_time', 'attributes']):
        for row in batch.to_pylist():
            at = row['start_time'].timestamp()
            if not service_start <= at < service_end or row['service'] != 'frontend':
                continue
            attrs = json.loads(row['attributes'] or '{}')
            if (attrs.get('span.kind') != 'client' or
                    attrs.get('net.peer.name') != 'recommendation' or
                    attrs.get('rpc.method') != 'ListRecommendations'):
                continue
            code = (str(attrs.get('otel.status_code', 'MISSING')),
                    str(attrs.get('rpc.grpc.status_code', 'MISSING')))
            caller_codes[code] += 1
            if len(caller_samples) < 5:
                caller_samples.append({'trace_id': row['trace_id'],
                                       'status_code': code[0], 'grpc_code': code[1]})
    result = {
        'status': 'READ_ONLY_ADMISSION_FAILURE_DIAGNOSTIC',
        'case_id': 'SF-011', 'run_id': 'SF-011-ATTEMPT-01',
        'http': {
            'all_marked': len(marked),
            'fixed_marked': sum(row['delay_header_applied'] for row in fixed),
            'in_window': len(in_window),
            'unmarked_in_window': sum(not row['delay_header_applied'] for row in in_window),
            'marked_outside_window': sum(not http_start <= timestamp(row['at_utc']) < http_end
                                         for row in marked),
            'marked_wrong_requested_delay': sum(row.get('requested_delay_ms') != 250
                                                 for row in marked),
        },
        'service': {'caller_code_counts': [
            {'otel_status_code': key[0], 'grpc_status_code': key[1], 'count': value}
            for key, value in sorted(caller_codes.items())],
            'example_trace_ids': caller_samples},
        'admission_unchanged': True,
    }
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
