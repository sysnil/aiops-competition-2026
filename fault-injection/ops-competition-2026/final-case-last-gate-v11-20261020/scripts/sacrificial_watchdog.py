#!/usr/bin/env python3
"""Worker3-only exact-PID sacrificial systemd recovery rehearsal."""

import argparse
import json
import os
import signal
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/root/ops-competition-2026/final-case-last-gate-v11-20261020/runtime')


def ticks(pid):
    return int(Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19])


def state(pid):
    return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0]


def recover(path):
    value = json.loads(Path(path).read_text())
    if (value.get('host') != socket.gethostname() or
            ticks(value['pid']) != value['start_ticks']):
        raise RuntimeError('WATCHDOG_PROCESS_IDENTITY_CHANGED')
    os.kill(value['pid'], signal.SIGCONT)
    return {'status': 'SACRIFICIAL_RECOVERY_SENT',
            'pid': value['pid'], 'at_utc': datetime.now(timezone.utc).isoformat()}


def run(run_id):
    if (socket.gethostname() != 'k8s-worker3' or
            not run_id.startswith('SF-017-ATTEMPT-MARKERTEST-')):
        raise ValueError('WATCHDOG_REHEARSAL_SCOPE')
    folder = ROOT / run_id
    folder.mkdir(parents=True, mode=0o700, exist_ok=True)
    record = folder / 'sacrificial-watchdog.json'
    if record.exists():
        raise ValueError('WATCHDOG_REHEARSAL_ALREADY_RUN')
    child = subprocess.Popen(['/usr/bin/sleep', '25'])
    unit = 'ops-rehearsal-w3-' + run_id.lower().split('-')[-1]
    identity = {'run_id': run_id, 'host': socket.gethostname(),
                'pid': child.pid, 'start_ticks': ticks(child.pid), 'unit': unit}
    record.write_text(json.dumps(identity, sort_keys=True) + '\n')
    result = {'status': 'SACRIFICIAL_WATCHDOG_FAILED', **identity}
    try:
        subprocess.run(['systemd-run', '--unit=' + unit, '--on-active=3s',
            '--timer-property=AccuracySec=100ms',
            '--timer-property=Persistent=false',
            '--property=RuntimeMaxSec=10', '/usr/bin/python3', '-B',
            str(Path(__file__).resolve()), 'recover', '--record', str(record)],
            check=True, capture_output=True, timeout=15)
        os.kill(child.pid, signal.SIGSTOP)
        time.sleep(5)
        if ticks(child.pid) != identity['start_ticks'] or state(child.pid) in ('T', 't'):
            raise RuntimeError('WATCHDOG_DID_NOT_RESUME_EXACT_PROCESS')
        result['status'] = 'SACRIFICIAL_WATCHDOG_PASS'
    finally:
        if child.poll() is None:
            os.kill(child.pid, signal.SIGCONT)
            child.terminate()
        child.wait(timeout=8)
        for suffix in ('.timer', '.service'):
            subprocess.run(['systemctl', 'stop', unit + suffix],
                           capture_output=True, timeout=10)
        result['completed_utc'] = datetime.now(timezone.utc).isoformat()
        (folder / 'sacrificial-watchdog-result.json').write_text(
            json.dumps(result, indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('run', 'recover'))
    parser.add_argument('--run-id')
    parser.add_argument('--record', type=Path)
    args = parser.parse_args()
    result = run(args.run_id) if args.command == 'run' else recover(args.record)
    print(json.dumps(result))
    if 'FAILED' in result['status']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
