#!/usr/bin/env python3
"""Bounded F02 CPU contention inside one frozen Kubernetes Pod cgroup."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import signal
import subprocess
import time

from host_fault import DURATION_SECONDS, wait_start, write_state


NAMESPACE = 'otel-demo'
CONTAINER = 'ad'
CPU_WORKERS = 4


def kubectl(*args, check=True):
    return subprocess.run(['kubectl', '-n', NAMESPACE, *args],
                          check=check, capture_output=True, text=True, timeout=30)


def precheck(pod_name, pod_uid, container_id):
    pod = json.loads(kubectl('get', 'pod', pod_name, '-o', 'json').stdout)
    statuses = [status for status in pod['status']['containerStatuses']
                if status['name'] == CONTAINER]
    if (pod['metadata']['uid'] != pod_uid or pod['spec']['nodeName'] != 'k8s-worker2' or
            len(statuses) != 1 or statuses[0]['containerID'] != container_id or
            statuses[0]['ready'] is not True or statuses[0]['restartCount'] != 0):
        raise ValueError('F02 frozen Pod UID, container or Node changed')
    check = kubectl('exec', pod_name, '-c', CONTAINER, '--', 'sh', '-c',
                    'command -v yes; command -v timeout; command -v pkill; '
                    'command -v pgrep; cat /sys/fs/cgroup/cpu.max; '
                    '! pgrep -x yes >/dev/null')
    quota = check.stdout.strip().splitlines()[-1].split()
    if len(quota) != 2 or quota[0] == 'max':
        raise ValueError('F02 target Pod has no CPU cgroup quota')
    return {'status': 'F02_POD_EXEC_PRECHECK_PASS', 'host': 'k8s-worker2',
            'case_id': 'F02', 'pod_name': pod_name, 'pod_uid': pod_uid,
            'container_id': container_id, 'cpu_max': quota,
            'duration_seconds': DURATION_SECONDS}


def shell_workload():
    return ('pids=""; cleanup(){ for pid in $pids; do kill "$pid" 2>/dev/null || true; '
            'done; }; trap cleanup EXIT HUP INT TERM; '
            'for i in 1 2 3 4; do yes >/dev/null & pids="$pids $!"; done; sleep 305')


def cleanup(state_file):
    state = json.loads(state_file.read_text())
    pod = json.loads(kubectl('get', 'pod', state['pod_name'], '-o', 'json').stdout)
    if pod['metadata']['uid'] != state['pod_uid']:
        raise ValueError('Cannot clean F02 against a replacement Pod')
    result = kubectl('exec', state['pod_name'], '-c', CONTAINER, '--', 'sh', '-c',
                     'pkill -x yes 2>/dev/null || true; ! pgrep -x yes >/dev/null')
    state['status'] = 'CLEANED'
    state['cleanup_at_utc'] = datetime.now(timezone.utc).isoformat()
    state['cleanup_exit_code'] = result.returncode
    write_state(state_file, state)
    return {'status': 'F02_POD_CPU_CLEANUP_PASS', 'host': state['host'],
            'at_utc': state['cleanup_at_utc']}


def stop_on_signal(signum, _frame):
    raise SystemExit(128 + signum)


def inject(pod_name, pod_uid, container_id, start_utc, state_file):
    facts = precheck(pod_name, pod_uid, container_id)
    if state_file.exists():
        raise ValueError('Refusing to replace a prior F02 attempt')
    wait_start(start_utc)
    signal.signal(signal.SIGTERM, stop_on_signal)
    signal.signal(signal.SIGHUP, stop_on_signal)
    state = {**facts, 'status': 'ARMED', 'requested_start_utc': start_utc}
    write_state(state_file, state)
    unit = 'stage4f-f02-master-cleanup'
    subprocess.run(['systemd-run', '--quiet', '--unit=' + unit,
                    '--on-active=300s', '/usr/bin/python3', str(Path(__file__).resolve()),
                    'cleanup', '--state-file', str(state_file)], check=True, timeout=20)
    state['watchdog_unit'] = unit
    write_state(state_file, state)
    try:
        command = ['kubectl', '-n', NAMESPACE, 'exec', pod_name, '-c', CONTAINER,
                   '--', 'timeout', '-s', 'TERM', '-k', '5', str(DURATION_SECONDS),
                   'sh', '-c', shell_workload()]
        process = subprocess.Popen(command, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        state['status'] = 'ACTIVE'
        state['start_at_utc'] = datetime.now(timezone.utc).isoformat()
        write_state(state_file, state)
        print(json.dumps({'status': 'F02_POD_CPU_ACTIVE',
                          'start_at_utc': state['start_at_utc']}), flush=True)
        stdout, stderr = process.communicate(timeout=DURATION_SECONDS + 15)
        state['workload_exit_code'] = process.returncode
        state['workload_stdout'] = stdout[:1000]
        state['workload_stderr'] = stderr[:1000]
        write_state(state_file, state)
    finally:
        result = cleanup(state_file)
        subprocess.run(['systemctl', 'stop', unit + '.timer'], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=('precheck', 'inject', 'cleanup'))
    parser.add_argument('--pod-name')
    parser.add_argument('--pod-uid')
    parser.add_argument('--container-id')
    parser.add_argument('--start-utc')
    parser.add_argument('--state-file', type=Path)
    args = parser.parse_args()
    if args.operation == 'cleanup':
        if not args.state_file:
            raise ValueError('F02 cleanup requires its state file')
        result = cleanup(args.state_file)
    elif args.operation == 'precheck':
        result = precheck(args.pod_name, args.pod_uid, args.container_id)
    else:
        if not args.start_utc or not args.state_file:
            raise ValueError('F02 injection needs a scheduled start and state file')
        result = inject(args.pod_name, args.pod_uid, args.container_id,
                        args.start_utc, args.state_file)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
