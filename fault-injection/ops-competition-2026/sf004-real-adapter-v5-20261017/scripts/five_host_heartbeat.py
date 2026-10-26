#!/usr/bin/env python3
"""Mac-to-Master five-host disk heartbeat for a future SF-004 formal attempt."""

import argparse
from collections import deque
import hashlib
import json
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


OPS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(OPS / 'sf003-to-sf012-production-20261015/scripts'))
from dynamic_storage import HOSTS, decide, forecast_remaining, remote


RUN_ID = 'SF-004-ATTEMPT-04'
MASTER = ['ssh', '-S', '/tmp/sfgen-master.sock', '-o', 'BatchMode=yes',
          'root@192.168.53.52']
HEARTBEAT = '/data/stage5-v3/ops-competition-2026/runs/SF-004-ATTEMPT-04/control/five-host-storage.json'
FORMAL_UNIT = 'ops-sf004-formal-controller04-20261017.service'
CAPTURE_UNIT = 'ops-sf004-formal-capture04-20261017.service'
FAULT_TERMINAL = '/data/stage5-v3/ops-competition-2026/runs/SF-004-ATTEMPT-04/control/fault-controller-terminal.json'
CAPTURE_TERMINAL = '/data/stage5-v3/ops-competition-2026/runs/SF-004-ATTEMPT-04/terminal.json'
FORECAST_CASE = 7_350_324_113


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def commitments():
    return {host: {'root_free_bytes': 64 * 1024**2, **({
        'data_free_bytes': FORECAST_CASE if host in ('master','worker2','worker3') else 0}
        if HOSTS[host][2] else {})} for host in HOSTS}


def active_competition_timers():
    inventory = {}
    for host, (address, socket, _) in HOSTS.items():
        result = subprocess.run(['ssh', '-S', socket, '-o', 'BatchMode=yes',
            'root@' + address, shlex.join(['systemctl', 'list-units',
            '--type=timer', '--state=active', '--plain', '--no-legend',
            '--no-pager', 'ops-sf*'])], capture_output=True, text=True,
            check=True, timeout=15)
        inventory[host] = [line.split()[0] for line in result.stdout.splitlines()
                           if line.strip() and line.split()[0].startswith('ops-sf')]
    return inventory


def publish(payload):
    code = '''import os,pathlib,sys
p=pathlib.Path(%r)
p.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
t=p.with_suffix('.tmp')
with t.open('w') as f:f.write(sys.stdin.read());f.flush();os.fsync(f.fileno())
t.chmod(0o600);os.replace(t,p)
''' % HEARTBEAT
    subprocess.run(MASTER + [shlex.join(['python3', '-c', code])],
                   input=json.dumps(payload) + '\n',
                   capture_output=True, text=True, check=True, timeout=15)


def unit_active(unit):
    result = subprocess.run(MASTER + ['systemctl', 'is-active', unit],
                            capture_output=True, text=True, timeout=10)
    return result.returncode == 0 and result.stdout.strip() == 'active'


def terminal_status(path):
    code = ('import json,pathlib; p=pathlib.Path(' + repr(path) +
            '); print(json.dumps(json.loads(p.read_text()).get("status") if p.is_file() else None))')
    result = subprocess.run(MASTER + [shlex.join(['python3', '-c', code])],
                            capture_output=True, text=True, check=True, timeout=10)
    return json.loads(result.stdout)


def startup_grace_allowed(checked_at, registered_t0, terminal):
    return terminal is None and checked_at < registered_t0 - 100


def stop_controller():
    result = subprocess.run(MASTER + ['systemctl', 'stop', FORMAL_UNIT],
                            capture_output=True, text=True, timeout=30)
    deadline = time.monotonic() + 15
    while unit_active(CAPTURE_UNIT) and time.monotonic() < deadline:
        if terminal_status(CAPTURE_TERMINAL) is not None:
            break
        time.sleep(1)
    capture = subprocess.run(MASTER + ['systemctl', 'stop', CAPTURE_UNIT],
                             capture_output=True, text=True, timeout=30)
    return {'fault_controller_exit_code': result.returncode,
            'capture_stop_exit_code': capture.returncode,
            'stderr': (result.stderr + capture.stderr)[-500:]}


def sample(history, started, max_seconds):
    at = time.monotonic()
    snapshot = {host: remote(host) for host in HOSTS}
    obligations = commitments()
    mature = [entry for entry in history if at - entry[0] >= 60]
    reference = mature[-1] if mature else None
    forecast = obligations if reference is None else forecast_remaining(
        reference[1], snapshot, at - reference[0],
        max(0, max_seconds - (at - started)), obligations)
    decision = decide(snapshot, forecast, fault_active=False)
    return at, snapshot, forecast, decision


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--plan-sha256', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--max-seconds', type=int, default=2500)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    if (args.output.exists() or sha(args.plan) != args.plan_sha256 or
            plan['id'] != RUN_ID or
            not 1800 <= args.max_seconds <= 2800):
        raise RuntimeError('Only a new, hash-bound SF-004 monitor run is permitted')
    registered_t0 = datetime.fromisoformat(plan['start_utc']).timestamp()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    started, history = time.monotonic(), deque(maxlen=12)
    with args.output.open('x') as log:
        while time.monotonic() - started <= args.max_seconds:
            try:
                at, snapshot, forecast, decision = sample(
                    history, started, args.max_seconds)
                timers = active_competition_timers()
                if any(timers.values()):
                    raise RuntimeError('Unregistered competition Worker Timer active')
                payload = {'run_id': RUN_ID, 'plan_sha256': args.plan_sha256,
                           'at_utc': now(), 'status': decision['status'],
                           'snapshot': snapshot, 'forecast': forecast,
                           'floor_bytes': 5_000_000_000,
                           'source_sha256': sha(__file__),
                           'active_competition_timers': timers}
                publish(payload)
                log.write(json.dumps(payload) + '\n')
                log.flush()
                history.append((at, snapshot))
                if decision['status'] != 'DYNAMIC_STORAGE_PASS':
                    stop_controller()
                    raise RuntimeError('Five-host storage forecast stopped the formal controller')
                if not unit_active(FORMAL_UNIT):
                    status = terminal_status(FAULT_TERMINAL)
                    if status != 'SF004_FAULT_SEQUENCE_COMPLETE_PENDING_CAPTURE':
                        if startup_grace_allowed(time.time(), registered_t0, status):
                            time.sleep(15)
                            continue
                        raise RuntimeError('Formal fault controller exited without success')
                if not unit_active(CAPTURE_UNIT):
                    status = terminal_status(CAPTURE_TERMINAL)
                    if status == 'SF004_FORMAL_CAPTURE_PASS_PENDING_EXPORT':
                        break
                    if startup_grace_allowed(time.time(), registered_t0, status):
                        time.sleep(15)
                        continue
                    raise RuntimeError('Formal capture exited without a complete case')
            except Exception as exc:
                failure = {'run_id': RUN_ID, 'plan_sha256': args.plan_sha256,
                           'at_utc': now(), 'status': 'FIVE_HOST_STORAGE_HEARTBEAT_FAILED',
                           'error': type(exc).__name__ + ': ' + str(exc)}
                try:
                    publish(failure)
                    failure['controller_stop'] = stop_controller()
                except Exception as cleanup_error:
                    failure['stop_error'] = str(cleanup_error)
                log.write(json.dumps(failure) + '\n')
                log.flush()
                raise
            time.sleep(15)
        else:
            stop_controller()
            raise RuntimeError('Storage monitor exceeded registered maximum duration')


if __name__ == '__main__':
    main()
