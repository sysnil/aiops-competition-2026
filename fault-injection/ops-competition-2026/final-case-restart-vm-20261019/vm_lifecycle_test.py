#!/usr/bin/env python3
"""Run real-VM, harmless Timer tests against the copied authorization core."""

import argparse
import hashlib
import json
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


LIMA = '/Users/liuzihan/.local/opt/lima-2.2.1/bin/limactl'
MASTER = 'sf-final-safety'
WORKER = 'sf-final-worker'
ROOT = Path('/home/liuzihan.guest/sf-vm')


def guest(name, *args, check=True):
    result = subprocess.run([LIMA, 'shell', name, '--', *map(str, args)],
                            capture_output=True, text=True, check=check,
                            timeout=25)
    return result.stdout.strip()


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def put(name, path, data):
    payload = json.dumps(data, sort_keys=True) + '\n'
    with tempfile.NamedTemporaryFile('w', delete=False) as stream:
        stream.write(payload)
        local = Path(stream.name)
    try:
        subprocess.run([LIMA, 'copy', str(local), name + ':' + str(path)],
                       capture_output=True, text=True, check=True, timeout=25)
    finally:
        local.unlink()
    return hashlib.sha256(payload.encode()).hexdigest()


def exists(name, path):
    return subprocess.run([LIMA, 'shell', name, '--', 'test', '-f', str(path)],
                          capture_output=True, timeout=15).returncode == 0


def setup(delay):
    token = uuid.uuid4().hex[:8].upper()
    run = 'SF-013-ATTEMPT-MARKERTEST-' + token
    action_id = run + '-POD'
    unit = 'ops-sf013-' + token.lower()
    controller = unit + '-controller'
    guard = unit + '-guard'
    planned = time.time() + delay
    master_dir = ROOT / 'runtime' / run / action_id
    worker_dir = ROOT / 'runtime' / run / action_id
    guest(MASTER, 'mkdir', '-p', '-m', '700', master_dir)
    guest(WORKER, 'mkdir', '-p', '-m', '700', worker_dir)
    master_boot = guest(MASTER, 'cat', '/proc/sys/kernel/random/boot_id')
    worker_boot = guest(WORKER, 'cat', '/proc/sys/kernel/random/boot_id')
    manifest = {'status': 'HARMLESS_VM_RESTART_TEST', 'run_id': run,
                'action_id': action_id, 'planned_utc': iso(planned)}
    manifest_path = master_dir / 'manifest.json'
    manifest_sha = put(MASTER, manifest_path, manifest)
    action = {'run_id': run, 'action_id': action_id, 'kind': 'POD',
              'worker': 'k8s-worker2', 'mechanism': 'PodMemoryStress',
              'duration_seconds': 300, 'manifest_sha256': manifest_sha,
              'capability': 'MARKER_ONLY', 'effect': 'MARKER',
              'entrypoint': str(ROOT / 'scripts/marker_cli.py')}
    registration = worker_dir / 'registration.json'
    action_sha = put(WORKER, registration, action)
    put(WORKER, worker_dir / 'auth-binding.json', {
        'run_id': run, 'action_id': action_id,
        'manifest_sha256': manifest_sha, 'capability': 'MARKER_ONLY',
        'worker_host': 'k8s-worker2', 'worker_boot_id': worker_boot,
        'master_boot_id': master_boot})
    for name in (controller, guard):
        guest(MASTER, 'sudo', 'systemd-run', '--unit=' + name,
              '--property=RuntimeMaxSec=180', '/usr/bin/sleep', '180')
    pid = int(guest(MASTER, 'sudo', 'systemctl', 'show',
                    controller + '.service', '-p', 'MainPID', '--value'))
    state = {'status': 'REGISTERED', 'run_id': run,
             'action_id': action_id, 'manifest_sha256': manifest_sha,
             'manifest_path': str(manifest_path), 'capability': 'MARKER_ONLY',
             'worker_host': 'k8s-worker2', 'worker_boot_id': worker_boot,
             'master_boot_id': master_boot, 'planned_utc': iso(planned),
             'controller_unit': controller, 'controller_pid': pid,
             'guard_unit': guard}
    put(MASTER, master_dir / 'state.json', state)
    state_digest = hashlib.sha256(json.dumps(
        state, sort_keys=True, separators=(',', ':'),
        ensure_ascii=True).encode()).hexdigest()
    put(MASTER, master_dir / 'permit.json', {
        'status': 'PERMITTED', 'run_id': run, 'action_id': action_id,
        'manifest_sha256': manifest_sha, 'state_sha256': state_digest,
        'issued_utc': iso(time.time() - 1),
        'expires_utc': iso(planned + 2)})
    calendar = datetime.fromtimestamp(planned, timezone.utc).strftime(
        '%Y-%m-%d %H:%M:%S.%f UTC')
    guest(WORKER, 'sudo', 'systemd-run', '--unit=' + unit,
          '--on-calendar=' + calendar,
          '--timer-property=AccuracySec=10ms',
          '--timer-property=RandomizedDelaySec=0',
          '--timer-property=Persistent=false',
          '--property=RuntimeMaxSec=12', '/usr/bin/python3', '-B',
          ROOT / 'marker_vm.py', '--registration', registration,
          '--sha256', action_sha)
    return {'run_id': run, 'action_id': action_id, 'unit': unit,
            'controller': controller, 'guard': guard,
            'planned_epoch': planned, 'manifest_sha256': manifest_sha,
            'master_boot_id': master_boot, 'worker_boot_id': worker_boot}


def inspect(plan):
    worker_dir = ROOT / 'runtime' / plan['run_id'] / plan['action_id']
    return {**plan,
            'marker_fired': exists(WORKER, worker_dir / 'fired.json'),
            'grant_consumed': exists(MASTER, worker_dir / 'consumed.json'),
            'master_revoked': exists(MASTER, worker_dir / 'REVOKED.json'),
            'master_boot_after': guest(MASTER, 'cat',
                                       '/proc/sys/kernel/random/boot_id'),
            'worker_boot_after': guest(WORKER, 'cat',
                                       '/proc/sys/kernel/random/boot_id'),
            'timer_state': guest(WORKER, 'sudo', 'systemctl', 'show',
                                 plan['unit'] + '.timer',
                                 '-p', 'ActiveState', '--value')}


def cleanup(plan):
    for name, unit in ((WORKER, plan['unit'] + '.timer'),
                       (WORKER, plan['unit'] + '.service'),
                       (MASTER, plan['controller'] + '.service'),
                       (MASTER, plan['guard'] + '.service')):
        guest(name, 'sudo', 'systemctl', 'stop', unit, check=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('setup', 'inspect', 'cleanup'))
    parser.add_argument('--delay', type=int, default=45)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.command == 'setup':
        result = setup(args.delay)
    else:
        if args.plan is None:
            parser.error('--plan is required')
        plan = json.loads(args.plan.read_text())
        result = inspect(plan) if args.command == 'inspect' else cleanup(plan)
    if args.output is not None:
        with args.output.open('x') as stream:
            json.dump(result, stream, indent=2, sort_keys=True)
            stream.write('\n')
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
