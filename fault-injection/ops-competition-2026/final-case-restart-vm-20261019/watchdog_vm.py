#!/usr/bin/env python3
"""Exercise an independent systemd recovery Timer on a disposable process."""

import argparse
import json
import os
import signal
import subprocess
import time
import uuid
from pathlib import Path


def call(*args):
    return subprocess.run(args, check=True, capture_output=True,
                          text=True, timeout=15).stdout.strip()


def state(pid):
    return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    token = uuid.uuid4().hex[:8]
    target = 'ops-sfvm-sacrifice-' + token
    watchdog = 'ops-sfvm-watchdog-' + token
    result = {'target_unit': target, 'watchdog_unit': watchdog,
              'status': 'WATCHDOG_VM_FAILED'}
    try:
        call('sudo', 'systemd-run', '--unit=' + target,
             '--property=RuntimeMaxSec=90', '/usr/bin/sleep', '90')
        pid = int(call('sudo', 'systemctl', 'show', target + '.service',
                       '-p', 'MainPID', '--value'))
        if pid <= 0:
            raise RuntimeError('SACRIFICIAL_PID_MISSING')
        ticks = state(pid)[19]
        call('sudo', 'systemd-run', '--unit=' + watchdog,
             '--on-active=3s', '--timer-property=AccuracySec=100ms',
             '--property=RuntimeMaxSec=10', '/usr/bin/kill', '-CONT', str(pid))
        os.kill(pid, signal.SIGSTOP)
        for _ in range(20):
            if state(pid)[0] in ('T', 't'):
                break
            time.sleep(.02)
        else:
            raise RuntimeError('SACRIFICIAL_PROCESS_NOT_STOPPED')
        time.sleep(5)
        after = state(pid)
        if after[19] != ticks or after[0] in ('T', 't'):
            raise RuntimeError('INDEPENDENT_WATCHDOG_DID_NOT_RESUME_EXACT_PID')
        result.update(status='WATCHDOG_VM_RESUME_PASS', pid=pid,
                      start_ticks=ticks, state_after=after[0])
    finally:
        for unit in (watchdog + '.timer', watchdog + '.service',
                     target + '.service'):
            subprocess.run(['sudo', 'systemctl', 'stop', unit],
                           capture_output=True, timeout=10)
    if args.output is not None:
        with args.output.open('x') as stream:
            json.dump(result, stream, indent=2, sort_keys=True)
            stream.write('\n')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
