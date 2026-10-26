#!/usr/bin/env python3
"""Full 1800-second no-fault capture with a separate harmless Marker chain."""

import argparse
import hashlib
import json
import os
import shlex
import socket
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
ROOT = OPS / 'final-case-fail-closed-v1-20261019'
SAFETY = ROOT
DATA = Path('/data/stage5-v3/ops-competition-2026/final-case-fail-closed-v1-20261019')
PLAN = ROOT / 'design/live-auth-nofault-1800.json'
RUN = DATA / 'validation-runs-v7/VAL7'
CONTROL = ROOT / 'results/no-fault-1800-live-auth-01'
MARKER_ID = 'SF013-AUTH-MARKER-20261019-full1800'
MARKER_ROOT = SAFETY / 'markers' / MARKER_ID
MASTER_TO_WORKER = '/run/ops-sf013-auth/w2.sock'
CAPTURE_UNIT = 'ops-sf013-auth-readiness-capture-20261019-01'
OBSERVER_UNIT = 'ops-sf013-auth-readiness-observer-20261019-01'
CONTROLLER_UNIT = 'ops-sf013-auth-control-20261019-full1800'
GUARD_UNIT = 'ops-sf013-auth-guard-20261019-full1800'
FLOOR = 5_000_000_000


def now():
    return datetime.now(timezone.utc).isoformat()


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def write_new(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('x') as stream:
        json.dump(payload, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(0o600)


def command(argv, timeout=25):
    result = subprocess.run(list(map(str, argv)), capture_output=True,
                            text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('Command failed: ' + str(argv[:3]) + ' ' +
                           result.stderr[-500:] + result.stdout[-500:])
    return result.stdout


def systemd_start(unit, argv, seconds, guard=False):
    options = ['--property=Restart=on-failure', '--property=RestartSec=1s'] if guard else []
    command(['systemd-run', '--unit=' + unit, '--collect',
             '--property=RuntimeMaxSec=' + str(seconds), *options, *argv])


def is_active(unit):
    result = subprocess.run(['systemctl', 'is-active', unit + '.service'],
                            capture_output=True, text=True, timeout=8)
    return result.returncode == 0 and result.stdout.strip() == 'active'


def worker(args):
    argv = ['python3', '-B', str(SAFETY / 'scripts/marker_worker.py'),
            args[0], '--run-id', MARKER_ID, '--action-id', MARKER_ID + '-MARK',
            '--unit', 'ops-sf013-' + (MARKER_ID + '-MARK').lower(),
            '--manifest-sha', sha(MARKER_ROOT / 'registration.json'), *args[1:]]
    return json.loads(command(['ssh', '-S', MASTER_TO_WORKER, '-o', 'BatchMode=yes',
                    'root@192.168.53.54', shlex.join(argv)]).splitlines()[-1])


def prereq():
    if socket.gethostname() != 'k8s-master':
        raise RuntimeError('Readiness chain is Master-only')
    if RUN.exists() or CONTROL.exists() or MARKER_ROOT.exists():
        raise RuntimeError('No-fault run identity already exists')
    plan = read(PLAN)
    if (plan['status'] != 'PREREGISTERED_LIVE_AUTH_NOFAULT_1800' or
            plan['faults_allowed'] is not False or
            plan['runs']['VAL7'] != {'duration_seconds': 1800, 'seed': 751720} or
            plan['readiness_harness']['run_id'] !=
            'SF-FINAL-LIVE-AUTH-NOFAULT-1800-20261019-01' or
            plan['readiness_harness']['real_fault_adapter_access'] is not False):
        raise RuntimeError('No-fault plan did not freeze the Marker-only scope')
    if any(sha(path) != digest for path, digest in plan['source_sha256'].items()):
        raise RuntimeError('No-fault code/source lock changed')
    # Explicit allowlist, including source hashes, prevents a real fault adapter
    # from entering this separate test executable.
    for path in (ROOT / 'validation-v7/run_validation.py',
                 ROOT / 'scripts/readiness_observer.py',
                 SAFETY / 'scripts/marker_controller.py',
                 SAFETY / 'scripts/marker_guard.py',
                 SAFETY / 'scripts/marker_worker.py'):
        if str(path) not in plan['source_sha256']:
            raise RuntimeError('Executable missing from Marker-only source lock')
    if not Path(MASTER_TO_WORKER).exists():
        raise RuntimeError('Authenticated Worker control socket missing')
    return plan


def register_marker(schedule, plan_sha):
    t0 = stamp(schedule['start_utc'])
    if not 20 <= t0 - time.time() <= 45:
        raise RuntimeError('Capture T0 missed Marker setup window')
    MARKER_ROOT.mkdir(parents=True, mode=0o700, exist_ok=False)
    worker_boot = command(['ssh', '-S', MASTER_TO_WORKER,
                           '-o', 'BatchMode=yes', 'root@192.168.53.54',
                           'cat /proc/sys/kernel/random/boot_id']).strip()
    master_boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    registration = {'status': 'PREREGISTERED_HARMLESS_LIVE_AUTH_MARKER',
        'run_id': MARKER_ID, 'action_id': MARKER_ID + '-MARK',
        'unit': 'ops-sf013-' + (MARKER_ID + '-MARK').lower(),
        'controller_unit': CONTROLLER_UNIT, 'guard_unit': GUARD_UNIT,
        'planned_utc': datetime.fromtimestamp(t0 + 600, timezone.utc).isoformat(),
        'scenario': 'normal', 'capability': 'MARKER_ONLY',
        'worker_host': 'k8s-worker2', 'worker_boot_id': worker_boot,
        'master_boot_id': master_boot, 'capture_plan_sha256': plan_sha,
        'real_fault_adapter_allowed': False}
    target = MARKER_ROOT / 'registration.json'
    write_new(target, registration)
    worker_root = SAFETY / 'marker-runtime' / MARKER_ID
    command(['ssh', '-S', MASTER_TO_WORKER, '-o', 'BatchMode=yes',
             'root@192.168.53.54', 'install -d -m 700 ' + str(worker_root)])
    command(['scp', '-q', '-o', 'BatchMode=yes',
             '-o', 'ControlPath=' + MASTER_TO_WORKER, str(target),
             'root@192.168.53.54:' + str(worker_root / 'registration.json')])
    return target, sha(target)


def cleanup_units(marker_registered):
    errors = []
    for unit in (CONTROLLER_UNIT, GUARD_UNIT, OBSERVER_UNIT, CAPTURE_UNIT):
        subprocess.run(['systemctl', 'stop', unit + '.service'],
                       capture_output=True, text=True, timeout=20)
    if marker_registered:
        try:
            result = worker(['cancel'])
            if result['status'] != 'ATTEMPT_TIMER_REVOKED_AND_INACTIVE':
                raise RuntimeError('Marker Worker did not verify revoke')
        except Exception as exc:
            errors.append(type(exc).__name__ + ': ' + str(exc))
    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    plan = prereq()
    CONTROL.mkdir(parents=True, mode=0o700)
    registration = None
    result = {'status': 'NOFAULT_1800_CHAIN_FAILED', 'at_utc': now(),
              'plan_sha256': sha(PLAN), 'real_faults_injected': 0,
              'production_case_created': False}
    try:
        systemd_start(CAPTURE_UNIT, ['/usr/bin/python3', '-B',
            str(ROOT / 'validation-v7/run_validation.py'), '--role', 'VAL7'], 2700)
        deadline = time.monotonic() + 25
        schedule_path = RUN / 'schedule.json'
        while not schedule_path.is_file() and time.monotonic() < deadline:
            if not is_active(CAPTURE_UNIT):
                raise RuntimeError('Capture failed before schedule registration')
            time.sleep(0.2)
        if not schedule_path.is_file():
            raise RuntimeError('Capture schedule did not appear')
        schedule = read(schedule_path)
        if (schedule['duration_seconds'] != 1800 or
                schedule['plan_sha256'] != sha(PLAN) or
                schedule['faults_injected'] != 0 or
                len(schedule['windows']) != 6):
            raise RuntimeError('No-fault capture schedule invalid')
        registration, registration_sha = register_marker(schedule, sha(PLAN))
        systemd_start(OBSERVER_UNIT, ['/usr/bin/python3', '-B',
            str(ROOT / 'scripts/readiness_observer.py'),
            '--capture-root', str(RUN), '--output', str(CONTROL / 'observer'),
            '--collector-uid', schedule['collector']['metadata']['uid']], 2500)
        ready = CONTROL / 'observer/ready.json'
        deadline = time.monotonic() + 15
        while not ready.is_file() and time.monotonic() < deadline:
            if not is_active(OBSERVER_UNIT):
                raise RuntimeError('Observer exited before READY')
            time.sleep(0.1)
        if not ready.is_file():
            raise RuntimeError('Observer READY handshake timed out')
        marker_args = ['--registration', str(registration),
                       '--registration-sha', registration_sha]
        systemd_start(GUARD_UNIT, ['/usr/bin/python3', '-B',
            str(SAFETY / 'scripts/marker_guard.py'), *marker_args], 950, True)
        systemd_start(CONTROLLER_UNIT, ['/usr/bin/python3', '-B',
            str(SAFETY / 'scripts/marker_controller.py'), *marker_args], 900)
        result.update(status='NOFAULT_1800_CHAIN_RUNNING',
                      capture_start_utc=schedule['start_utc'],
                      capture_end_utc=schedule['end_utc'],
                      collector_uid=schedule['collector']['metadata']['uid'],
                      marker_registration_sha256=registration_sha,
                      unit_names=[CAPTURE_UNIT, OBSERVER_UNIT, CONTROLLER_UNIT,
                                  GUARD_UNIT])
        write_new(CONTROL / 'startup.json', result)
        deadline = stamp(schedule['end_utc']) + 360
        while time.time() < deadline:
            terminal = RUN / 'execution-result.json'
            observer_terminal = CONTROL / 'observer/terminal.json'
            marker_terminal = MARKER_ROOT / 'controller-terminal.json'
            guard_terminal = MARKER_ROOT / 'guard-terminal.json'
            if terminal.is_file() and read(terminal)['status'] != \
                    'VALIDATION_EXECUTION_COMPLETE_PENDING_ASSESSMENT':
                raise RuntimeError('Capture failed: ' + str(read(terminal).get('error')))
            if observer_terminal.is_file() and read(observer_terminal)['status'] != \
                    'NOFAULT_OBSERVER_COMPLETE':
                raise RuntimeError('Observer failed: ' + str(read(observer_terminal).get('error')))
            if marker_terminal.is_file() and read(marker_terminal).get('worker_cleanup_verified') is not True:
                raise RuntimeError('Marker controller cleanup unverified')
            if guard_terminal.is_file() and read(guard_terminal)['worker_cancel']['status'] != \
                    'ATTEMPT_TIMER_REVOKED_AND_INACTIVE':
                raise RuntimeError('Marker guard cleanup unverified')
            if all(path.is_file() for path in (terminal, observer_terminal,
                                                marker_terminal, guard_terminal)):
                break
            if not terminal.is_file() and not is_active(CAPTURE_UNIT):
                raise RuntimeError('Capture process exited without terminal')
            if not observer_terminal.is_file() and not is_active(OBSERVER_UNIT):
                raise RuntimeError('Observer process exited without terminal')
            if time.time() > stamp(schedule['start_utc']) + 650 and not marker_terminal.is_file():
                raise RuntimeError('Marker controller did not finish by deadline')
            if os.statvfs('/data/stage5-v3').f_bavail * \
                    os.statvfs('/data/stage5-v3').f_frsize < FLOOR:
                raise RuntimeError('Master data disk crossed five-GB floor')
            time.sleep(5)
        else:
            raise RuntimeError('Joint 1800-second test exceeded audit deadline')
        command(['/usr/bin/python3', '-B',
            str(ROOT / 'validation-v7/evaluate.py'), '--role', 'VAL7',
            '--plan', str(PLAN), '--run-dir', str(RUN),
            '--output', str(RUN / 'assessment.json')], timeout=300)
        assessment = read(RUN / 'assessment.json')
        if assessment['status'] != 'NO_FAULT_VALIDATION_PASS':
            raise RuntimeError('No-fault data quality did not pass: ' +
                               repr(assessment['blockers']))
        observed = worker(['inspect'])
        if (not observed['fired'] or not observed['revoked'] or
                any(item['ActiveState'] in ('active', 'activating', 'reloading')
                    for item in observed['units'].values())):
            raise RuntimeError('Harmless Marker terminal state inconsistent')
        result.update(status='NOFAULT_1800_CHAIN_PASS',
                      completed_utc=now(), capture_terminal_sha256=sha(RUN / 'execution-result.json'),
                      assessment_sha256=sha(RUN / 'assessment.json'),
                      observer_terminal_sha256=sha(CONTROL / 'observer/terminal.json'),
                      marker_terminal_sha256=sha(MARKER_ROOT / 'controller-terminal.json'),
                      guard_terminal_sha256=sha(MARKER_ROOT / 'guard-terminal.json'),
                      no_residual_marker_timer=True)
    except BaseException as error:
        result['error'] = type(error).__name__ + ': ' + str(error)
    finally:
        result['cleanup_errors'] = cleanup_units(registration is not None)
        if result['cleanup_errors']:
            result['status'] = 'NOFAULT_1800_CHAIN_CLEANUP_UNVERIFIED'
        result['completed_utc'] = now()
        write_new(args.output, result)
        print(json.dumps({'status': result['status'], 'error': result.get('error'),
                          'cleanup_errors': result['cleanup_errors']}), flush=True)
    if result['status'] != 'NOFAULT_1800_CHAIN_PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
