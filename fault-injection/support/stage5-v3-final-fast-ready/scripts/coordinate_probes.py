#!/usr/bin/env python3
"""Mac coordinator for detached W2->W3 read-only probes around one Clean run."""

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import time


HOSTS = {'master': ('192.168.53.52', '/tmp/s5v3-final-master.sock'),
         'worker2': ('192.168.53.54', '/tmp/s5v3-final-w2.sock'),
         'worker3': ('192.168.53.55', '/tmp/s5v3-final-w3.sock')}
SOURCE = Path('stage5-v3-autonomous-preparation/scripts/probe_network.py')
REMOTE_SCRIPT = '/root/stage5-v3-final-fast-ready/scripts/probe_network.py'
DATA = '/data/stage5-v3/final-fast-ready/runs'
TEMP = '/data/rca-archives/stage5-v3-final-fast-ready-probes'
PATH = 'NETWORK_PATH/k8s-worker2/k8s-worker3'


def ssh(host, *argv):
    ip, sock = HOSTS[host]
    return ['ssh', '-o', 'BatchMode=yes', '-o', 'ControlPath=' + sock,
            'root@' + ip, *map(str, argv)]


def remote(host, *argv):
    result = subprocess.run(ssh(host, *argv), capture_output=True, text=True,
                            timeout=30)
    if result.returncode:
        raise RuntimeError(host + ' command failed: ' + result.stderr[-500:])
    return result.stdout


def schedule(run_id):
    value = json.loads(remote('master', 'cat', DATA + '/' + run_id + '/schedule.json'))
    if value['run_id'] != run_id or value['fault_injected'] or value[
            'trace_source'] != 'JAEGER_OPENSEARCH_STORAGE':
        raise RuntimeError('Unregistered or faulted Clean schedule')
    return value


def arm(run_id):
    plan = schedule(run_id)
    start = datetime.fromisoformat(plan['scheduled_start_utc'])
    if not 10 < (start - datetime.now(timezone.utc)).total_seconds() < 180:
        raise RuntimeError('Probe arm must finish before Clean starts')
    if remote('worker3', 'ss', '-ltn', 'sport', '=', ':5201').count(':5201'):
        raise RuntimeError('Probe receiver port occupied')
    root = TEMP + '/' + run_id
    remote('worker2', 'mkdir', '-p', root, '/root/stage5-v3-final-fast-ready/scripts')
    copy = subprocess.run(['scp', '-q', '-o', 'ControlPath=' + HOSTS[
        'worker2'][1], str(SOURCE), 'root@' + HOSTS['worker2'][0] + ':' +
        REMOTE_SCRIPT], capture_output=True, text=True, timeout=30)
    if copy.returncode:
        raise RuntimeError('Probe source sync failed')
    receiver = 's5v3ffr-receiver-' + run_id.lower()
    remote('worker3', 'systemd-run', '--unit=' + receiver,
        '--property=RuntimeMaxSec=1700', '/usr/bin/timeout', '1650',
        '/usr/bin/iperf3', '-s', '-B', '192.168.53.55', '-p', '5201')
    registered = []
    try:
        for phase, offset, count in (('before', 1, 120), ('during', 601, 60),
                                     ('after', 901, 120)):
            unit = 's5v3ffr-probe-' + phase + '-' + run_id.lower()
            begin = (start + timedelta(seconds=offset)).isoformat()
            remote('worker2', 'systemd-run', '--unit=' + unit,
                '--property=RuntimeMaxSec=1700', '/usr/bin/python3', '-B',
                REMOTE_SCRIPT, '--path', PATH, '--source-ip', '192.168.53.54',
                '--destination-ip', '192.168.53.55', '--start-utc', begin,
                '--samples', str(count), '--interval', '5', '--output', root + '/' + phase)
            registered.append(unit + '.service')
    except Exception:
        for unit in registered:
            subprocess.run(ssh('worker2', 'systemctl', 'stop', unit),
                           capture_output=True, timeout=20)
        subprocess.run(ssh('worker3', 'systemctl', 'stop', receiver + '.service'),
                       capture_output=True, timeout=20)
        raise
    return {'status': 'W2_W3_PROBES_ARMED', 'run_id': run_id,
        'scheduled_start_utc': plan['scheduled_start_utc'],
        'receiver_unit': receiver + '.service', 'probe_units': registered,
        'remote_raw_root': root, 'fault_injected': False}


def stream_copy(source_host, source, target_host, target):
    remote(target_host, 'mkdir', '-p', str(Path(target).parent))
    if remote(target_host, 'test', '!', '-e', target).strip():
        raise RuntimeError('Unexpected target-existence result')
    remote(target_host, 'mkdir', target)
    reader = subprocess.Popen(ssh(source_host, 'tar', '-C', source,
        '-cf', '-', '.'), stdout=subprocess.PIPE,
        stderr=subprocess.PIPE)
    writer = subprocess.Popen(ssh(target_host, 'tar', '-C', target, '-xf', '-'),
        stdin=reader.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    reader.stdout.close()
    _, error = writer.communicate(timeout=600)
    _, source_error = reader.communicate(timeout=30)
    if reader.returncode or writer.returncode:
        raise RuntimeError('Probe archive stream failed: ' +
                           (source_error + error)[-500:].decode(errors='replace'))


def collect(run_id):
    plan = schedule(run_id)
    end = datetime.fromisoformat(plan['scheduled_end_utc'])
    if (datetime.now(timezone.utc) - end).total_seconds() < 5:
        raise RuntimeError('Probe collection before run end')
    source = TEMP + '/' + run_id
    target = DATA + '/' + run_id + '/probes'
    receiver = 's5v3ffr-receiver-' + run_id.lower() + '.service'
    try:
        for phase, count in (('before', 120), ('during', 60), ('after', 120)):
            row = json.loads(remote('worker2', 'cat', source + '/' + phase +
                                    '/manifest.json'))
            if row['path'] != PATH or row['planned_samples'] != count or row[
                    'interval_seconds'] != 5 or sum(row['status_counts'].values()) != count:
                raise RuntimeError('Probe phase incomplete: ' + phase)
        stream_copy('worker2', source, 'master', target)
        for phase in ('before', 'during', 'after'):
            old = remote('worker2', 'sha256sum', source + '/' + phase +
                         '/observations.jsonl').split()[0]
            new = remote('master', 'sha256sum', target + '/' + phase +
                         '/observations.jsonl').split()[0]
            if old != new:
                raise RuntimeError('Probe transfer SHA mismatch: ' + phase)
    finally:
        remote('worker3', 'systemctl', 'stop', receiver)
    return {'status': 'W2_W3_PROBES_TRANSFERRED_VERIFIED_CLEANED',
        'run_id': run_id, 'target': target, 'receiver_stopped': True,
        'fault_injected': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('arm', 'collect'))
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists(): raise ValueError('Refusing output overwrite')
    result = arm(args.run_id) if args.action == 'arm' else collect(args.run_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'status': result['status'], 'run_id': args.run_id}))


if __name__ == '__main__': main()
