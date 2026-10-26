#!/usr/bin/env python3
"""Private bounded Phase 1 SERVICE/POD/NODE rehearsal adapter."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time


DURATION_SECONDS = 300
STATE_ROOT = Path('/root/stage5-v3-autonomous-preparation/runtime')
CASE_ID = re.compile(r'^S5V3AP-(?:SERVICE|POD|NODE)-S[12]$')
FAMILIES = {
    'SERVICE': {'process_stall'},
    'POD': {'memory_pressure'},
    'NODE': {'cpu_contention'},
}
CPU_WORKERS = {'cpu_contention': 6}
MIB = 1024 * 1024
GIB = 1024 ** 3


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command(*args):
    return subprocess.check_output(args, text=True, timeout=20)


def process_start(pid):
    fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
    return fields[19]


def task_pid(container_id):
    bare = container_id.removeprefix('containerd://')
    rows = [line.split() for line in command('ctr', '-n', 'k8s.io',
                                              'tasks', 'list').splitlines()]
    matches = [int(row[1]) for row in rows if len(row) >= 3 and
               row[0] == bare and row[2] == 'RUNNING']
    if len(matches) != 1:
        raise ValueError('Frozen container is not one running containerd task')
    return matches[0]


def cgroup_path(pid, pod_uid, container_id):
    row = Path(f'/proc/{pid}/cgroup').read_text().strip()
    if '::' not in row:
        raise ValueError('Stage5 resource adapter requires cgroup v2')
    relative = row.split('::', 1)[1]
    if (pod_uid.replace('-', '_') not in relative or
            container_id.removeprefix('containerd://') not in relative):
        raise ValueError('Target process is not in its frozen Pod UID cgroup')
    path = Path('/sys/fs/cgroup') / relative.lstrip('/')
    if not path.is_dir():
        raise ValueError('Frozen target container cgroup is absent')
    return path


def mem_available():
    line = next(row for row in Path('/proc/meminfo').read_text().splitlines()
                if row.startswith('MemAvailable:'))
    return int(line.split()[1]) * 1024


def memory_bytes(layer, cgroup=None):
    if layer == 'NODE':
        available = mem_available()
        amount = min(16 * GIB, int(available * 0.55))
        if available < 20 * GIB or available - amount < 8 * GIB:
            raise ValueError('Node memory fault lacks an eight-GiB safety reserve')
        return amount
    limit_text = (cgroup / 'memory.max').read_text().strip()
    if limit_text == 'max':
        raise ValueError('Pod memory fault needs an existing memory cgroup limit')
    limit = int(limit_text)
    current = int((cgroup / 'memory.current').read_text().strip())
    headroom = limit - current
    amount = min(int(headroom * 0.55), int(limit * 0.80) - current)
    if amount < 32 * MIB or current + amount > limit * 0.80:
        raise ValueError('Pod memory fault lacks bounded cgroup headroom')
    return amount


def quota_reduction(cgroup):
    text = (cgroup / 'cpu.max').read_text().strip()
    fields = text.split()
    if len(fields) != 2 or fields[0] == 'max':
        raise ValueError('Target Pod has no existing CPU quota')
    quota, period = map(int, fields)
    reduced = max(10_000, min(quota // 8, period // 4))
    if quota < period or reduced >= quota or not os.access(cgroup / 'cpu.max', os.W_OK):
        raise ValueError('CPU quota cannot be reduced safely in this cgroup')
    return {'old': text, 'new': f'{reduced} {period}'}


def precheck(design_freeze, preflight_freeze):
    design = json.loads(design_freeze.read_text())
    before = json.loads(preflight_freeze.read_text())
    layer, family = before.get('root_layer'), before.get('family')
    age = (datetime.now(timezone.utc) -
           datetime.fromisoformat(before['created_at_utc'])).total_seconds()
    if (design.get('status') != 'QUALIFICATION_REGISTRATION_FROZEN_PRE_FAULT' or
            before.get('status') != 'QUALIFICATION_PREFLIGHT_FROZEN_PRE_FAULT' or
            before.get('design_freeze_sha256') != sha256(design_freeze) or
            before.get('gt_read') is not False or
            before.get('faults_injected') != 0 or
            not 0 <= age <= 300 or
            family not in FAMILIES.get(layer, set()) or
            not CASE_ID.fullmatch(before.get('case_id', ''))):
        raise ValueError('Qualification resource fault lacks fresh registered preflight')
    host = socket.gethostname()
    assignments = [row for row in before.get('fault_hosts', [])
                   if row['node'] == host]
    expected = 2 if layer == 'SERVICE' else 1
    if len(assignments) != expected or len(before.get('fault_hosts', [])) != expected:
        raise ValueError('Resource fault assignments differ from registered target count')
    result = {'status': 'QUALIFICATION_RESOURCE_PRECHECK_PASS',
              'case_id': before['case_id'], 'root_layer': layer,
              'family': family, 'host': host,
              'design_freeze_sha256': sha256(design_freeze),
              'preflight_freeze_sha256': sha256(preflight_freeze),
              'target_entity': before['target_entity'],
              'duration_seconds': DURATION_SECONDS}
    if layer in ('SERVICE', 'POD'):
        targets = []
        for target in assignments:
            pid = task_pid(target['container_id'])
            cgroup = cgroup_path(pid, target['pod_uid'], target['container_id'])
            targets.append({'pod_name': target['pod_name'],
                            'pod_uid': target['pod_uid'],
                            'container_id': target['container_id'],
                            'pid': pid, 'pid_start': process_start(pid),
                            'cgroup': str(cgroup)})
        if len({target['pod_uid'] for target in targets}) != expected:
            raise ValueError('Target Pod UIDs are not distinct')
        if layer == 'SERVICE':
            result['targets'] = targets
        else:
            target = targets[0]
            result.update({'pod_name': target['pod_name'],
                           'pod_uid': target['pod_uid'],
                           'container_id': target['container_id'],
                           'container_pid': target['pid'],
                           'container_pid_start': target['pid_start'],
                           'cgroup': target['cgroup'],
                           'memory_current_before_bytes': int((Path(target['cgroup']) /
                               'memory.current').read_text().strip()),
                           'memory_limit_bytes': int((Path(target['cgroup']) /
                               'memory.max').read_text().strip()),
                           'memory_bytes': memory_bytes(layer, Path(target['cgroup']))})
    else:
        if host not in {'k8s-worker1', 'k8s-worker2', 'k8s-worker3'}:
            raise ValueError('Node fault target is not a registered Worker')
        if family == 'cpu_contention' and os.cpu_count() != 8:
            raise ValueError('Node CPU topology changed from registered eight cores')
    return result


def write_state(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2) + '\n')
    os.replace(temporary, path)


def same_process(item):
    pid = item['pid']
    try:
        return process_start(pid) == item['pid_start']
    except FileNotFoundError:
        return False


def stop_child(item):
    if not same_process(item):
        return
    os.kill(item['pid'], signal.SIGCONT)
    os.kill(item['pid'], signal.SIGTERM)
    for _ in range(10):
        if not same_process(item):
            return
        time.sleep(0.1)
    if same_process(item):
        os.kill(item['pid'], signal.SIGKILL)


def cleanup(state_file):
    if not state_file.is_file():
        raise ValueError('No private resource fault state to clean')
    state = json.loads(state_file.read_text())
    if state['host'] != socket.gethostname() or state['status'] not in (
            'ARMED', 'ACTIVE', 'CLEANED'):
        raise ValueError('Resource fault cleanup belongs to another host')
    if state['status'] == 'CLEANED':
        return {'status': 'STAGE5_RESOURCE_FAULT_CLEANUP_PASS',
                'case_id': state['case_id'], 'already_clean': True}
    for item in state.get('stopped_targets', []):
        if same_process(item):
            os.kill(item['pid'], signal.SIGCONT)
    for item in state.get('children', []):
        stop_child(item)
    if state.get('quota_written'):
        path = Path(state['cgroup']) / 'cpu.max'
        if not path.is_file():
            raise ValueError('Cannot restore CPU quota after target cgroup disappeared')
        path.write_text(state['quota']['old'])
        if path.read_text().strip() != state['quota']['old']:
            raise ValueError('CPU quota restore did not verify')
    if state.get('io_file'):
        path = Path(state['io_file'])
        if not path.is_relative_to(STATE_ROOT):
            raise ValueError('Refusing to remove an unrelated I/O file')
        path.unlink(missing_ok=True)
    state['status'] = 'CLEANED'
    state['cleanup_at_utc'] = datetime.now(timezone.utc).isoformat()
    write_state(state_file, state)
    return {'status': 'STAGE5_RESOURCE_FAULT_CLEANUP_PASS',
            'case_id': state['case_id'], 'already_clean': False}


def wait_start(start_utc):
    scheduled = datetime.fromisoformat(start_utc.replace('Z', '+00:00')).timestamp()
    wait = scheduled - time.time()
    if not -2 <= wait <= 120:
        raise ValueError('Resource fault start must be within two minutes')
    if wait > 0:
        time.sleep(wait)


def spawn(state_file, state, argv, cgroup=None, input_token=None):
    child = subprocess.Popen(argv, stdin=subprocess.PIPE if input_token else None,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
    item = {'pid': child.pid, 'pid_start': process_start(child.pid)}
    state.setdefault('children', []).append(item)
    write_state(state_file, state)
    if cgroup:
        (Path(cgroup) / 'cgroup.procs').write_text(str(child.pid))
        actual = Path(f'/proc/{child.pid}/cgroup').read_text().split('::', 1)[1].strip()
        expected = '/' + str(Path(cgroup).relative_to('/sys/fs/cgroup'))
        if actual != expected:
            raise ValueError('Resource worker did not enter the target cgroup')
    if input_token:
        child.stdin.write(input_token)
        child.stdin.flush()
        child.stdin.close()
    return child


def run_workload(state_file, state):
    family = state['family']
    if family == 'process_stall':
        state['stopped_targets'] = [
            {'pid': target['pid'], 'pid_start': target['pid_start']}
            for target in state['targets']]
        write_state(state_file, state)
        for item in state['stopped_targets']:
            if not same_process(item):
                raise ValueError('Service replica identity changed before pause')
            os.kill(item['pid'], signal.SIGSTOP)
        time.sleep(DURATION_SECONDS)
    elif family in ('single_instance_degradation',
                    'service_local_latency_error'):
        item = {'pid': state['container_pid'],
                'pid_start': state['container_pid_start']}
        state['stopped_targets'] = [item]
        write_state(state_file, state)
        for _ in range(20):
            if not same_process(item):
                raise ValueError('Single-instance target restarted during pulses')
            os.kill(item['pid'], signal.SIGSTOP)
            time.sleep(3)
            os.kill(item['pid'], signal.SIGCONT)
            time.sleep(12)
    elif family == 'cpu_quota_throttling':
        path = Path(state['cgroup']) / 'cpu.max'
        state['quota_written'] = True
        write_state(state_file, state)
        path.write_text(state['quota']['new'])
        if path.read_text().strip() != state['quota']['new']:
            raise ValueError('Pod CPU quota reduction did not verify')
        time.sleep(DURATION_SECONDS)
    elif family in CPU_WORKERS:
        workers = CPU_WORKERS[family]
        for index in range(workers):
            argv = (['taskset', '-c', str(index), 'yes'] if family ==
                    'cpu_contention' else ['yes'])
            spawn(state_file, state, argv,
                  state.get('cgroup') if family != 'cpu_contention' else None)
        time.sleep(DURATION_SECONDS)
    elif family == 'memory_pressure':
        holder = Path(__file__).with_name('resource_memory_holder.py')
        spawn(state_file, state, [sys.executable, str(holder), '--bytes',
                                 str(state['memory_bytes'])],
              state.get('cgroup'), b'1')
        time.sleep(DURATION_SECONDS)
    elif family == 'io_contention':
        deadline = time.monotonic() + DURATION_SECONDS
        while time.monotonic() < deadline:
            child = spawn(state_file, state, ['dd', 'if=/dev/zero',
                          'of=' + state['io_file'], 'bs=1M', 'count=512',
                          'oflag=direct', 'conv=fdatasync'])
            remaining = max(1, deadline - time.monotonic())
            if child.wait(timeout=remaining) != 0:
                raise ValueError('Bounded root-device I/O worker failed')
    else:
        raise ValueError('No registered Stage5 resource workload')


def stop_on_signal(signum, _frame):
    raise SystemExit(128 + signum)


def inject(design_freeze, preflight_freeze, start_utc, state_file):
    facts = precheck(design_freeze, preflight_freeze)
    if state_file.exists() or not state_file.is_relative_to(STATE_ROOT):
        raise ValueError('Resource fault state must be new and private')
    wait_start(start_utc)
    signal.signal(signal.SIGTERM, stop_on_signal)
    signal.signal(signal.SIGHUP, stop_on_signal)
    state = {**facts, 'status': 'ARMED', 'requested_start_utc': start_utc,
             'children': [], 'stopped_targets': []}
    write_state(state_file, state)
    unit = 's5v3ap-' + facts['case_id'].lower() + '-' + facts['host'] + '-cleanup'
    subprocess.run(['systemd-run', '--quiet', '--unit=' + unit,
                    '--on-active=305s', '/usr/bin/python3',
                    str(Path(__file__).resolve()), 'cleanup', '--state-file',
                    str(state_file)], check=True, timeout=20)
    state['watchdog_unit'] = unit
    state['status'] = 'ACTIVE'
    state['start_at_utc'] = datetime.now(timezone.utc).isoformat()
    write_state(state_file, state)
    try:
        print(json.dumps({'status': 'STAGE5_RESOURCE_FAULT_ACTIVE',
                          'case_id': facts['case_id'],
                          'start_at_utc': state['start_at_utc']}), flush=True)
        run_workload(state_file, state)
    finally:
        result = cleanup(state_file)
        subprocess.run(['systemctl', 'stop', unit + '.timer'], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=20)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=('precheck', 'inject', 'cleanup'))
    parser.add_argument('--design-freeze', type=Path)
    parser.add_argument('--preflight-freeze', type=Path)
    parser.add_argument('--start-utc')
    parser.add_argument('--state-file', type=Path)
    args = parser.parse_args()
    if args.operation == 'cleanup':
        if not args.state_file:
            raise ValueError('Cleanup requires a private state path')
        result = cleanup(args.state_file)
    elif args.operation == 'precheck':
        result = precheck(args.design_freeze, args.preflight_freeze)
    else:
        if not args.start_utc or not args.state_file:
            raise ValueError('Injection requires a scheduled start and state path')
        result = inject(args.design_freeze, args.preflight_freeze,
                        args.start_utc, args.state_file)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
