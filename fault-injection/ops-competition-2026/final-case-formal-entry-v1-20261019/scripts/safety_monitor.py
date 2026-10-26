#!/usr/bin/env python3
"""Read-only five-host heartbeat; missing proof blocks the formal sequence."""

import argparse
import json
import os
import shlex
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

from control_contract import ROOT, load_manifest
from storage_policy import ROOTS, check


HOSTS = {'k8s-worker1': ('192.168.53.53', '/run/ops-final-ssh/w1.sock'),
         'k8s-worker2': ('192.168.53.54', '/run/ops-final-ssh/w2.sock'),
         'k8s-worker3': ('192.168.53.55', '/run/ops-final-ssh/w3.sock'),
         'vm1': ('192.168.53.50', '/run/ops-final-ssh/vm1.sock')}


def utc():
    return datetime.now(timezone.utc).isoformat()


def command(host, argv):
    if host == 'k8s-master':
        call = list(map(str, argv))
    else:
        ip, socket = HOSTS[host]
        if not Path(socket).is_socket():
            raise RuntimeError('SAFETY_HOST_CONTROL_UNAVAILABLE:' + host)
        call = ['ssh', '-S', socket, '-o', 'BatchMode=yes',
                '-o', 'ConnectTimeout=3', 'root@' + ip,
                shlex.join(map(str, argv))]
    result = subprocess.run(call, text=True, capture_output=True, timeout=7)
    if result.returncode:
        raise RuntimeError('SAFETY_HOST_COMMAND_FAILED:' + host)
    return result.stdout


def read_space(host, mount):
    actual = command(host, ['findmnt', '-T', mount, '-n', '-o', 'TARGET']).strip()
    if actual != mount:
        raise RuntimeError('SAFETY_MOUNT_CHANGED:' + host + ':' + mount)
    output = command(host, ['df', '-B1', '--output=avail', mount]).splitlines()
    if len(output) != 2:
        raise RuntimeError('SAFETY_DF_PARSE:' + host + ':' + mount)
    return int(output[1].strip())


def sample(manifest, *, space=read_space, run_command=command):
    run = manifest['run_id']
    case = run.split('-ATTEMPT-', 1)[0]
    free = {(host, mount): space(host, mount)
            for host, mounts in ROOTS.items() for mount in mounts}
    budget = check(case, free)
    if budget['status'] != 'STORAGE_PASS':
        raise RuntimeError('SAFETY_STORAGE_FLOOR_OR_RESERVE')
    nodes = json.loads(run_command('k8s-master',
        ['kubectl', 'get', 'nodes', '-o', 'json']))['items']
    if (len(nodes) != 4 or any(not any(c.get('type') == 'Ready' and
        c.get('status') == 'True' for c in node['status']['conditions'])
        for node in nodes)):
        raise RuntimeError('SAFETY_NODES_NOT_READY')
    collector = json.loads(run_command('k8s-master', ['kubectl', '-n', 'otel-demo',
        'get', 'deployment', 'otel-collector', '-o', 'json']))
    if collector.get('status', {}).get('readyReplicas') != collector['spec']['replicas']:
        raise RuntimeError('SAFETY_COLLECTOR_NOT_READY')
    expected = {action['unit'] + '.timer' for action in manifest['actions']
                if action['worker'] != 'k8s-master'}
    for host in ('k8s-master', *HOSTS):
        units = run_command(host, ['systemctl', 'list-units', '--type=timer',
            '--state=active', '--no-legend', 'ops-sf0*'])
        active = {line.split()[0] for line in units.splitlines() if line.strip()}
        allowed = {item for item in expected if any(
            a.get('unit', '') + '.timer' == item and a['worker'] == host
            for a in manifest['actions'])}
        allowed.update('ops-' + a['action_id'].lower() + '-recovery.timer'
                       for a in manifest['actions'] if a['worker'] == host)
        if active - allowed:
            raise RuntimeError('SAFETY_UNREGISTERED_TIMER:' + host)
        qdisc = run_command(host, ['tc', 'qdisc', 'show'])
        if 'netem' in qdisc or ' tbf ' in qdisc:
            raise RuntimeError('SAFETY_NETWORK_FAULT_RESIDUE:' + host)
    with urlopen('http://10.97.14.138:9090/-/healthy', timeout=3) as response:
        if response.status != 200:
            raise RuntimeError('SAFETY_PROMETHEUS')
    with urlopen('http://10.111.114.255:9200/_cluster/health', timeout=3) as response:
        if json.load(response).get('status') != 'green':
            raise RuntimeError('SAFETY_OPENSEARCH')
    return {'status': 'DYNAMIC_STORAGE_AND_BASIC_HEALTH_PASS',
            'run_id': run, 'at_utc': utc(),
            'space': [{'host': host, 'mount': mount, 'free_bytes': count}
                      for (host, mount), count in sorted(free.items())],
            'budget': budget, 'nodes_ready': 4}


def write_atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    temp = path.with_suffix('.next')
    if temp.exists():
        raise RuntimeError('SAFETY_HEARTBEAT_PENDING_WRITE')
    temp.write_text(json.dumps(value, sort_keys=True) + '\n')
    temp.chmod(0o600)
    os.replace(temp, path)


def run(manifest, *, interval=10, sleep=time.sleep):
    path = ROOT / 'runtime' / manifest['run_id'] / 'master' / 'safety-heartbeat.json'
    while True:
        try:
            result = sample(manifest)
        except BaseException as error:
            result = {'status': 'SAFETY_BLOCKED', 'run_id': manifest['run_id'],
                      'at_utc': utc(), 'error': type(error).__name__ + ':' + str(error)}
        write_atomic(path, result)
        if result['status'] != 'DYNAMIC_STORAGE_AND_BASIC_HEALTH_PASS':
            raise RuntimeError('FORMAL_SAFETY_MONITOR_BLOCKED')
        sleep(interval)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--manifest-sha', required=True)
    args = parser.parse_args()
    run(load_manifest(args.manifest, args.manifest_sha, allow_rehearsal=False))


if __name__ == '__main__':
    main()
