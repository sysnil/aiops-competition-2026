#!/usr/bin/env python3
"""Freeze a current, public service inventory and observed call graph."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

import yaml


BUSINESS = {'frontend-proxy', 'frontend', 'product-catalog', 'recommendation',
            'ad', 'cart', 'checkout', 'currency', 'shipping', 'quote', 'payment',
            'email', 'image-provider'}
BACKGROUND = {'accounting', 'fraud-detection', 'kafka', 'valkey-cart', 'astronomy-db'}
CONTROL = {'flagd', 'load-generator', 'otel-collector', 'otel-cluster-collector',
           'prometheus', 'jaeger', 'opensearch', 'grafana', 'agent', 'mcp',
           'opamp-server', 'telemetry-docs', 'chatbot'}


def kube(*args):
    return json.loads(subprocess.check_output(['kubectl', '-n', 'otel-demo',
        *args, '-o', 'json'], text=True, timeout=30))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', required=True, type=Path)
    parser.add_argument('--machine', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    baseline = json.loads(args.baseline.read_text())
    machine = yaml.safe_load(args.machine.read_text())
    services = kube('get', 'svc')['items']
    pods = kube('get', 'pods')['items']
    service_names = {row['metadata']['name'] for row in services}
    pod_rows = []
    for row in pods:
        name = row['metadata']['labels'].get('opentelemetry.io/name')
        if not name:
            continue
        pod_rows.append({'service': name, 'pod': row['metadata']['name'],
                         'uid': row['metadata']['uid'], 'ip': row['status'].get('podIP'),
                         'node': row['spec'].get('nodeName'),
                         'ready': all(item.get('ready') for item in row['status'].get('containerStatuses', []))})
    edge_rows = []
    for edge, count in baseline['edges'].items():
        source, destination = edge.split('->', 1)
        edge_rows.append({'source': source, 'destination': destination,
                          'baseline_span_count': count,
                          'category': 'BUSINESS_REACHABLE' if source in BUSINESS and destination in BUSINESS
                                      else 'CONTROL_OR_BACKGROUND',
                          'evidence': {'source': args.baseline.as_posix(),
                                       'window_start': baseline['window_start'],
                                       'window_end': baseline['window_end']}})
    graph = {'created_utc': datetime.now(timezone.utc).isoformat(),
             'source': 'current Kubernetes objects + JAEGER_OPENSEARCH_STORAGE',
             'baseline_sha256': hashlib.sha256(args.baseline.read_bytes()).hexdigest(),
             'services': [{'name': name,
                           'classification': ('BUSINESS' if name in BUSINESS else
                                              'BACKGROUND' if name in BACKGROUND else
                                              'CONTROL_OR_OTHER'),
                           'pods': [row for row in pod_rows if row['service'] == name],
                           'baseline_spans': baseline['services'].get(name, 0)}
                          for name in sorted(service_names)],
             'edges': edge_rows,
             'business_service_denominator': sorted(BUSINESS & service_names),
             'business_edge_denominator': sorted(edge['source'] + '->' + edge['destination']
                 for edge in edge_rows if edge['category'] == 'BUSINESS_REACHABLE'),
             'pod_identity_in_trace': baseline['pod_identified_span_fraction'],
             'network_path_coverage_status': 'UNVERIFIED_PENDING_FRESH_ROUTE_AND_FLOW_PROOF'}
    catalog = {'created_utc': graph['created_utc'], 'api_provenance': machine['provenance'],
               'verified_entrypoints': machine['operations'],
               'workflows': machine['workflows'],
               'business_services': graph['business_service_denominator'],
               'background_or_control': sorted((BACKGROUND | CONTROL) & service_names),
               'branch_basis': 'state graph derived only from deployed k6 HTTP tasks',
               'excluded_unverified_apis': ['direct shipping API', 'direct payment API',
                  'direct currency API', 'direct image-provider API'],
               'limitations': ['frontend-web is a browser instrumentation identity, not a Kubernetes Service',
                  'current HTTP generator does not execute browser asset requests',
                  'business edges are observed in a finite trace window, not a complete static architecture']}
    (args.output/'service-call-graph.json').write_text(json.dumps(graph, indent=2)+'\n')
    (args.output/'business-workflow-catalog.json').write_text(json.dumps(catalog, indent=2)+'\n')
    print(json.dumps({'services':len(graph['business_service_denominator']),
                      'edges':len(graph['business_edge_denominator']),
                      'pods':len(pod_rows)}))


if __name__ == '__main__':
    main()
