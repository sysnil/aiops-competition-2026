"""Bounded SERVICE/POD rehearsal with independent process and quota recovery."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time

import yaml


STATE_ROOT = Path('/root/cross-layer-workload-generation/runtime')
SCRIPT = Path(__file__).resolve()


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2) + '\n')
    os.replace(temp, path)


def process_start(pid):
    return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]


def task_pid(container_id):
    bare = container_id.removeprefix('containerd://')
    text = subprocess.check_output(['ctr', '-n', 'k8s.io', 'tasks', 'list'],
                                   text=True, timeout=15)
    matches = [int(row[1]) for line in text.splitlines()
               if len(row := line.split()) >= 3 and row[0] == bare and row[2] == 'RUNNING']
    if len(matches) != 1:
        raise ValueError('Target container is not one running containerd task')
    return matches[0]


def cgroup_path(pid, pod_uid, container_id):
    line = Path(f'/proc/{pid}/cgroup').read_text().strip()
    if '::' not in line:
        raise ValueError('Target is not in cgroup v2')
    relative = line.split('::', 1)[1]
    if pod_uid.replace('-', '_') not in relative or (
            container_id.removeprefix('containerd://') not in relative):
        raise ValueError('Target UID/container differs from current cgroup')
    path = Path('/sys/fs/cgroup') / relative.lstrip('/')
    if not path.is_dir():
        raise ValueError('Target cgroup is absent')
    return path


def precheck(plan_path, preflight_path, experiment_id):
    plan = yaml.safe_load(plan_path.read_text())
    preflight = json.loads(preflight_path.read_text())
    rows = [item for item in plan['service_pod_experiments']
            if item['id'] == experiment_id]
    if (len(rows) != 1 or preflight.get('status') != 'RH_PREFLIGHT_READY' or
            preflight.get('experiment_id') != experiment_id or
            preflight.get('plan_sha256') != sha256(plan_path) or
            preflight.get('faults_injected') != 0 or
            datetime.now(timezone.utc).timestamp() -
            datetime.fromisoformat(preflight['created_at_utc']).timestamp() > 300):
        raise ValueError('Rehearsal preflight is missing, stale, or mismatched')
    spec = rows[0]
    host = socket.gethostname()
    targets = [row for row in preflight['targets'] if row['node'] == host]
    if len(targets) != 1 or targets[0]['service'] != spec['target_service']:
        raise ValueError('Exactly one current service Pod must match this host')
    target = targets[0]
    pid = task_pid(target['container_id'])
    cgroup = cgroup_path(pid, target['uid'], target['container_id'])
    facts = {'experiment_id': experiment_id, 'host': host, 'pod': target['name'],
             'uid': target['uid'], 'container_id': target['container_id'],
             'pid': pid, 'pid_start': process_start(pid), 'cgroup': str(cgroup),
             'kind': spec['mechanism'], 'duration_seconds': spec['fault_seconds'],
             'preflight_sha256': sha256(preflight_path), 'plan_sha256': sha256(plan_path)}
    if spec['mechanism'] == 'reversible_cgroup_cpu_quota_reduction':
        original = (cgroup / 'cpu.max').read_text().strip()
        quota, period = map(int, original.split())
        reduced = max(10000, round(quota * spec['quota_fraction_of_original']))
        if quota < period or reduced >= quota or not os.access(cgroup / 'cpu.max', os.W_OK):
            raise ValueError('Current CPU quota is not safely reducible')
        facts['quota'] = {'old': original, 'new': f'{reduced} {period}'}
    elif spec['mechanism'] == 'bounded_sigstop_pulses':
        if not 1 <= spec['stop_seconds'] < spec['period_seconds'] <= 30:
            raise ValueError('Pulse timing is not bounded')
        facts['stop_seconds'] = spec['stop_seconds']
        facts['period_seconds'] = spec['period_seconds']
    else:
        raise ValueError('Unsupported rehearsal mechanism')
    return facts


def same_process(state):
    try:
        return process_start(state['pid']) == state['pid_start']
    except FileNotFoundError:
        return False


def unfreeze(state_file):
    state = json.loads(state_file.read_text())
    if state['host'] != socket.gethostname():
        raise ValueError('Unfreeze belongs to another host')
    if same_process(state):
        os.kill(state['pid'], signal.SIGCONT)
    return {'status': 'RH_TARGET_CONTINUED', 'experiment_id': state['experiment_id']}


def cleanup(state_file):
    if not state_file.is_relative_to(STATE_ROOT) or not state_file.is_file():
        raise ValueError('Cleanup state is outside isolated rehearsal root')
    state = json.loads(state_file.read_text())
    if state['host'] != socket.gethostname():
        raise ValueError('Cleanup belongs to another host')
    unfreeze(state_file)
    if state.get('quota_written'):
        path = Path(state['cgroup']) / 'cpu.max'
        if not path.is_file():
            raise ValueError('Cannot restore CPU quota; target cgroup disappeared')
        path.write_text(state['quota']['old'])
        if path.read_text().strip() != state['quota']['old']:
            raise ValueError('CPU quota restore did not verify')
    state['status'] = 'CLEANED'
    state['cleanup_at_utc'] = datetime.now(timezone.utc).isoformat()
    write_json(state_file, state)
    return {'status': 'RH_RESOURCE_CLEANUP_PASS', 'experiment_id': state['experiment_id'],
            'host': state['host'], 'same_process': same_process(state)}


def schedule_cleanup(state_file, seconds, suffix):
    unit = 'wg-' + state_file.stem.lower() + '-' + socket.gethostname() + '-' + suffix
    subprocess.run(['systemd-run', '--quiet', '--unit=' + unit,
                    '--on-active=' + str(seconds) + 's', '/usr/bin/python3',
                    str(SCRIPT), 'cleanup' if suffix == 'cleanup' else 'unfreeze',
                    '--state-file', str(state_file)], check=True, timeout=15)
    return unit


def stop_timer(unit):
    subprocess.run(['systemctl', 'stop', unit + '.timer'], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)


def run(plan_path, preflight_path, experiment_id, start_utc, state_file):
    facts = precheck(plan_path, preflight_path, experiment_id)
    if state_file != STATE_ROOT / (experiment_id + '-' + facts['host'] + '.json') or state_file.exists():
        raise ValueError('State path is not new and host-specific')
    scheduled = datetime.fromisoformat(start_utc.replace('Z', '+00:00')).timestamp()
    wait = scheduled - time.time()
    if not -2 <= wait <= 120:
        raise ValueError('Start is not within two minutes')
    if wait > 0:
        time.sleep(wait)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(SystemExit(143)))
    signal.signal(signal.SIGHUP, lambda *_: (_ for _ in ()).throw(SystemExit(129)))
    state = {**facts, 'status': 'ARMED', 'requested_start_utc': start_utc,
             'quota_written': False}
    write_json(state_file, state)
    watchdog = schedule_cleanup(state_file, facts['duration_seconds'] + 20, 'cleanup')
    state['watchdog_unit'] = watchdog
    state['status'] = 'ACTIVE'
    state['start_at_utc'] = datetime.now(timezone.utc).isoformat()
    write_json(state_file, state)
    print(json.dumps({'status': 'RH_RESOURCE_ACTIVE', 'experiment_id': experiment_id,
                      'host': facts['host'], 'start_at_utc': state['start_at_utc']}), flush=True)
    try:
        if facts['kind'] == 'reversible_cgroup_cpu_quota_reduction':
            path = Path(facts['cgroup']) / 'cpu.max'
            state['quota_written'] = True
            write_json(state_file, state)
            path.write_text(facts['quota']['new'])
            if path.read_text().strip() != facts['quota']['new']:
                raise ValueError('Reduced quota did not verify')
            time.sleep(facts['duration_seconds'])
        else:
            deadline = time.monotonic() + facts['duration_seconds']
            cycle = 0
            while time.monotonic() < deadline:
                if not same_process(state):
                    raise ValueError('Target container restarted during pulse rehearsal')
                timer = schedule_cleanup(state_file, facts['stop_seconds'] + 2,
                                         'pulse' + str(cycle))
                try:
                    os.kill(state['pid'], signal.SIGSTOP)
                    time.sleep(facts['stop_seconds'])
                finally:
                    unfreeze(state_file)
                    stop_timer(timer)
                cycle += 1
                time.sleep(min(facts['period_seconds'] - facts['stop_seconds'],
                               max(0, deadline - time.monotonic())))
    finally:
        result = cleanup(state_file)
        stop_timer(watchdog)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=('precheck', 'run', 'cleanup', 'unfreeze'))
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--preflight', type=Path)
    parser.add_argument('--experiment-id')
    parser.add_argument('--start-utc')
    parser.add_argument('--state-file', type=Path)
    args = parser.parse_args()
    if args.operation == 'precheck':
        result = precheck(args.plan, args.preflight, args.experiment_id)
    elif args.operation == 'run':
        result = run(args.plan, args.preflight, args.experiment_id,
                     args.start_utc, args.state_file)
    elif args.operation == 'cleanup':
        result = cleanup(args.state_file)
    else:
        result = unfreeze(args.state_file)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
