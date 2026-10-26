"""Audit a healthy Jaeger Query slice against its underlying span documents."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import urllib.parse
import urllib.error

from capture_live import JAEGER, OPENSEARCH, request_json, write_json
from trace_storage_reader import INDEX_PREFIX, index_names


def span_view(span, process):
    references = span.get('references') or []
    parents = sorted((row['refType'], row['traceID'], row['spanID'])
                     for row in references)
    return {'service': process['serviceName'], 'operation': span['operationName'],
            'start_us': span['startTime'], 'duration_us': span['duration'],
            'parents': parents}


def audit(start, end, service, limit=20):
    if start.tzinfo is None or end.tzinfo is None or start >= end or limit < 1:
        raise ValueError('Invalid equivalence audit window')
    start_us, end_us = int(start.timestamp() * 1e6), int(end.timestamp() * 1e6)
    query = urllib.parse.urlencode({'service': service, 'limit': limit,
                                    'start': start_us, 'end': end_us})
    try:
        jaeger = request_json(JAEGER + '/traces?' + query)
    except urllib.error.HTTPError as exc:
        return {'status': 'JAEGER_API_EQUIVALENCE_PARTIAL',
                'reason': 'Jaeger Query HTTP ' + str(exc.code),
                'window_start_utc': start.isoformat(), 'window_end_utc': end.isoformat(),
                'service': service, 'traces_checked': 0}
    traces = jaeger.get('data')
    if not isinstance(traces, list) or not traces:
        raise ValueError('Jaeger returned no traces for equivalence audit')
    indices = index_names(start - (end - start), end + (end - start))
    records = []
    for trace in traces:
        trace_id = trace['traceID']
        body = {'size': 1000, 'track_total_hits': True,
                'query': {'term': {'traceID': trace_id}}}
        stored = request_json(OPENSEARCH + '/' + ','.join(indices) + '/_search',
                              json.dumps(body).encode())['hits']
        if stored['total']['relation'] != 'eq' or stored['total']['value'] >= 1000:
            raise ValueError('Storage trace retrieval was truncated')
        jaeger_spans = {row['spanID']: span_view(row, trace['processes'][row['processID']])
                        for row in trace['spans']}
        storage_spans = {hit['_source']['spanID']:
                         span_view(hit['_source'], hit['_source']['process'])
                         for hit in stored['hits']}
        common = set(jaeger_spans) & set(storage_spans)
        differences = {field: sum(jaeger_spans[span_id][field] !=
                                  storage_spans[span_id][field] for span_id in common)
                       for field in ('service', 'operation', 'start_us',
                                     'duration_us', 'parents')}
        records.append({'trace_id': trace_id,
                        'jaeger_spans': len(jaeger_spans),
                        'storage_spans': len(storage_spans),
                        'common_span_ids': len(common),
                        'jaeger_only_span_ids': len(set(jaeger_spans) - common),
                        'storage_only_span_ids': len(set(storage_spans) - common),
                        'field_mismatches': differences})
    exact = all(row['jaeger_spans'] == row['storage_spans'] == row['common_span_ids']
                and not any(row['field_mismatches'].values()) for row in records)
    return {'status': ('SAMPLED_CORE_EQUIVALENCE_PASS' if exact else
                       'JAEGER_API_EQUIVALENCE_PARTIAL'),
            'scope': 'One healthy Jaeger Query slice; API failures in other windows remain documented',
            'window_start_utc': start.isoformat(), 'window_end_utc': end.isoformat(),
            'service': service, 'traces_checked': len(records),
            'spans_compared': sum(row['common_span_ids'] for row in records),
            'trace_comparisons': records}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start-utc', required=True)
    parser.add_argument('--end-utc', required=True)
    parser.add_argument('--service', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refusing to overwrite equivalence audit')
    start = datetime.fromisoformat(args.start_utc.replace('Z', '+00:00'))
    end = datetime.fromisoformat(args.end_utc.replace('Z', '+00:00'))
    result = audit(start, end, args.service)
    write_json(args.output, result)
    print(json.dumps({key: value for key, value in result.items()
                      if key != 'trace_comparisons'}))


if __name__ == '__main__':
    main()
