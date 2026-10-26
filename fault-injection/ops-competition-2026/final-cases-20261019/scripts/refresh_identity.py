"""Read-only Kubernetes, process, and cgroup identity check for new attempts."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shlex
import subprocess


HISTORICAL = Path('ops-competition-2026/low-stable-v4-20261017/results/sf007-attempt01-identity.json')
HOSTS = {
    'k8s-master': ('192.168.53.52', '/tmp/sf-final-master.sock'),
    'k8s-worker1': ('192.168.53.53', '/tmp/sf-final-w1.sock'),
    'k8s-worker2': ('192.168.53.54', '/tmp/sf-final-w2.sock'),
    'k8s-worker3': ('192.168.53.55', '/tmp/sf-final-w3.sock'),
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def remote(host, args):
    address, socket = HOSTS[host]
    result = subprocess.run(
        ['ssh', '-S', socket, '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
         'root@' + address, shlex.join(list(map(str, args)))],
        capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError(host + ' read-only identity query failed: ' + result.stderr[-600:])
    return result.stdout


def process(host, pid):
    code = '''import json,pathlib
p=%r
fields=pathlib.Path("/proc",str(p),"stat").read_text().rsplit(")",1)[1].split()
cg=pathlib.Path("/proc",str(p),"cgroup").read_text().strip().split("::",1)[1]
x={"pid":p,"start_ticks":fields[19],"process_state":fields[0],
"cgroup":str(pathlib.Path("/sys/fs/cgroup")/cg.lstrip("/"))}
c=pathlib.Path(x["cgroup"])
print(json.dumps({"process":x,"memory_current_bytes":int((c/"memory.current").read_text()),
"memory_max_bytes":(c/"memory.max").read_text().strip(),
"memory_events":(c/"memory.events").read_text(),
"cpu_max":(c/"cpu.max").read_text().strip()}))''' % pid
    return json.loads(remote(host, ['python3', '-B', '-c', code]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError('Identity snapshot already exists')
    old = json.loads(HISTORICAL.read_text())
    if old['status'] != 'CANDIDATE_CGROUPS_INSPECTED_NOT_QUALIFIED' or len(old['targets']) != 4:
        raise RuntimeError('Historical target universe changed')
    pods = json.loads(remote('k8s-master', ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf',
        '-n', 'otel-demo', 'get', 'pods', '-o', 'json']))['items']
    by_uid = {pod['metadata']['uid']: pod for pod in pods}
    targets = []
    for prior in old['targets']:
        pod = by_uid.get(prior['uid'])
        if pod is None or pod['metadata'].get('deletionTimestamp'):
            raise RuntimeError('Historical Pod UID disappeared')
        containers = pod['status'].get('containerStatuses', [])
        if (pod['metadata']['name'] != prior['name'] or
                pod['spec']['nodeName'] != prior['node'] or
                pod['status'].get('podIP') != prior['pod_ip'] or
                len(containers) != 1 or not containers[0].get('ready') or
                containers[0]['containerID'] != prior['container_id'] or
                containers[0]['restartCount'] != prior['restart_count']):
            raise RuntimeError('Pod placement, container or restart identity drift')
        live = process(prior['node'], prior['container_task_pid'])
        current = live['process']
        before = prior['processes'][0]
        if (current['pid'] != prior['container_task_pid'] or
                str(current['start_ticks']) != str(before['start_ticks']) or
                current['cgroup'] != prior['cgroup'] or
                prior['container_id'].split('://', 1)[1] not in current['cgroup'] or
                current['process_state'] in ('T', 't', 'Z', 'X')):
            raise RuntimeError('Exact PID/start-ticks/cgroup differs from qualified identity')
        row = dict(prior)
        row.update(memory_current_bytes=live['memory_current_bytes'],
                   memory_max_bytes=live['memory_max_bytes'],
                   memory_events=live['memory_events'], cpu_max=live['cpu_max'])
        if live['memory_max_bytes'] == 'max':
            raise RuntimeError('Container memory limit is unbounded')
        headroom = int(live['memory_max_bytes']) * 4 // 5 - live['memory_current_bytes']
        row.pop('old_adapter_headroom_bytes', None)
        row['live_80pct_headroom_bytes'] = headroom
        row['conservative_32MiB_plus_32MiB_overhead_fits_80pct'] = (
            headroom >= 64 * 1024**2)
        row['processes'] = [{'pid': current['pid'],
                            'start_ticks': str(current['start_ticks']),
                            'state': current['process_state'],
                            'comm': before['comm']}]
        targets.append(row)
    for service in ('frontend', 'recommendation'):
        expected = {row['uid'] for row in targets if row['service'] == service}
        actual = {pod['metadata']['uid'] for pod in pods
                  if pod['metadata']['labels'].get('opentelemetry.io/name') == service
                  and not pod['metadata'].get('deletionTimestamp')}
        if expected != actual:
            raise RuntimeError('Service replica universe changed: ' + service)
    result = {'status': 'CANDIDATE_CGROUPS_INSPECTED_NOT_QUALIFIED',
              'at_utc': datetime.now(timezone.utc).isoformat(),
              'targets': targets, 'faults_injected': 0,
              'historical_identity_sha256': sha(HISTORICAL),
              'inspection_source_sha256': sha(Path(__file__))}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': result['status'],
                      'targets': len(targets), 'sha256': sha(args.output)}))


if __name__ == '__main__':
    main()
