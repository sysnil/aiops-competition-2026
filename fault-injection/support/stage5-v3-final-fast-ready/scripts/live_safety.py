"""Fast Master-local no-fault safety tick between full Global Safety snapshots."""

from datetime import datetime, timezone
import json
import subprocess
import urllib.parse
import urllib.request


KUBECTL = ['/usr/bin/kubectl', '--kubeconfig=/etc/kubernetes/admin.conf',
           '--context=kubernetes-admin@kubernetes']
PROM = 'http://10.97.14.138:9090'
OPENSEARCH = 'http://10.111.114.255:9200'


def query(*argv):
    return json.loads(subprocess.check_output(KUBECTL + list(argv) + ['-o', 'json'],
                      text=True, timeout=30))


def url_json(url):
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.load(response)


def tick():
    nodes = query('get', 'nodes')['items']
    expected = {'k8s-master', 'k8s-worker1', 'k8s-worker2', 'k8s-worker3'}
    if {row['metadata']['name'] for row in nodes} != expected:
        raise RuntimeError('Four-node safety inventory changed')
    for row in nodes:
        conditions = {c['type']: c['status'] for c in row['status']['conditions']}
        if conditions.get('Ready') != 'True' or any(conditions.get(name) != 'False'
           for name in ('DiskPressure', 'MemoryPressure', 'PIDPressure')):
            raise RuntimeError('Node Ready/pressure safety failed')
    leases = query('-n', 'kube-node-lease', 'get', 'leases')['items']
    now = datetime.now(timezone.utc)
    for row in leases:
        if row['metadata']['name'] in expected:
            stamp = datetime.fromisoformat(row['spec']['renewTime'].replace('Z', '+00:00'))
            if (now - stamp).total_seconds() > 60:
                raise RuntimeError('Kubelet lease stale')
    with urllib.request.urlopen(PROM + '/-/ready', timeout=10) as response:
        if response.status != 200:
            raise RuntimeError('Prometheus not ready')
    if url_json(OPENSEARCH + '/_cluster/health')['status'] != 'green':
        raise RuntimeError('OpenSearch not green')
    prom = url_json(PROM + '/api/v1/query?' + urllib.parse.urlencode({'query':
        'sum(increase(k6_http_req_failed_total{service_name="load-generator",'
        'condition="nonzero"}[2m])) or vector(0)'}))
    rows = prom['data']['result'] if prom['status'] == 'success' else []
    if len(rows) != 1 or float(rows[0]['value'][1]) != 0:
        raise RuntimeError('Background workload HTTP failure')
    pods = query('-n', 'otel-demo', 'get', 'pods')['items']
    for prefix in ('otel-collector-', 'otel-cluster-collector-', 'jaeger-',
                   'prometheus-'):
        matches = [p for p in pods if p['metadata']['name'].startswith(prefix)]
        if not matches or any(p['status'].get('phase') != 'Running' or not all(
                c.get('ready') for c in p['status'].get('containerStatuses', []))
                for p in matches):
            raise RuntimeError('Telemetry component not Ready: ' + prefix)
    return {'status': 'NO_FAULT_LIVE_SAFETY_PASS', 'at_utc': now.isoformat(),
            'nodes_ready': 4, 'opensearch': 'green', 'http_failures_2m': 0}
