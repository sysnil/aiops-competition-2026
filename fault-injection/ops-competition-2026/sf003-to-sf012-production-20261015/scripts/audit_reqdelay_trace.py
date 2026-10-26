#!/usr/bin/env python3
"""Audit public OpenSearch spans for each request in the isolated qualification."""

import argparse
import hashlib
import json
import re
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


RUN = Path('/data/stage5-v3/ops-competition-2026/rehearsals/SF30-REQDELAY-QUAL-20261015')
OS = 'http://10.111.114.255:9200'
TRACE_ID = re.compile(r'[0-9a-f]{32}\Z')
SPAN_ID = re.compile(r'[0-9a-f]{16}\Z')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fetch(index, trace_ids):
    query = {'size': 5000, 'track_total_hits': True,
             '_source': ['traceID', 'spanID', 'startTime', 'operationName',
                         'process.serviceName', 'tag.span@kind', 'duration'],
             'query': {'terms': {'traceID': trace_ids}}}
    body = json.dumps(query).encode()
    request = urllib.request.Request(OS + '/' + index + '/_search', data=body,
                                     headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.load(response)
    hits = data['hits']['hits']
    total = data['hits']['total']
    if data['_shards']['failed'] or total['relation'] != 'eq' or total['value'] != len(hits):
        raise RuntimeError('Trace query partial or truncated')
    return hits


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--audit-offset-seconds', type=int, choices=(60, 120, 240), required=True)
    args = parser.parse_args()
    if not (RUN / 'result.json').exists():
        raise RuntimeError('Qualification not finished')
    rows = [json.loads(line) for line in (RUN / 'requests.jsonl').read_text().splitlines()]
    ids = [row['trace_id'] for row in rows]
    if len(ids) != len(set(ids)) or not all(TRACE_ID.fullmatch(t) for t in ids):
        raise RuntimeError('Invalid or duplicate request Trace ID')
    index = 'jaeger-jaeger-span-' + datetime.fromisoformat(rows[0]['at_utc']).date().isoformat()
    seen = set()
    proxy_server = Counter()
    service_spans = Counter()
    identities = set()
    for offset in range(0, len(ids), 50):
        group = ids[offset:offset + 50]
        for hit in fetch(index, group):
            span = hit['_source']
            trace_id, span_id = span['traceID'], span['spanID']
            if trace_id not in group or not TRACE_ID.fullmatch(trace_id) or not SPAN_ID.fullmatch(span_id):
                raise RuntimeError('Unexpected stored trace/span identity')
            identity = (trace_id, span_id)
            if identity in identities:
                raise RuntimeError('Duplicate stored span')
            identities.add(identity)
            seen.add(trace_id)
            service = span['process']['serviceName']
            service_spans[service] += 1
            if service == 'frontend-proxy' and span.get('tag', {}).get('span@kind') == 'server':
                proxy_server[trace_id] += 1
    by_phase = {}
    for phase in ('before', 'during', 'after'):
        phase_rows = [r for r in rows if r['phase'] == phase]
        by_phase[phase] = {
            'issued_requests': len(phase_rows),
            'trace_ids_in_storage': sum(r['trace_id'] in seen for r in phase_rows),
            'proxy_server_span_matches': sum(proxy_server[r['trace_id']] == 1 for r in phase_rows),
            'missing_proxy_server_trace_ids': [r['trace_id'] for r in phase_rows
                                               if proxy_server[r['trace_id']] != 1],
        }
    output = {
        'status': 'TRACE_MATCH_COMPLETE' if all(
            value['proxy_server_span_matches'] == value['issued_requests']
            for value in by_phase.values()) else 'TRACE_MATCH_INCOMPLETE',
        'audit_utc': datetime.now(timezone.utc).isoformat(),
        'audit_offset_seconds': args.audit_offset_seconds,
        'trace_source': 'JAEGER_OPENSEARCH_STORAGE',
        'index': index,
        'requests_sha256': sha(RUN / 'requests.jsonl'),
        'result_sha256': sha(RUN / 'result.json'),
        'unique_spans': len(identities), 'service_span_counts': dict(service_spans),
        'phases': by_phase,
        'private_injection_evidence_accessed': False,
    }
    path = RUN / f'trace-audit-plus-{args.audit_offset_seconds}.json'
    with path.open('x') as stream:
        json.dump(output, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': output['status'], 'phase_matches': {
        key: [v['proxy_server_span_matches'], v['issued_requests']]
        for key, v in by_phase.items()}, 'path': str(path)}))


if __name__ == '__main__':
    main()
