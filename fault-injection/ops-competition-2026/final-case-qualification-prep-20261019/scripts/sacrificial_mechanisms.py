#!/usr/bin/env python3
"""Bounded non-business processes for PodMemory and dual-replica pause plumbing."""

import argparse
import hashlib
import json
import os
import signal
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/root/ops-competition-2026/final-case-qualification-prep-20261019/results')


def utc():
    return datetime.now(timezone.utc).isoformat()


def proc(pid):
    fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
    return {'pid': pid, 'state': fields[0], 'start_ticks': int(fields[19])}


def same(row):
    try:
        return proc(row['pid'])['start_ticks'] == row['start_ticks']
    except FileNotFoundError:
        return False


def memory_available():
    for line in Path('/proc/meminfo').read_text().splitlines():
        if line.startswith('MemAvailable:'):
            return int(line.split()[1]) * 1024
    raise RuntimeError('MEMAVAILABLE_MISSING')


def cleanup(path):
    record = json.loads(Path(path).read_text())
    if record['host'] != socket.gethostname():
        raise RuntimeError('SACRIFICIAL_WRONG_HOST')
    actions = []
    for item in record['children']:
        if not same(item):
            raise RuntimeError('SACRIFICIAL_PID_REUSED_OR_MISSING')
        if record['kind'] == 'SERVICE_DUAL_PAUSE':
            os.kill(item['pid'], signal.SIGCONT)
            actions.append('SIGCONT')
        else:
            os.kill(item['pid'], signal.SIGTERM)
            actions.append('SIGTERM')
    result = {'status': 'SACRIFICIAL_WATCHDOG_ACTION_COMPLETE',
              'kind': record['kind'], 'actions': actions, 'at_utc': utc()}
    path = Path(path).with_name('watchdog-action.json')
    with path.open('x') as stream:
        json.dump(result, stream)
        stream.write('\n')
    return result


def run(kind, token):
    expected_host = {'POD_MEMORY_32M': 'k8s-worker2',
                     'SERVICE_DUAL_PAUSE': 'k8s-worker3'}[kind]
    if socket.gethostname() != expected_host or not token.isalnum() or len(token) != 8:
        raise ValueError('SACRIFICIAL_SCOPE')
    if memory_available() < 8 * 1024**3:
        raise RuntimeError('SACRIFICIAL_NODE_MEMORY_FLOOR')
    folder = ROOT / (kind.lower() + '-' + token)
    folder.mkdir(parents=True, mode=0o700, exist_ok=False)
    children = []
    if kind == 'POD_MEMORY_32M':
        code = ('import time; data=bytearray(32*1024*1024);'
                'data[::4096]=b"x"*(len(data)//4096);time.sleep(30)')
        children.append(subprocess.Popen(['/usr/bin/python3', '-c', code]))
    else:
        children = [subprocess.Popen(['/usr/bin/sleep', '30']) for _ in range(2)]
    time.sleep(.3)
    records = [proc(child.pid) for child in children]
    unit = 'ops-sfqual-' + token.lower()
    registration = {'kind': kind, 'host': expected_host, 'children': records,
                    'unit': unit, 'at_utc': utc()}
    state = folder / 'registration.json'
    state.write_text(json.dumps(registration, sort_keys=True) + '\n')
    result = {'status': 'SACRIFICIAL_MECHANISM_FAILED', **registration,
              'memory_available_before_bytes': memory_available()}
    try:
        subprocess.run(['systemd-run', '--unit=' + unit, '--on-active=3s',
            '--timer-property=AccuracySec=100ms',
            '--timer-property=Persistent=false', '--property=RuntimeMaxSec=12',
            '/usr/bin/python3', '-B', str(Path(__file__).resolve()),
            'cleanup', '--record', str(state)], check=True, capture_output=True,
            timeout=15)
        if kind == 'SERVICE_DUAL_PAUSE':
            for child in children:
                os.kill(child.pid, signal.SIGSTOP)
            time.sleep(.2)
            if not all(proc(child.pid)['state'] in ('T', 't') for child in children):
                raise RuntimeError('DUAL_SACRIFICIAL_PAUSE_NOT_APPLIED')
        time.sleep(5)
        action = json.loads((folder / 'watchdog-action.json').read_text())
        if kind == 'SERVICE_DUAL_PAUSE':
            if not all(same(row) and proc(row['pid'])['state'] not in ('T', 't')
                       for row in records):
                raise RuntimeError('DUAL_SACRIFICIAL_RESUME_FAILED')
        elif any(child.poll() is None for child in children):
            raise RuntimeError('SACRIFICIAL_MEMORY_HOLDER_NOT_TERMINATED')
        if len(action['actions']) != len(children):
            raise RuntimeError('SACRIFICIAL_WATCHDOG_SCOPE')
        result.update(status='SACRIFICIAL_MECHANISM_PASS',
                      watchdog_action=action,
                      memory_available_after_bytes=memory_available())
    finally:
        for child in children:
            if child.poll() is None:
                os.kill(child.pid, signal.SIGCONT)
                child.terminate()
            child.wait(timeout=8)
        for suffix in ('.timer', '.service'):
            subprocess.run(['systemctl', 'stop', unit + suffix],
                           capture_output=True, timeout=10)
        result['completed_utc'] = utc()
        (folder / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('verb', choices=('run', 'cleanup'))
    parser.add_argument('--kind', choices=('POD_MEMORY_32M', 'SERVICE_DUAL_PAUSE'))
    parser.add_argument('--token')
    parser.add_argument('--record', type=Path)
    args = parser.parse_args()
    result = cleanup(args.record) if args.verb == 'cleanup' else run(args.kind, args.token)
    print(json.dumps(result))
    if result['status'] == 'SACRIFICIAL_MECHANISM_FAILED':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
