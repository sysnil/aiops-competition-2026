#!/usr/bin/env python3
"""Five-host storage guard for the isolated request-delay qualification."""

import argparse
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from dynamic_storage import HOSTS, decide, forecast_remaining, remote


UNIT = 'ops-sf30-reqdelay-qual-20261015.service'
ALLOWED_UNITS = (UNIT, 'ops-sf30-reqdelay-medium-20261015.service')
MASTER = ['ssh', '-S', '/tmp/sfgen-master.sock', '-o', 'BatchMode=yes',
          'root@192.168.53.52']


def now():
    return datetime.now(timezone.utc).isoformat()


def unit_active():
    result = subprocess.run(MASTER + ['systemctl', 'is-active', UNIT],
                            capture_output=True, text=True, timeout=10)
    return result.returncode == 0 and result.stdout.strip() == 'active'


def stop_qualification():
    result = subprocess.run(MASTER + ['systemctl', 'stop', UNIT],
                            capture_output=True, text=True, timeout=20)
    return {'exit_code': result.returncode, 'stderr': result.stderr[-500:]}


def main():
    global UNIT
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--max-seconds', type=int, default=480)
    parser.add_argument('--interval-seconds', type=int, default=15)
    parser.add_argument('--unit', choices=ALLOWED_UNITS, default=UNIT)
    args = parser.parse_args()
    UNIT = args.unit
    if args.output.exists() or args.max_seconds <= 0 or args.interval_seconds <= 0:
        raise ValueError('New output and positive bounded intervals required')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    began = time.monotonic()
    previous, previous_at = None, None
    status = 'STORAGE_MONITOR_PASS'
    with args.output.open('x') as log:
        while time.monotonic() - began <= args.max_seconds:
            sampled_at = time.monotonic()
            try:
                active = unit_active()
                snapshot = {host: remote(host) for host in HOSTS}
                if previous is None:
                    forecast = {host: {'root_free_bytes': 64 * 1024**2, **({
                        'data_free_bytes': 64 * 1024**2} if HOSTS[host][2] else {})}
                        for host in HOSTS}
                else:
                    forecast = forecast_remaining(previous, snapshot,
                        sampled_at - previous_at,
                        max(0, args.max_seconds - (sampled_at - began)),
                        {host: {'root_free_bytes': 64 * 1024**2, **({
                            'data_free_bytes': 64 * 1024**2} if HOSTS[host][2] else {})}
                            for host in HOSTS})
                decision = decide(snapshot, forecast, fault_active=active,
                                  cleanup_armed=True)
                row = {'at_utc': now(), 'active': active, 'snapshot': snapshot,
                       'forecast': forecast, 'decision': decision}
                previous, previous_at = snapshot, sampled_at
                if decision['status'] != 'DYNAMIC_STORAGE_PASS':
                    status = 'STORAGE_MONITOR_ABORTED_QUALIFICATION'
                    row['stop'] = stop_qualification() if active else None
                    log.write(json.dumps(row) + '\n')
                    log.flush()
                    break
                log.write(json.dumps(row) + '\n')
                log.flush()
                if not active:
                    break
            except Exception as exc:
                status = 'STORAGE_MONITOR_FAILED_CLOSED'
                row = {'at_utc': now(), 'error': type(exc).__name__ + ': ' + str(exc),
                       'stop': stop_qualification()}
                log.write(json.dumps(row) + '\n')
                log.flush()
                break
            time.sleep(args.interval_seconds)
        else:
            status = 'STORAGE_MONITOR_TIMEOUT'
            stop_qualification()
    print(json.dumps({'status': status, 'output': str(args.output)}))
    if status != 'STORAGE_MONITOR_PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
