#!/usr/bin/env python3
"""Short Stage4F health gate immediately after global freeze and before faults."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
import urllib.parse
import urllib.request

from contracts import sha256, verify as verify_contracts
from network_path_precheck import validate as validate_path
from pod_precheck import FAMILIES
from trace_storage_reader import index_names


PROM = 'http://10.97.14.138:9090'
JAEGER = 'http://10.107.96.1:16686/jaeger/ui/api'
OPENSEARCH = 'http://10.111.114.255:9200'
ROUTE_PROOF = Path('readiness/f04-route-proof-podprobe-2026-10-03/public_verified_route_map.json')


def kubectl(*args):
    return subprocess.check_output(['kubectl', *args], text=True, timeout=30)


def kube_json(*args):
    return json.loads(kubectl(*args, '-o', 'json'))


def get_json(url):
    with urllib.request.urlopen(url, timeout=12) as response:
        return json.load(response)


def post_json(url, body):
    request = urllib.request.Request(url, data=json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=12) as response:
        return json.load(response)


def prom_query(expression):
    data = get_json(PROM + '/api/v1/query?' + urllib.parse.urlencode({'query': expression}))
    if data.get('status') != 'success' or data['data']['resultType'] != 'vector':
        raise ValueError('Prometheus query failed')
    return data['data']['result']


def scalar(expression):
    rows = prom_query(expression)
    if len(rows) != 1:
        raise ValueError('Prometheus scalar has no unique value')
    return float(rows[0]['value'][1])


def ready(item):
    return any(row['type'] == 'Ready' and row['status'] == 'True'
               for row in item['status']['conditions'])


def run(root, residue_file, dry_run=False, recovery=False):
    if not dry_run:
        freeze = verify_contracts(root)
    else:
        if (root / 'freeze-manifest.json').exists():
            raise ValueError('Pre-freeze dry run cannot replace the formal gate')
        freeze = None
    residue = json.loads(residue_file.read_text())
    observed = datetime.fromisoformat(residue['at_utc'])
    age = (datetime.now(timezone.utc) - observed).total_seconds()
    if residue.get('status') != 'HOST_RESIDUE_PASS' or len(residue.get('hosts', {})) < 5 or (
            not 0 <= age <= 180):
        raise ValueError('Five-host residue audit is absent or stale')
    nodes = kube_json('get', 'nodes')['items']
    node_names = {item['metadata']['name'] for item in nodes if ready(item)}
    if node_names != {'k8s-master', 'k8s-worker1', 'k8s-worker2', 'k8s-worker3'}:
        raise ValueError('Four Kubernetes Nodes are not Ready')
    calico_pods = kubectl('-n', 'calico-system', 'get', 'pods',
                          '-l', 'k8s-app=calico-node', '-o', 'name').splitlines()
    if len(calico_pods) != 4:
        raise ValueError('Calico Node DaemonSet has fewer than four Pods')
    bgp = {}
    for pod in calico_pods:
        output = kubectl('-n', 'calico-system', 'exec', pod, '-c', 'calico-node',
                         '--', '/bin/calico-node', '-bird-ready')
        match = re.search(r'established = (\d+)', output)
        if not match or int(match.group(1)) != 3:
            raise ValueError('Calico BGP peer count is not 3/3: ' + pod)
        bgp[pod] = 3
    requests = scalar('sum(increase(k6_http_reqs_total{service_name="load-generator"}[2m]))')
    failures = scalar('sum(increase(k6_http_req_failed_total{service_name="load-generator",'
                      'condition="nonzero"}[2m])) or vector(0)')
    if requests < 20 or failures != 0:
        raise ValueError('Clean workload request/error gate failed')
    with urllib.request.urlopen(PROM + '/-/ready', timeout=12) as response:
        if response.status != 200:
            raise ValueError('Prometheus is not Ready')
    search = get_json(OPENSEARCH + '/_cluster/health')
    if search.get('status') != 'green':
        raise ValueError('OpenSearch is not green')
    if 'frontend' not in get_json(JAEGER + '/services').get('data', []):
        raise ValueError('Jaeger lacks the frontend service')
    now = datetime.now(timezone.utc)
    two_minutes_ago = now.timestamp() - 120
    indices = ','.join(index_names(datetime.fromtimestamp(two_minutes_ago, timezone.utc), now))
    traces = post_json(OPENSEARCH + '/' + indices + '/_count', {
        'query': {'bool': {'filter': [
            {'range': {'startTimeMillis': {'gte': int(two_minutes_ago * 1000),
                                           'lt': int(now.timestamp() * 1000)}}},
            {'term': {'process.serviceName': 'frontend'}}]}}})
    if traces.get('count', 0) < 1 or traces.get('_shards', {}).get('failed') != 0:
        raise ValueError('Jaeger OpenSearch storage has no recent frontend spans')
    collector = kube_json('-n', 'otel-demo', 'get', 'deployment', 'otel-collector')
    if collector['status'].get('readyReplicas') != collector['spec']['replicas']:
        raise ValueError('OTel Collector Deployment is not Ready')
    ad = kube_json('-n', 'otel-demo', 'get', 'deployment', 'ad')
    pods = kube_json('-n', 'otel-demo', 'get', 'pods', '-l', 'opentelemetry.io/name=ad')['items']
    if ad['spec']['replicas'] != 2 or ad['status'].get('readyReplicas') != 2 or len(pods) != 2:
        raise ValueError('ad has fewer than two Ready replicas')
    pod_uids = set()
    for pod in pods:
        status = pod['status']['containerStatuses'][0]
        if not status['ready'] or status['restartCount'] != 0:
            raise ValueError('ad Pod not Ready or restarted')
        pod_uids.add(pod['metadata']['uid'])
        container_id = status['containerID'].removeprefix('containerd://')
        for family in FAMILIES:
            rows = prom_query(family + '{namespace="otel-demo",pod="' +
                              pod['metadata']['name'] + '",container="ad"}')
            matches = [row for row in rows if row['metric'].get('name') == container_id]
            if len(matches) != 1:
                raise ValueError('ad Pod telemetry is missing: ' + family)
    if len(pod_uids) != 2:
        raise ValueError('ad replicas share a Pod UID')
    proof_file = root / ROUTE_PROOF
    proof = json.loads(proof_file.read_text())
    if validate_path(proof)['status'] != 'NETWORK_PATH_PRECHECK_PASS':
        raise ValueError('F04 public route proof is invalid')
    if freeze and freeze.get('f04_route_proof_sha256') != sha256(proof_file):
        raise ValueError('F04 route proof changed after global freeze')
    current_pods = {item['metadata']['uid']: item for item in
                    kube_json('-n', 'otel-demo', 'get', 'pods')['items']}
    for edge in proof['service_edges']:
        for prefix in ('source', 'destination'):
            pod = current_pods.get(edge[prefix + '_pod_uid'])
            if not pod or pod['spec']['nodeName'] != edge[prefix + '_node']:
                raise ValueError('F04 route-proof Pod UID or placement changed')
    return {'status': ('PRE_FREEZE_HEALTH_SMOKE_PASS' if dry_run else
                       'RECOVERY_GATE_PASS' if recovery else
                       'POST_FREEZE_HEALTH_GATE_PASS'),
            'at_utc': datetime.now(timezone.utc).isoformat(),
            'freeze_manifest_sha256': sha256(root / 'freeze-manifest.json') if freeze else None,
            'residue_sha256': sha256(residue_file), 'nodes_ready': sorted(node_names),
            'calico_bgp_peers': bgp, 'workload_requests_2m': requests,
            'workload_nonzero_failures_2m': failures, 'opensearch': 'green',
            'jaeger_service_ready': True,
            'trace_source': 'JAEGER_OPENSEARCH_STORAGE',
            'trace_storage_recent_frontend_spans': traces['count'],
            'otel_collector_ready': True,
            'ad_pod_uids': sorted(pod_uids), 'ad_five_metric_families': list(FAMILIES),
            'route_proof_sha256': sha256(proof_file)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--residue', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--recovery', action='store_true')
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refusing to overwrite a health gate')
    try:
        if args.dry_run and args.recovery:
            raise ValueError('Recovery and dry-run modes are mutually exclusive')
        payload = run(args.root, args.residue, args.dry_run, args.recovery)
    except Exception as exc:
        payload = {'status': ('PRE_FREEZE_HEALTH_SMOKE_FAILED' if args.dry_run else
                              'RECOVERY_GATE_FAILED' if args.recovery else
                              'POST_FREEZE_HEALTH_GATE_FAILED'),
                   'at_utc': datetime.now(timezone.utc).isoformat(),
                   'reason': str(exc)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + '\n')
    print(json.dumps(payload))
    if payload['status'].endswith('FAILED'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
