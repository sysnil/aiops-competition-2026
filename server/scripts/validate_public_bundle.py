#!/usr/bin/env python3
"""Read-only six-file validation before transferring a competition Public bundle."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re


FILES = {'metrics.parquet', 'logs.parquet', 'traces.parquet',
         'trace_index.parquet', 'topology.json', 'manifest.json'}
TIMESTAMP_COLUMNS = {'metrics.parquet': 'timestamp', 'logs.parquet': 'timestamp',
                     'traces.parquet': 'start_time'}
FORBIDDEN = ('ground_truth', 'private_gt', 'injection-plan', 'injection_record',
             'fault_target', 'fault_command', 'scoring_answer')


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def utc(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Timezone missing')
    return result.astimezone(timezone.utc)


def unix_ns(value):
    delta = value - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return ((delta.days * 86400 + delta.seconds) * 1_000_000_000 +
            delta.microseconds * 1000)


def main():
    import pyarrow as pa
    import pyarrow.parquet as pq

    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('--case-id', required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.source.is_symlink() or not args.source.is_dir():
        raise RuntimeError('Source must be an existing non-symlink directory; output must be new')
    listed = {path.name for path in args.source.iterdir()}
    if listed != FILES or any((args.source / name).is_symlink() for name in FILES):
        raise RuntimeError('Public six-file whitelist or symlink check failed')
    manifest_path = args.source / 'manifest.json'
    manifest_sha = sha(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    start, end = utc(manifest['window']['start_utc']), utc(manifest['window']['end_utc'])
    start_ns, end_ns = unix_ns(start), unix_ns(end)
    expected_seconds = 3600 if args.case_id in ('SF-001', 'SF-002') else 1800
    if (manifest_sha != args.manifest_sha256 or
            manifest.get('schema_version') != 'CONTEST_EXCHANGE_V2' or
            manifest.get('case_id') != args.case_id or
            manifest['window'].get('case_id') != args.case_id or
            (end - start).total_seconds() != expected_seconds):
        raise RuntimeError('Source manifest identity, hash or registered duration mismatch')
    public_json = json.dumps({'manifest': manifest,
                              'topology': json.loads((args.source / 'topology.json').read_text())},
                             ensure_ascii=False).lower()
    if any(marker in public_json for marker in FORBIDDEN):
        raise RuntimeError('Private control marker in Public JSON')

    file_checks = {}
    total_bytes = 0
    for name in sorted(FILES - {'manifest.json'}):
        path = args.source / name
        record = manifest['files'][name]
        digest, size = sha(path), path.stat().st_size
        if digest != record['sha256'] or size != record['bytes']:
            raise RuntimeError('Public file hash or size mismatch: ' + name)
        file_checks[name] = {'sha256': digest, 'bytes': size}
        total_bytes += size
    total_bytes += manifest_path.stat().st_size

    counts = {}
    minutes = {}
    for name, column in TIMESTAMP_COLUMNS.items():
        parquet = pq.ParquetFile(args.source / name)
        counts[name] = parquet.metadata.num_rows
        if str(parquet.schema_arrow.field(column).type) != 'timestamp[ns, tz=UTC]':
            raise RuntimeError('Wrong UTC nanosecond timestamp schema: ' + name)
        seen = Counter()
        for batch in parquet.iter_batches(batch_size=20000, columns=[column]):
            for timestamp in batch.column(0).cast(pa.int64()).to_pylist():
                if timestamp is None or not start_ns <= timestamp < end_ns:
                    raise RuntimeError('Parquet row outside half-open time window: ' + name)
                seen[(timestamp - start_ns) // (60 * 1_000_000_000)] += 1
        if len(seen) != expected_seconds // 60 or any(seen[i] == 0 for i in
                range(expected_seconds // 60)):
            raise RuntimeError('Modality minute coverage gap: ' + name)
        minutes[name] = len(seen)

    trace_counts = Counter()
    first_span = {}
    span_ids = set()
    traces_file = pq.ParquetFile(args.source / 'traces.parquet')
    for batch in traces_file.iter_batches(batch_size=20000,
            columns=['trace_id', 'span_id', 'start_time']):
        trace_ids = batch.column(0).to_pylist()
        span_values = batch.column(1).to_pylist()
        starts = batch.column(2).cast(pa.int64()).to_pylist()
        for trace_id, span_id, span_start in zip(trace_ids, span_values, starts):
            if (not isinstance(trace_id, str) or not isinstance(span_id, str) or
                    not re.fullmatch(r'[0-9a-fA-F]{16,64}', trace_id) or
                    not re.fullmatch(r'[0-9a-fA-F]{16,64}', span_id)):
                raise RuntimeError('Invalid Trace/Span ID')
            key = (trace_id.lower(), span_id.lower())
            if key in span_ids:
                raise RuntimeError('Duplicate Trace/Span identity')
            span_ids.add(key)
            trace_counts[trace_id.lower()] += 1
            current = first_span.get(trace_id.lower())
            if current is None or span_start < current:
                first_span[trace_id.lower()] = span_start
    index_file = pq.ParquetFile(args.source / 'trace_index.parquet')
    counts['trace_index.parquet'] = index_file.metadata.num_rows
    indexed = set()
    for batch in index_file.iter_batches(batch_size=20000,
            columns=['trace_id', 'start_time', 'span_count']):
        trace_ids = batch.column(0).to_pylist()
        starts = batch.column(1).cast(pa.int64()).to_pylist()
        spans = batch.column(2).to_pylist()
        for raw_id, span_start, span_count in zip(trace_ids, starts, spans):
            trace_id = raw_id.lower()
            if (trace_id in indexed or trace_counts.get(trace_id) != span_count or
                    first_span.get(trace_id) != span_start):
                raise RuntimeError('Trace Index differs from real Spans')
            indexed.add(trace_id)
    if indexed != set(trace_counts):
        raise RuntimeError('Trace Index misses a real Trace ID')
    expected_counts = {'metrics.parquet': manifest['row_counts']['metrics'],
        'logs.parquet': manifest['row_counts']['logs'],
        'traces.parquet': manifest['row_counts']['traces'],
        'trace_index.parquet': manifest['trace_index_rows']}
    if (counts != expected_counts or len(trace_counts) != manifest['unique_trace_count']):
        raise RuntimeError('Source Parquet counts differ from Public Manifest')
    result = {'status': 'PUBLIC_SOURCE_VALIDATED', 'case_id': args.case_id,
        'source': str(args.source), 'manifest_sha256': manifest_sha,
        'duration_seconds': expected_seconds, 'total_bytes': total_bytes,
        'files': file_checks, 'row_counts': counts,
        'unique_traces': len(trace_counts), 'nonempty_minutes': minutes,
        'private_marker_scan': 'PASS', 'parquet_readback': 'PASS',
        'trace_index_span_identity': 'PASS'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
