#!/usr/bin/env python3
"""Capture one fresh, GT-free Stage4F observation window on the Master."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.error
import urllib.request

sys.path.insert(0,'/root/ops-competition-2026/sf002-production-20261014/scripts')
from trace_storage_reader import TRACE_SOURCE, read_window as read_trace_storage_window


PROMETHEUS = 'http://10.97.14.138:9090'
JAEGER = 'http://10.107.96.1:16686/jaeger/ui/api'
OPENSEARCH = 'http://10.111.114.255:9200'
PROM_QUERIES = {
    'cpu': 'container_cpu_usage_seconds_total{namespace="otel-demo",container!="",container!="POD"}',
    'memory': 'container_memory_working_set_bytes{namespace="otel-demo",container!="",container!="POD"}',
    'cfs_periods': 'container_cpu_cfs_periods_total{namespace="otel-demo",container!="",container!="POD"}',
    'cfs_throttled_periods': 'container_cpu_cfs_throttled_periods_total{namespace="otel-demo",container!="",container!="POD"}',
    'cfs_throttled_seconds': 'container_cpu_cfs_throttled_seconds_total{namespace="otel-demo",container!="",container!="POD"}',
    'node_cpu': 'k8s_node_cpu_usage',
    'node_cpu_time': 'k8s_node_cpu_time_seconds_total',
    'node_ready': 'k8s_node_condition_ready',
    'node_memory': 'k8s_node_memory_available_bytes',
    'span_calls': 'traces_span_metrics_calls_total',
    'http_failures': 'k6_http_req_failed_total{service_name="load-generator",condition="nonzero"}',
}
K8S_RESOURCES = ('pods', 'services', 'endpointslices', 'deployments')


def request_json(url, body=None, method=None):
    headers = {'Content-Type': 'application/json'} if body is not None else {}
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    is_trace_search = url.startswith(OPENSEARCH + '/') and '/_search' in url
    attempts = 6 if is_trace_search else 4
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read(4096).decode('utf-8', errors='replace')
            print(json.dumps({'source': 'opensearch_trace_reader' if is_trace_search else 'http_reader',
                              'http_status': exc.code, 'attempt': attempt + 1,
                              'response_excerpt': detail}, ensure_ascii=True), file=sys.stderr, flush=True)
            retryable = is_trace_search and exc.code in (429, 502, 503, 504)
            retryable |= body is None and exc.code in (429, 500, 502, 503, 504)
            if not retryable or attempt == attempts - 1:
                raise
            time.sleep(min(2 ** attempt, 16))


def kubectl_json(*args):
    result = subprocess.run(['kubectl', *args, '-o', 'json'], check=True,
                            capture_output=True, text=True, timeout=45)
    return json.loads(result.stdout)


def write_json(path,value):
    from bounded_storage import write_json as bounded_write
    return bounded_write(path,value)


def iso_utc(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Window timestamp must include a timezone')
    return parsed.astimezone(timezone.utc)


def jaeger_segments(service, start_us, end_us, depth=0):
    query = urllib.parse.urlencode({'service': service, 'limit': 1000,
                                    'start': start_us, 'end': end_us})
    try:
        data = request_json(JAEGER + '/traces?' + query)
    except urllib.error.HTTPError as exc:
        if exc.code in (500, 502, 503, 504) and depth < 6 and end_us - start_us > 5_000_000:
            middle = (start_us + end_us) // 2
            return (jaeger_segments(service, start_us, middle, depth + 1) +
                    jaeger_segments(service, middle, end_us, depth + 1))
        raise ValueError('Jaeger trace segment unreadable: service=' + service +
                         ' depth=' + str(depth) + ' HTTP=' + str(exc.code)) from exc
    traces = data.get('data')
    if not isinstance(traces, list):
        raise ValueError('Jaeger trace query failed: ' + service)
    if len(traces) < 1000:
        return [data]
    if end_us - start_us <= 1_000_000 or depth >= 8:
        raise ValueError('Jaeger trace cap reached in a one-second window: ' + service)
    middle = (start_us + end_us) // 2
    return (jaeger_segments(service, start_us, middle, depth + 1) +
            jaeger_segments(service, middle, end_us, depth + 1))


def capture(phase, start, end, output, trace_source, reuse_kubernetes_from=None):
    if phase not in ('before', 'during', 'after') or output.exists():
        raise ValueError('Unknown phase or existing capture directory')
    if trace_source != TRACE_SOURCE:
        raise ValueError('Stage4F trace source must be explicitly fixed to ' + TRACE_SOURCE)
    start_time, end_time = iso_utc(start), iso_utc(end)
    now = datetime.now(timezone.utc)
    capture_started = now
    if (start_time >= end_time or end_time > now or
            (not reuse_kubernetes_from and (now - end_time).total_seconds() > 120)):
        raise ValueError('Capture must finish within 120 seconds of the live window end')
    output.mkdir(parents=True)
    start_sec, end_sec = start_time.timestamp(), end_time.timestamp()
    snapshot_hashes = {}
    if reuse_kubernetes_from:
        if reuse_kubernetes_from.name != phase:
            raise ValueError('Recovery snapshot phase differs from the capture phase')
        for name in K8S_RESOURCES + ('nodes',):
            source = reuse_kubernetes_from / 'kubernetes' / (name + '.json')
            observed = datetime.fromtimestamp(source.stat().st_mtime, timezone.utc)
            if not 0 <= (observed - end_time).total_seconds() <= 120:
                raise ValueError('Recovered Kubernetes snapshot is outside the original window')
            data = json.loads(source.read_text())
            if not isinstance(data.get('items'), list):
                raise ValueError('Recovered Kubernetes snapshot is not a resource list')
            target = output / 'kubernetes' / (name + '.json')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            snapshot_hashes[str(source)] = hashlib.sha256(source.read_bytes()).hexdigest()
        metadata_observed = max(datetime.fromtimestamp(
            (reuse_kubernetes_from / 'kubernetes' / (name + '.json')).stat().st_mtime,
            timezone.utc) for name in K8S_RESOURCES + ('nodes',))
    else:
        for name in K8S_RESOURCES:
            write_json(output / 'kubernetes' / (name + '.json'),
                       kubectl_json('-n', 'otel-demo', 'get', name))
        write_json(output / 'kubernetes/nodes.json', kubectl_json('get', 'nodes'))
        metadata_observed = datetime.now(timezone.utc)
    if (metadata_observed - end_time).total_seconds() > 120:
        raise ValueError('Kubernetes snapshot is too late for this window')
    for name, expression in PROM_QUERIES.items():
        query = urllib.parse.urlencode({'query': expression, 'start': start_sec,
                                        'end': end_sec, 'step': 15})
        data = request_json(PROMETHEUS + '/api/v1/query_range?' + query)
        if data.get('status') != 'success' or data.get('data', {}).get('resultType') != 'matrix':
            raise ValueError('Prometheus range query failed: ' + name)
        write_json(output / 'metrics' / (name + '.json'), data)
    trace_metadata = read_trace_storage_window(start_time, end_time, output,
                                               OPENSEARCH, request_json, write_json)
    body = json.dumps({'size': 1000, 'track_total_hits': True,
                       'sort': ['_doc'], 'query': {'range': {
                           '@timestamp': {'gte': start_time.isoformat(),
                                          'lt': end_time.isoformat()}}}}).encode()
    logs = request_json(OPENSEARCH + '/otel-logs-*/_search?scroll=2m', body)
    total = logs.get('hits', {}).get('total', {})
    if not isinstance(total, dict) or total.get('relation') != 'eq':
        raise ValueError('OpenSearch log total is not exact')
    log_count, page = 0, 0
    scroll_id = logs.get('_scroll_id')
    try:
        while True:
            rows = logs.get('hits', {}).get('hits', [])
            if not isinstance(rows, list) or (not rows and log_count < total['value']):
                raise ValueError('OpenSearch scroll ended before all logs were read')
            write_json(output / 'logs' / f'page-{page:04d}.json', logs)
            log_count += len(rows)
            if log_count == total['value']:
                break
            if log_count > total['value'] or page >= 499 or not scroll_id:
                raise ValueError('OpenSearch log scroll is incomplete')
            logs = request_json(OPENSEARCH + '/_search/scroll',
                                json.dumps({'scroll': '2m', 'scroll_id': scroll_id}).encode())
            scroll_id = logs.get('_scroll_id', scroll_id)
            page += 1
    finally:
        if scroll_id:
            request_json(OPENSEARCH + '/_search/scroll',
                         json.dumps({'scroll_id': [scroll_id]}).encode(), method='DELETE')
    files = sorted(path for path in output.rglob('*') if path.is_file())
    completed = datetime.now(timezone.utc)
    manifest = {'schema_version': 1, 'phase': phase,
                'window': {'start_utc': start_time.isoformat(), 'end_utc': end_time.isoformat(),
                           'start_unix_ns': int(start_sec * 1e9),
                           'end_unix_ns': int(end_sec * 1e9)},
                'metadata_observed_utc': metadata_observed.isoformat(),
                'capture_implementation': 'phase4_exact_microsecond_trace_filter_inherited_from_phase3',
                'capture_started_utc': capture_started.isoformat(),
                'capture_completed_utc': completed.isoformat(),
                'start_lag_seconds': (capture_started - end_time).total_seconds(),
                'completion_lag_seconds': (completed - end_time).total_seconds(),
                'recovered_kubernetes_source_sha256': snapshot_hashes,
                'source': 'live_prometheus_opensearch_jaeger_storage_kubernetes',
                'trace_source': TRACE_SOURCE,
                'trace_storage_indices': trace_metadata['indices'],
                'trace_query_sha256': trace_metadata['query_sha256'],
                'trace_records': trace_metadata['unique_traces'],
                'span_records': trace_metadata['unique_spans'],
                'raw_span_hits': trace_metadata['total_storage_hits'],
                'identical_duplicate_hits': trace_metadata['identical_duplicate_hits'],
                'log_records': log_count,
                'sha256': {str(path.relative_to(output)): hashlib.sha256(path.read_bytes()).hexdigest()
                           for path in files}}
    write_json(output / 'capture-manifest.json', manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', required=True, choices=('before', 'during', 'after'))
    parser.add_argument('--start-utc', required=True)
    parser.add_argument('--end-utc', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--trace-source', required=True, choices=(TRACE_SOURCE,))
    parser.add_argument('--wait-until-end', action='store_true')
    parser.add_argument('--reuse-kubernetes-from', type=Path)
    args = parser.parse_args()
    if args.wait_until_end:
        wait = iso_utc(args.end_utc).timestamp() + 5 - time.time()
        if wait > 1800:
            raise ValueError('Refusing to schedule a capture more than 30 minutes ahead')
        if wait > 0:
            time.sleep(wait)
    result = capture(args.phase, args.start_utc, args.end_utc, args.output,
                     args.trace_source, args.reuse_kubernetes_from)
    print(json.dumps({'phase': result['phase'], 'traces': result['trace_records'],
                      'logs': result['log_records'], 'files': len(result['sha256'])}))


if __name__ == '__main__':
    main()
