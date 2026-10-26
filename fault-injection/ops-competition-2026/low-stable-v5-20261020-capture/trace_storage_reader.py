"""Read Jaeger's OpenSearch span documents without the Jaeger Query adjusters."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sys


TRACE_SOURCE = 'JAEGER_OPENSEARCH_STORAGE'
INDEX_PREFIX = 'jaeger-jaeger-span-'
TRACE_ID = re.compile(r'[0-9a-fA-F]{16,32}\Z')
SPAN_ID = re.compile(r'[0-9a-fA-F]{16}\Z')
PAGE_SIZE = 500
MAX_PAGES = 500
MAX_IDENTICAL_DUPLICATE_FRACTION = 0.005


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def index_names(start, end):
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise ValueError('Trace window must be ordered and timezone-aware')
    day = start.astimezone(timezone.utc).date()
    last = (end.astimezone(timezone.utc) - timedelta(microseconds=1)).date()
    names = []
    while day <= last:
        names.append(INDEX_PREFIX + day.isoformat())
        day += timedelta(days=1)
    return names


def validate_span(hit, indices, start_us, end_us):
    if hit.get('_index') not in indices or not isinstance(hit.get('_id'), str):
        raise ValueError('Trace hit came from an unexpected index or lacks a document ID')
    span = hit.get('_source')
    if not isinstance(span, dict) or not TRACE_ID.fullmatch(str(span.get('traceID', ''))) or (
            not SPAN_ID.fullmatch(str(span.get('spanID', '')))):
        raise ValueError('Malformed OpenSearch trace/span identity')
    stamp = span.get('startTime')
    if (not isinstance(stamp, int) or not start_us <= stamp < end_us or
            span.get('startTimeMillis') != stamp // 1000):
        raise ValueError('Trace timestamp is outside the exact acquisition window')
    if (not isinstance(span.get('duration'), int) or span['duration'] < 0 or
            not isinstance(span.get('operationName'), str) or
            span.get('tags') is not None and not isinstance(span.get('tags'), list) or
            span.get('references') is not None and not isinstance(span.get('references'), list) or
            not isinstance(span.get('process'), dict) or
            not isinstance(span['process'].get('serviceName'), str) or
            not span['process']['serviceName']):
        raise ValueError('Malformed OpenSearch span content')
    for reference in span.get('references') or []:
        if (reference.get('refType') not in ('CHILD_OF', 'FOLLOWS_FROM') or
                not TRACE_ID.fullmatch(str(reference.get('traceID', ''))) or
                not SPAN_ID.fullmatch(str(reference.get('spanID', '')))):
            raise ValueError('Malformed OpenSearch span reference')
    return span


def legacy_traces(hits):
    grouped = {}
    for hit in hits:
        span = hit['_source']
        trace = grouped.setdefault(span['traceID'], [])
        trace.append(span)
    output = []
    for trace_id, spans in sorted(grouped.items()):
        services = sorted({span['process']['serviceName'] for span in spans})
        process_ids = {service: 'p' + str(index) for index, service in enumerate(services)}
        processes = {process_ids[service]: {'serviceName': service}
                     for service in services}
        normalized = []
        for source in sorted(spans, key=lambda item: (item['startTime'], item['spanID'])):
            tags = list(source.get('tags') or [])
            kind = (source.get('tag') or {}).get('span@kind')
            if kind and not any(item.get('key') == 'span.kind' for item in tags):
                tags.append({'key': 'span.kind', 'type': 'string', 'value': kind})
            normalized.append({'traceID': trace_id, 'spanID': source['spanID'],
                               'operationName': source['operationName'],
                               'references': source.get('references') or [],
                               'startTime': source['startTime'],
                               'duration': source['duration'], 'tags': tags,
                               'processID': process_ids[source['process']['serviceName']]})
        output.append({'traceID': trace_id, 'spans': normalized, 'processes': processes})
    return output


def register_hit(hit, seen, duplicates):
    span = hit['_source']
    identity = (span['traceID'].lower(), span['spanID'].lower())
    earlier = seen.get(identity)
    if earlier is None:
        seen[identity] = hit
        return True
    if earlier['_source'] != span:
        raise ValueError('Conflicting duplicate trace/span content in storage window')
    duplicates.append({'trace_id': identity[0], 'span_id': identity[1],
                       'first_document_id': earlier['_id'],
                       'duplicate_document_id': hit['_id'],
                       'source_sha256': digest(span)})
    return False


def read_window(start, end, output, base_url, request_json, write_json):
    indices = index_names(start, end)
    index_path = ','.join(indices)
    settings = request_json(base_url + '/' + index_path +
                            '/_settings?filter_path=*.settings.index.uuid')
    mappings = request_json(base_url + '/' + index_path + '/_mapping')
    if set(settings) != set(indices) or set(mappings) != set(indices):
        raise ValueError('Missing or unexpected Jaeger OpenSearch span index')
    source_indices = {name: {'uuid': settings[name]['settings']['index']['uuid'],
                             'mapping_sha256': digest(mappings[name]['mappings'])}
                      for name in indices}
    start_us = int(start.timestamp() * 1_000_000)
    end_us = int(end.timestamp() * 1_000_000)
    query = {'size': PAGE_SIZE, 'track_total_hits': True, 'sort': ['_doc'],
             'query': {'bool': {'filter': [
                 {'range': {'startTimeMillis': {'gte': start_us // 1000,
                                                'lt': (end_us + 999) // 1000}}},
                 {'range': {'startTime': {'gte': start_us, 'lt': end_us}}}]}}}
    query_hash = digest(query)
    response = request_json(base_url + '/' + index_path + '/_search?scroll=2m',
                            json.dumps(query).encode())
    total = response.get('hits', {}).get('total', {})
    if not isinstance(total, dict) or total.get('relation') != 'eq':
        raise ValueError('OpenSearch trace total is not exact')
    expected = total['value']
    if not isinstance(expected, int) or expected < 1 or expected > PAGE_SIZE * MAX_PAGES:
        raise ValueError('OpenSearch trace window is empty or exceeds the bounded read budget')
    seen = {}
    duplicates = []
    hits = []
    raw_count = 0
    scroll_id = response.get('_scroll_id')
    page = 0
    try:
        while True:
            if response.get('_shards', {}).get('failed', 0) != 0:
                raise ValueError('OpenSearch trace scroll has failed shards')
            rows = response.get('hits', {}).get('hits', [])
            if not isinstance(rows, list) or not rows or page >= MAX_PAGES:
                raise ValueError('OpenSearch trace scroll ended early or exceeded page budget')
            write_json(output / 'trace-storage-pages' / f'page-{page:04d}.json', response)
            for hit in rows:
                validate_span(hit, set(indices), start_us, end_us)
                raw_count += 1
                if register_hit(hit, seen, duplicates):
                    hits.append(hit)
            if raw_count == expected:
                break
            if raw_count > expected or not scroll_id:
                raise ValueError('OpenSearch trace scroll count mismatch')
            response = request_json(base_url + '/_search/scroll',
                                    json.dumps({'scroll': '2m', 'scroll_id': scroll_id}).encode())
            scroll_id = response.get('_scroll_id', scroll_id)
            page += 1
    finally:
        if scroll_id:
            primary_error = sys.exc_info()[1]
            try:
                request_json(base_url + '/_search/scroll',
                             json.dumps({'scroll_id': [scroll_id]}).encode(), method='DELETE')
            except Exception:
                if primary_error is None:
                    raise
    final_settings = request_json(base_url + '/' + index_path +
                                  '/_settings?filter_path=*.settings.index.uuid')
    if any(final_settings.get(name, {}).get('settings', {}).get('index', {}).get('uuid') !=
           source_indices[name]['uuid'] for name in indices):
        raise ValueError('Jaeger span index UUID changed during acquisition')
    if len(duplicates) > expected * MAX_IDENTICAL_DUPLICATE_FRACTION:
        raise ValueError('Identical duplicate span fraction exceeds registered limit')
    duplicate_audit = {'policy': 'EXACT_SOURCE_ONLY_FIRST_DOCUMENT_RETAINED',
                       'maximum_fraction': MAX_IDENTICAL_DUPLICATE_FRACTION,
                       'raw_storage_hits': expected,
                       'unique_spans': len(seen),
                       'duplicate_documents': duplicates}
    write_json(output / 'trace-storage-duplicate-audit.json', duplicate_audit)
    traces = legacy_traces(hits)
    for offset in range(0, len(traces), PAGE_SIZE):
        write_json(output / 'traces' / f'storage-{offset // PAGE_SIZE:04d}.json',
                   {'data': traces[offset:offset + PAGE_SIZE]})
    metadata = {'trace_source': TRACE_SOURCE, 'indices': source_indices,
                'query_start_utc': start.isoformat(), 'query_end_utc': end.isoformat(),
                'query_sha256': query_hash, 'total_storage_hits': expected,
                'unique_spans': len(seen), 'unique_traces': len(traces),
                'identical_duplicate_hits': len(duplicates),
                'duplicate_audit': 'trace-storage-duplicate-audit.json',
                'raw_pages': page + 1}
    write_json(output / 'trace-storage-source.json', metadata)
    return metadata
