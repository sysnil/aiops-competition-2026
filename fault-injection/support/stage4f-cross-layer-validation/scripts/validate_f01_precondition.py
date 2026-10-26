#!/usr/bin/env python3
"""Validate a fresh two-replica, fault-free Service case precondition."""

import argparse
import hashlib
import json
from pathlib import Path
import urllib.parse

from capture_live import PROMETHEUS, request_json
from convert_live import (kubernetes_metadata, load_capture, metric_rows,
                          trace_rows, write_json)
from pod_precheck import FAMILIES


def prom_value(expression, time_utc):
    query = urllib.parse.urlencode({'query': expression, 'time': time_utc})
    response = request_json(PROMETHEUS + '/api/v1/query?' + query)
    if response.get('status') != 'success' or response['data']['resultType'] != 'vector':
        raise ValueError('Prometheus instant query failed')
    rows = response['data']['result']
    if len(rows) != 1:
        raise ValueError('Prometheus instant query did not return one scalar')
    return float(rows[0]['value'][1])


def validate(before_dir, service):
    capture = load_capture(before_dir, 'before')
    window = capture['window']
    if window['end_unix_ns'] - window['start_unix_ns'] < 600_000_000_000:
        raise ValueError('Fresh clean window is shorter than ten minutes')
    metadata = kubernetes_metadata(capture, 'readiness-precondition')
    pods = [pod for pod in metadata['pods'] if pod['service'] == service]
    if len(pods) != 2 or len({pod['uid'] for pod in pods}) != 2:
        raise ValueError('Service does not have two distinct stable Ready Pod UIDs')
    deployment = next((item for item in capture['kubernetes']['deployments']['items']
                       if item['metadata']['name'] == service), None)
    if not deployment or (deployment['spec']['replicas'],
                          deployment['status'].get('readyReplicas'),
                          deployment['status'].get('updatedReplicas')) != (2, 2, 2) or (
            deployment['metadata']['generation'] !=
            deployment['status'].get('observedGeneration')):
        raise ValueError('Two-replica Deployment is not fully stable')
    endpoints = [endpoint for item in capture['kubernetes']['endpointslices']['items']
                 if item['metadata'].get('labels', {}).get('kubernetes.io/service-name') == service
                 for endpoint in item.get('endpoints', [])
                 if endpoint.get('conditions', {}).get('ready') is True]
    if {endpoint.get('targetRef', {}).get('uid') for endpoint in endpoints} != {
            pod['uid'] for pod in pods}:
        raise ValueError('Service EndpointSlice does not contain both Ready Pod UIDs')
    metric = metric_rows(capture, metadata['pods'])
    counts = {}
    for pod in pods:
        entity = f"POD/{pod['namespace']}/{pod['name']}"
        counts[entity] = {name: len({row['time_unix_ns'] for row in metric
                                     if row['entity'] == entity and row['family'] == name})
                          for name in FAMILIES}
        if min(counts[entity].values()) < 20:
            raise ValueError('One replica has insufficient clean Pod telemetry')
    _, calls = trace_rows(capture, set(metadata['services']))
    inbound_calls = {edge: len(rows) for edge, rows in calls.items()
                     if edge.endswith('->' + service)}
    if not inbound_calls or sum(inbound_calls.values()) < 30:
        raise ValueError('Service lacks active clean public trace traffic')
    end = window['end_utc']
    requests = prom_value('sum(increase(k6_http_reqs_total{service_name="load-generator"}[10m]))',
                          end)
    failures = prom_value('sum(increase(k6_http_req_failed_total{service_name="load-generator",'
                          'condition="nonzero"}[10m])) or vector(0)', end)
    if requests < 100 or failures != 0:
        raise ValueError('Clean workload request/error gate failed')
    return {'status': 'F01_PRECONDITION_PASS', 'service': service,
            'selection_rationale': 'Two Ready replicas distinguish a service-wide root from a single Pod root',
            'clean_window': window, 'pods': [{key: pod[key] for key in
                                              ('name', 'uid', 'node', 'container_ids')}
                                             for pod in pods],
            'pod_metric_sample_counts': counts,
            'inbound_trace_calls': inbound_calls,
            'workload_requests_10m': requests,
            'workload_nonzero_failures_10m': failures,
            'capture_manifest_sha256': hashlib.sha256(
                (before_dir / 'capture-manifest.json').read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--before', required=True, type=Path)
    parser.add_argument('--service', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refusing to overwrite F01 precondition audit')
    result = validate(args.before, args.service)
    write_json(args.output, result)
    print(json.dumps({'status': result['status'], 'service': args.service,
                      'pod_uids': [pod['uid'] for pod in result['pods']],
                      'workload_requests_10m': result['workload_requests_10m']}))


if __name__ == '__main__':
    main()
