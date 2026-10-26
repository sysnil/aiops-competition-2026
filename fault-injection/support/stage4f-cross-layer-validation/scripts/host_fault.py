#!/usr/bin/env python3
"""Bounded private Stage4F faults on Workers; never imported by RCA adapters."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import time


DURATION_SECONDS = 300
POD_CPU_WORKERS = 4
NODE_CPU_WORKERS = 6
PATH_DELAY_MS = 100
PATH_DESTINATION_CIDR = '10.244.126.0/24'
PATH_DESTINATION_POD_IP = '10.244.126.8'
PATH_NEXT_HOP = '192.168.53.54'
PATH_INTERFACE = 'ens160'
HOSTS = {'F01': {'k8s-worker1', 'k8s-worker2'},
         'F02': {'k8s-worker2'}, 'F03': {'k8s-worker1'},
         'F04': {'k8s-worker3'}}


def command(*args):
    return subprocess.check_output(args, text=True, timeout=20)


def hostname():
    return command('hostname').strip()


def task_pid(container_id):
    bare = container_id.removeprefix('containerd://')
    rows = [line.split() for line in command('ctr', '-n', 'k8s.io', 'tasks', 'list').splitlines()]
    matches = [int(row[1]) for row in rows if len(row) >= 3 and row[0] == bare and
               row[2] == 'RUNNING']
    if len(matches) != 1:
        raise ValueError('Frozen container is not one running containerd task')
    return matches[0]


def process_start(pid):
    fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
    return fields[19]


def cgroup_path(pid, pod_uid, container_id):
    row = Path(f'/proc/{pid}/cgroup').read_text().strip()
    path = row.split('::', 1)[1]
    if (pod_uid.replace('-', '_') not in path or
            container_id.removeprefix('containerd://') not in path):
        raise ValueError('Container process is not in the frozen Pod UID cgroup')
    return Path('/sys/fs/cgroup') / path.lstrip('/')


def path_qdisc_commands(interface=PATH_INTERFACE, destination_cidr=PATH_DESTINATION_CIDR):
    return [
        ['tc', 'qdisc', 'replace', 'dev', interface, 'root', 'handle', '1:',
         'prio', 'bands', '3', 'priomap', *(['0'] * 16)],
        ['tc', 'qdisc', 'add', 'dev', interface, 'parent', '1:3',
         'handle', '30:', 'netem', 'delay', str(PATH_DELAY_MS) + 'ms'],
        ['tc', 'filter', 'add', 'dev', interface, 'protocol', 'ip',
         'parent', '1:0', 'prio', '1', 'u32', 'match', 'ip', 'dst',
         destination_cidr, 'flowid', '1:3'],
    ]


def precheck(case_id, pod_uid=None, container_id=None):
    host = hostname()
    if host not in HOSTS[case_id]:
        raise ValueError('Fault case cannot run on this host')
    result = {'status': 'HOST_FAULT_PRECHECK_PASS', 'case_id': case_id,
              'host': host, 'duration_seconds': DURATION_SECONDS}
    if case_id in ('F01', 'F02'):
        if not pod_uid or not container_id:
            raise ValueError('Pod UID and container ID are mandatory')
        pid = task_pid(container_id)
        group = cgroup_path(pid, pod_uid, container_id)
        if not group.is_dir() or not os.path.exists(f'/proc/{pid}'):
            raise ValueError('Frozen Pod cgroup is absent')
        result.update({'pod_uid': pod_uid, 'container_id': container_id,
                       'pid': pid, 'pid_start': process_start(pid),
                       'cgroup': str(group)})
        if case_id == 'F02':
            quota = (group / 'cpu.max').read_text().strip().split()
            if len(quota) != 2 or quota[0] == 'max' or not os.access(group / 'cgroup.procs', os.W_OK):
                raise ValueError('F02 target lacks a writable CPU-limited cgroup')
            result['cpu_max'] = quota
    if case_id == 'F03':
        if os.cpu_count() != 8 or not Path('/usr/bin/yes').is_file():
            raise ValueError('F03 Worker1 CPU topology is not the registered eight cores')
        result['cpu_workers'] = NODE_CPU_WORKERS
    if case_id == 'F04':
        route = json.loads(command('ip', '-j', 'route', 'get', PATH_DESTINATION_POD_IP))[0]
        if route.get('gateway') != PATH_NEXT_HOP or route.get('dev') != PATH_INTERFACE:
            raise ValueError('F04 route no longer uses the frozen Worker3-to-Worker2 path')
        qdiscs = json.loads(command('tc', '-j', 'qdisc', 'show', 'dev', PATH_INTERFACE))
        roots = [row for row in qdiscs if row.get('root') is True]
        if len(roots) != 1 or roots[0].get('kind') != 'mq' or (
                any(row.get('kind') == 'netem' for row in qdiscs)):
            raise ValueError('F04 source interface is not at its clean mq qdisc')
        result.update({'destination_cidr': PATH_DESTINATION_CIDR,
                       'destination_pod_ip': PATH_DESTINATION_POD_IP,
                       'next_hop': PATH_NEXT_HOP, 'interface': PATH_INTERFACE,
                       'delay_ms': PATH_DELAY_MS})
    return result


def write_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, indent=2) + '\n')
    os.replace(temporary, path)


def cleanup(state_file):
    if not state_file.is_file():
        return {'status': 'NO_STATE_TO_CLEAN'}
    state = json.loads(state_file.read_text())
    case = state['case_id']
    if case == 'F01':
        pid = state['pid']
        if Path(f'/proc/{pid}').is_dir() and process_start(pid) == state['pid_start']:
            os.kill(pid, signal.SIGCONT)
    elif case in ('F02', 'F03'):
        for item in state.get('workers', []):
            pid = item['pid']
            if Path(f'/proc/{pid}').is_dir() and process_start(pid) == item['pid_start']:
                os.kill(pid, signal.SIGTERM)
    else:
        qdiscs = json.loads(command('tc', '-j', 'qdisc', 'show', 'dev', PATH_INTERFACE))
        if any(row.get('kind') == 'prio' and row.get('root') is True and
               row.get('handle') == '1:' for row in qdiscs):
            subprocess.run(['tc', 'qdisc', 'del', 'dev', PATH_INTERFACE, 'root'],
                           check=True, timeout=20)
        restored = json.loads(command('tc', '-j', 'qdisc', 'show', 'dev', PATH_INTERFACE))
        if not any(row.get('kind') == 'mq' and row.get('root') is True
                   for row in restored):
            raise ValueError('F04 source qdisc did not recover to mq')
    state['status'] = 'CLEANED'
    state['cleanup_at_utc'] = datetime.now(timezone.utc).isoformat()
    write_state(state_file, state)
    return {'status': 'HOST_FAULT_CLEANUP_PASS', 'case_id': case,
            'host': state['host'], 'at_utc': state['cleanup_at_utc']}


def start_watchdog(state_file, case_id, host):
    unit = 'stage4f-' + case_id.lower() + '-' + host + '-cleanup'
    subprocess.run(['systemd-run', '--quiet', '--unit=' + unit,
                    '--on-active=300s', '/usr/bin/python3', str(Path(__file__).resolve()),
                    'cleanup', '--state-file', str(state_file)], check=True, timeout=20)
    return unit


def stop_watchdog(unit):
    subprocess.run(['systemctl', 'stop', unit + '.timer'],
                   check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)


def wait_start(start_utc):
    start = datetime.fromisoformat(start_utc.replace('Z', '+00:00')).timestamp()
    wait = start - time.time()
    if not -2 <= wait <= 120:
        raise ValueError('Fault start must be predeclared within 120 seconds')
    if wait > 0:
        time.sleep(wait)


def stop_on_signal(signum, _frame):
    raise SystemExit(128 + signum)


def inject(case_id, start_utc, state_file, pod_uid=None, container_id=None):
    if case_id == 'F02':
        raise ValueError('F02 must use the frozen in-Pod pod_cpu_exec.py adapter')
    facts = precheck(case_id, pod_uid, container_id)
    if state_file.exists():
        raise ValueError('Refusing to replace a prior fault attempt')
    wait_start(start_utc)
    signal.signal(signal.SIGTERM, stop_on_signal)
    signal.signal(signal.SIGHUP, stop_on_signal)
    state = {**facts, 'status': 'ARMED', 'workers': [],
             'requested_start_utc': start_utc}
    write_state(state_file, state)
    unit = start_watchdog(state_file, case_id, facts['host'])
    state['watchdog_unit'] = unit
    write_state(state_file, state)
    try:
        if case_id == 'F01':
            os.kill(facts['pid'], signal.SIGSTOP)
        elif case_id in ('F02', 'F03'):
            for index in range(POD_CPU_WORKERS if case_id == 'F02' else NODE_CPU_WORKERS):
                command_line = (['yes'] if case_id == 'F02' else
                                ['taskset', '-c', str(index), 'yes'])
                child = subprocess.Popen(command_line, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL, start_new_session=True)
                item = {'pid': child.pid, 'pid_start': process_start(child.pid)}
                state['workers'].append(item)
                write_state(state_file, state)
                if case_id == 'F02':
                    (Path(facts['cgroup']) / 'cgroup.procs').write_text(str(child.pid))
                    if Path(f'/proc/{child.pid}/cgroup').read_text().split('::', 1)[1].strip() != (
                            '/' + str(Path(facts['cgroup']).relative_to('/sys/fs/cgroup'))):
                        raise ValueError('F02 CPU worker did not enter target cgroup')
        else:
            for tc_command in path_qdisc_commands():
                subprocess.run(tc_command, check=True, timeout=20)
        state['status'] = 'ACTIVE'
        state['start_at_utc'] = datetime.now(timezone.utc).isoformat()
        write_state(state_file, state)
        print(json.dumps({'status': 'HOST_FAULT_ACTIVE', 'case_id': case_id,
                          'host': facts['host'], 'start_at_utc': state['start_at_utc']}), flush=True)
        time.sleep(DURATION_SECONDS)
    finally:
        result = cleanup(state_file)
        stop_watchdog(unit)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=('precheck', 'inject', 'cleanup'))
    parser.add_argument('--case-id', choices=tuple(HOSTS))
    parser.add_argument('--pod-uid')
    parser.add_argument('--container-id')
    parser.add_argument('--start-utc')
    parser.add_argument('--state-file', type=Path)
    args = parser.parse_args()
    if args.operation == 'cleanup':
        if not args.state_file:
            raise ValueError('Cleanup needs its frozen attempt state file')
        result = cleanup(args.state_file)
    elif args.operation == 'precheck':
        result = precheck(args.case_id, args.pod_uid, args.container_id)
    else:
        if not args.start_utc or not args.state_file:
            raise ValueError('Fault injection needs a scheduled start and state file')
        result = inject(args.case_id, args.start_utc, args.state_file,
                        args.pod_uid, args.container_id)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
