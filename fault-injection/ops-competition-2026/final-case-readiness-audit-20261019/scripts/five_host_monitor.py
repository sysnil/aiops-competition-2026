#!/usr/bin/env python3
"""Independent read-only five-host disk gate for the no-fault run."""

import argparse
import hashlib
import json
import subprocess
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path


OPS = Path('/Users/liuzihan/Documents/ChatGPT/otel/ops-competition-2026')
sys.path.insert(0, str(OPS / 'sf003-to-sf012-production-20261015/scripts'))
sys.path.insert(0, str(OPS / 'low-stable-v4-20261017/storage-v2'))
from dynamic_storage import HOSTS, forecast_remaining, remote
from storage_forecast_gate import StorageForecastGate


SOCKETS = {
    'master': '/tmp/sf-ready-master.sock', 'worker1': '/tmp/sf-ready-w1.sock',
    'worker2': '/tmp/sf-ready-w2.sock', 'worker3': '/tmp/sf-ready-w3.sock',
    'vm1': '/tmp/sf-ready-vm1.sock'}
for host, (address, _, data) in list(HOSTS.items()):
    HOSTS[host] = (address, SOCKETS[host], data)

MASTER = ['ssh', '-S', SOCKETS['master'], '-o', 'BatchMode=yes',
          'root@192.168.53.52']
CAPTURE = 'ops-sf013-readiness-capture-20261019-01.service'
TERMINAL = ('/root/ops-competition-2026/final-case-production-readiness-20261019/'
            'results/no-fault-1800-01/joint-terminal.json')
COMMITMENT = {
    host: {'root_free_bytes': 64 * 1024**2,
           **({'data_free_bytes': 7_350_324_113 if host == 'master' else
                                    2_000_000_000} if HOSTS[host][2] else {})}
    for host in HOSTS}


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def terminal_exists():
    result = subprocess.run(MASTER + ['test', '-e', TERMINAL],
                            capture_output=True, timeout=8)
    return result.returncode == 0


def stop_capture(reason):
    result = subprocess.run(MASTER + ['systemctl', 'stop', CAPTURE],
                            capture_output=True, text=True, timeout=25)
    if result.returncode:
        raise RuntimeError('Unable to stop no-fault capture: ' + reason +
                           ' ' + result.stderr[-400:])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--terminal', type=Path, required=True)
    parser.add_argument('--max-seconds', type=int, default=2400)
    args = parser.parse_args()
    if args.output.exists() or args.terminal.exists():
        raise RuntimeError('No overwrite of five-host monitor evidence')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    history = deque(maxlen=10)
    gate = StorageForecastGate()
    result = {'status': 'FIVE_HOST_NOFAULT_MONITOR_FAILED', 'samples': 0,
              'at_utc': now(), 'commitment': COMMITMENT,
              'source_sha256': sha(__file__)}
    try:
        with args.output.open('x') as log:
            while time.monotonic() - started < args.max_seconds:
                at = time.monotonic()
                snapshot = {host: remote(host) for host in HOSTS}
                mature = [entry for entry in history if at - entry[0] >= 60]
                prior = mature[-1] if mature else None
                forecast = (COMMITMENT if prior is None else
                    forecast_remaining(prior[1], snapshot, at - prior[0],
                        max(0, args.max_seconds - (at - started)), COMMITMENT))
                decision = gate.evaluate(snapshot, forecast)
                row = {'at_utc': now(), 'snapshot': snapshot,
                       'forecast': forecast, 'decision': decision}
                log.write(json.dumps(row) + '\n')
                log.flush()
                result['samples'] += 1
                history.append((at, snapshot))
                if decision['status'] != 'DYNAMIC_STORAGE_PASS':
                    stop_capture(decision.get('reason', 'storage gate'))
                    raise RuntimeError('Five-host storage gate stopped no-fault capture')
                if terminal_exists():
                    result['status'] = 'FIVE_HOST_NOFAULT_MONITOR_COMPLETE'
                    break
                time.sleep(15)
            else:
                raise RuntimeError('Five-host monitor exceeded its bound')
    except BaseException as exc:
        result['error'] = type(exc).__name__ + ': ' + str(exc)
    finally:
        result['completed_utc'] = now()
        result['log_sha256'] = sha(args.output) if args.output.is_file() else None
        with args.terminal.open('x') as stream:
            json.dump(result, stream, indent=2)
            stream.write('\n')
        print(json.dumps({'status': result['status'],
                          'samples': result['samples'],
                          'error': result.get('error')}), flush=True)
    if result['status'] != 'FIVE_HOST_NOFAULT_MONITOR_COMPLETE':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
