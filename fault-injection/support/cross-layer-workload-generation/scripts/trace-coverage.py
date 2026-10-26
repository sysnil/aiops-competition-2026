#!/usr/bin/env python3
"""Read-only Jaeger OpenSearch trace coverage with explicit completeness limits."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import subprocess
import time
import urllib.request


OS = 'http://10.111.114.255:9200'
INDEX = 'jaeger-jaeger-span-*'


def fetch_json(path, body=None):
    request = urllib.request.Request(OS + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type': 'application/json'} if body is not None else {})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def scan(query, max_spans=100000):
    body = {'size': 2000, 'sort': ['_doc'], '_source': [
        'traceID', 'spanID', 'references', 'startTimeMillis', 'duration',
        'operationName', 'process.serviceName', 'process.tags', 'tags', 'tag.span@kind'],
        'query': query}
    page = fetch_json(f'/{INDEX}/_search?scroll=1m', body)
    scroll_id = page.get('_scroll_id')
    sources = []
    total = page['hits']['total']['value']
    try:
        while True:
            if page.get('_shards', {}).get('failed'):
                raise RuntimeError('OpenSearch shard failure')
            hits = page['hits']['hits']
            if not hits:
                break
            sources.extend(hit['_source'] for hit in hits)
            if len(sources) > max_spans:
                raise RuntimeError('Trace cap reached; refusing incomplete coverage')
            page = fetch_json('/_search/scroll', {'scroll': '1m', 'scroll_id': scroll_id})
            scroll_id = page.get('_scroll_id', scroll_id)
    finally:
        if scroll_id:
            try:
                fetch_json('/_search/scroll', {'scroll_id': [scroll_id]})
            except Exception:
                pass
    return sources, total


def describe(spans):
    services, edges, operations, pods, trace_spans = Counter(), Counter(), Counter(), Counter(), defaultdict(dict)
    duplicate_ids = 0
    for span in spans:
        service = span.get('process', {}).get('serviceName')
        trace_id, span_id = span.get('traceID'), span.get('spanID')
        if not service or not trace_id or not span_id:
            continue
        key = (trace_id, span_id)
        if span_id in trace_spans[trace_id]:
            duplicate_ids += 1
            continue
        trace_spans[trace_id][span_id] = span
        services[service] += 1
        operations[service + ':' + span.get('operationName', '?')] += 1
        tags = {tag.get('key'): tag.get('value') for tag in span.get('process', {}).get('tags', [])}
        pod = tags.get('k8s.pod.name') or tags.get('k8s.pod.uid')
        if pod:
            pods[f'{service}/{pod}'] += 1
    incomplete_parent = 0
    depths = []
    for trace_id, by_id in trace_spans.items():
        parents = {}
        for span_id, span in by_id.items():
            references = [ref for ref in span.get('references') or []
                          if ref.get('refType') == 'CHILD_OF' and ref.get('traceID') == trace_id]
            parent = references[0]['spanID'] if references else None
            parents[span_id] = parent
            if parent:
                ancestor = by_id.get(parent)
                if not ancestor:
                    incomplete_parent += 1
                else:
                    src = ancestor.get('process', {}).get('serviceName')
                    dst = span.get('process', {}).get('serviceName')
                    if src and dst and src != dst:
                        edges[f'{src}->{dst}'] += 1
        memo = {}
        def depth(span_id, stack):
            if span_id in memo:
                return memo[span_id]
            if span_id in stack:
                return 0
            parent = parents.get(span_id)
            memo[span_id] = 1 + (depth(parent, stack | {span_id}) if parent in parents else 0)
            return memo[span_id]
        depths.append(max((depth(span_id, set()) for span_id in by_id), default=0))
    return {'span_count': len(spans), 'trace_count': len(trace_spans),
            'services': dict(services.most_common()),
            'edges': dict(edges.most_common()),
            'operations': dict(operations.most_common()),
            'pod_identified_span_counts': dict(pods.most_common()),
            'pod_identified_span_fraction': sum(pods.values()) / len(spans) if spans else 0,
            'trace_depth_distribution': dict(Counter(depths)),
            'duplicate_trace_span_ids': duplicate_ids,
            'missing_parent_references': incomplete_parent}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--minutes', type=float, default=5)
    parser.add_argument('--run', type=Path)
    parser.add_argument('--window-start')
    parser.add_argument('--window-end')
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refusing to overwrite coverage output')
    now = datetime.now(timezone.utc)
    start = datetime.fromisoformat(args.window_start) if args.window_start else now-timedelta(minutes=args.minutes)
    end = datetime.fromisoformat(args.window_end) if args.window_end else now
    if start >= end:
        raise ValueError('Invalid trace window')
    filters = [{'range': {'startTimeMillis': {'gte': int(start.timestamp()*1000),
                                             'lt': int(end.timestamp()*1000)}}}]
    run_ids = []
    if args.run:
        if not args.window_start and not args.window_end:
            manifest = json.loads((args.run/'result-manifest.json').read_text())
            start = datetime.fromisoformat(manifest['started_utc']) - timedelta(seconds=30)
            end = datetime.fromisoformat(manifest['ended_utc']) + timedelta(seconds=90)
        rows = [json.loads(line) for line in (args.run/'sessions.jsonl').read_text().splitlines()]
        run_ids = sorted({step['trace_id'] for row in rows for step in row['steps']})
        if not run_ids:
            raise ValueError('No generated trace IDs')
        filters.append({'terms': {'traceID': run_ids}})
        filters[0] = {'range': {'startTimeMillis': {'gte': int(start.timestamp()*1000),
                                                   'lt': int(end.timestamp()*1000)}}}
    spans, total = scan({'bool': {'filter': filters}})
    result = {'source': 'JAEGER_OPENSEARCH_STORAGE', 'index': INDEX,
              'window_start': start.isoformat(), 'window_end': end.isoformat(),
              'query_trace_ids': len(run_ids), 'matched_trace_ids': len({span.get('traceID') for span in spans}),
              'opensearch_total': total, 'query_sha256': hashlib.sha256(
                json.dumps(filters, sort_keys=True).encode()).hexdigest(),
              **describe(spans)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({key: result[key] for key in ('span_count','trace_count','matched_trace_ids','missing_parent_references')}))


if __name__ == '__main__':
    main()
