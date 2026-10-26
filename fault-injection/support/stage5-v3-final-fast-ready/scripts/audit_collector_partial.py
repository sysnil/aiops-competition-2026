#!/usr/bin/env python3
"""Read only failed first-stage raw pages; never upgrade timing to PASS."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path

from clean_protocol import save_new, sha
from timed_collector_traces import issued


TARGETS = ('cart', 'checkout', 'shipping', 'quote')


def audit(run):
    trace = json.loads((run / 'timed-traces/trace-schedule.json').read_text())
    if trace['status'] != 'TIMED_CAPTURE_FAIL' or not trace['error'].startswith(
            'first_plus5 missed completion'):
        raise RuntimeError('This audit only applies to the preserved failed first read')
    ids = set(issued(run))
    pages = sorted((run / 'timed-traces/first_plus5').glob('page-*.json.gz'))
    if not pages:
        raise RuntimeError('First raw OpenSearch read absent')
    expected = None
    actual = 0
    identities = set()
    proxy = set()
    backend = {name: Counter() for name in TARGETS}
    hashes = {}
    for path in pages:
        with gzip.open(path, 'rt', encoding='utf-8') as source:
            page = json.load(source)
        if page['_shards'].get('failed'):
            raise RuntimeError('OpenSearch shard failed in raw page')
        total = page['hits']['total']
        if total['relation'] != 'eq' or (expected is not None and
                                        expected != total['value']):
            raise RuntimeError('OpenSearch page total drift')
        expected = total['value']
        hits = page['hits']['hits']
        actual += len(hits)
        hashes[path.name] = sha(path)
        for hit in hits:
            span = hit['_source']
            identity = (span['traceID'], span['spanID'])
            if identity in identities:
                raise RuntimeError('Duplicate TraceID/SpanID in raw page set')
            identities.add(identity)
            if (span.get('tag') or {}).get('span@kind') != 'server':
                continue
            service = span['process']['serviceName']
            if service == 'frontend-proxy': proxy.add(span['traceID'])
            if service in backend:
                uid = {row.get('key'): row.get('value') for row in
                    span['process'].get('tags') or []}.get('k8s.pod.uid')
                backend[service][uid or 'UNATTRIBUTED'] += 1
    if expected != actual or actual != len(identities):
        raise RuntimeError('Raw OpenSearch scroll incomplete')
    return {'status': 'PRESERVED_LATE_FIRST_READ_AUDITED_NOT_TIMELY',
        'at_utc': datetime.now(timezone.utc).isoformat(),
        'run_id': run.name, 'timed_capture_status': trace['status'],
        'first_read_completed_utc': trace['completed_utc'],
        'issued_trace_ids': len(ids),
        'proxy_server_matched_issued_ids': len(proxy & ids),
        'missing_proxy_issued_ids': sorted(ids - proxy),
        'raw_span_hits': actual, 'unique_trace_span_ids': len(identities),
        'raw_page_count': len(pages), 'raw_page_sha256': hashes,
        'target_server_uid_counts': {name: dict(counts)
                                     for name, counts in backend.items()},
        'source_is_late_relative_to_first_deadline': True,
        'claim_about_plus120_or_plus240': 'NOT_AVAILABLE',
        'claim_about_permanent_absence': 'ONLY_ABSENCE_AT_THIS_READ_TIME',
        'fault_injected': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.run)
    save_new(args.output, result)
    print(json.dumps({'status': result['status'], 'issued': result['issued_trace_ids'],
        'proxy_matched': result['proxy_server_matched_issued_ids'],
        'raw_spans': result['raw_span_hits']}))


if __name__ == '__main__': main()
