#!/usr/bin/env python3
"""One immutable, harmless real-systemd live-auth scenario."""

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

from auth_protocol import boot_id
from marker_controller import ROOT, SOCKET, remote


SCENARIOS = {
    'normal', 'master_denied', 'expired_permission', 'revoked',
    'worker_master_network_loss', 'controller_exit', 'guard_exit',
    'master_to_worker_loss', 'callback_error', 'identity_mismatch',
    'replay_preconsumed', 'auth_interrupted', 'action_mismatch',
    'master_boot_mismatch', 'worker_boot_mismatch',
    'guard_crash_restart', 'guard_restart_then_controller_exit',
}
WORKER = '192.168.53.54'
WORKER_ROOT = ROOT / 'marker-runtime'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def call(argv, timeout=12):
    result = subprocess.run(list(map(str, argv)), capture_output=True,
                            text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('COMMAND_FAILED: ' + result.stderr[-550:] +
                           result.stdout[-300:])
    return result.stdout


def worker_shell(command):
    return call(['ssh', '-S', SOCKET, '-o', 'BatchMode=yes',
                 'root@' + WORKER, command])


def active(unit):
    result = subprocess.run(['systemctl', 'is-active', unit + '.service'],
                            capture_output=True, text=True, timeout=4)
    return result.returncode == 0 and result.stdout.strip() == 'active'


def restart_count(unit):
    return int(call(['systemctl', 'show', unit + '.service',
                     '-p', 'NRestarts', '--value']).strip())


def wait(test, seconds, label):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if test():
            return
        time.sleep(.1)
    raise RuntimeError('WAIT_TIMEOUT: ' + label)


def unit_start(unit, script, path, digest, guard=False):
    properties = (['--property=Restart=on-failure', '--property=RestartSec=1s']
                  if guard else [])
    call(['systemd-run', '--unit=' + unit, '--collect',
          '--property=RuntimeMaxSec=55', *properties,
          '/usr/bin/python3', '-B', str(script),
          '--registration', str(path), '--registration-sha', digest])


def registration(scenario, token):
    run_id = 'SF013-AUTH-MARKER-' + token
    action_id = run_id + '-MARK'
    root = ROOT / 'markers' / run_id
    root.mkdir(parents=True, mode=0o700, exist_ok=False)
    value = {
        'status': 'PREREGISTERED_HARMLESS_LIVE_AUTH_MARKER',
        'run_id': run_id, 'action_id': action_id,
        'unit': 'ops-sf013-' + action_id.lower(),
        'controller_unit': 'ops-sf013-auth-control-' + token,
        'guard_unit': 'ops-sf013-auth-guard-' + token,
        'planned_utc': (datetime.now(timezone.utc) +
                        timedelta(seconds=24)).isoformat(),
        'capability': 'MARKER_ONLY', 'worker_host': 'k8s-worker2',
        'worker_boot_id': worker_shell('cat /proc/sys/kernel/random/boot_id').strip(),
        'master_boot_id': boot_id(), 'scenario': scenario,
        'real_fault_adapter_allowed': False}
    if scenario == 'worker_boot_mismatch':
        value['worker_boot_id'] = '44444444-4444-4444-4444-444444444444'
    path = root / 'registration.json'
    with path.open('x') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
    value['manifest_sha256'] = sha(path)
    worker_dir = WORKER_ROOT / run_id
    worker_shell('install -d -m 700 ' + str(worker_dir))
    call(['scp', '-q', '-o', 'BatchMode=yes',
          '-o', 'ControlPath=' + SOCKET, str(path),
          'root@' + WORKER + ':' + str(worker_dir / 'registration.json')])
    return value, path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scenario', choices=sorted(SCENARIOS), required=True)
    parser.add_argument('--token', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if (socket.gethostname() != 'k8s-master' or args.output.exists() or
            not args.token.startswith('20261019') or
            not args.token.replace('-', '').isalnum() or
            len(args.token) > 48):
        raise RuntimeError('New bounded Master-only Marker run required')
    reg, path = registration(args.scenario, args.token)
    root = path.parent
    planned = datetime.fromisoformat(reg['planned_utc']).timestamp()
    controller, guard = reg['controller_unit'], reg['guard_unit']
    isolated = Path(SOCKET + '.test-isolated')
    moved = False
    result = {'status': 'LIVE_AUTH_MARKER_SCENARIO_FAILED',
              'scenario': args.scenario, 'run_id': reg['run_id'],
              'planned_utc': reg['planned_utc'],
              'registration_sha256': reg['manifest_sha256'],
              'real_faults_injected': 0}
    try:
        unit_start(guard, ROOT / 'scripts/marker_guard.py', path,
                   reg['manifest_sha256'], guard=True)
        unit_start(controller, ROOT / 'scripts/marker_controller.py', path,
                   reg['manifest_sha256'])
        wait(lambda: (root / 'controller-ready.json').exists(), 8,
             'controller preregistered and armed')
        worker = remote(reg, 'inspect')
        if not worker['armed'] or not active(guard) or not active(controller):
            raise RuntimeError('Marker Timer/Guard/Controller not pending')
        result['both_units_and_timer_pending'] = True
        if args.scenario == 'auth_interrupted':
            while time.time() < planned + .3:
                time.sleep(.02)
            killer = '''import os,signal,subprocess
rows=subprocess.check_output(['ps','-eo','pid,args'],text=True).splitlines()
for row in rows:
    parts=row.strip().split(None,1)
    if len(parts)==2 and parts[1].startswith('ssh -F /dev/null -i /root/ops-competition-2026/final-case-auth-secrets-20261019/worker2-live-auth-ed25519 ') and parts[1].endswith(' final-case-auth-v1'):
        os.kill(int(parts[0]),signal.SIGKILL)
        print(parts[0])'''
            killed = worker_shell('python3 -c ' + shlex.quote(killer)).strip()
            result['inflight_ssh_client_killed'] = bool(killed)
            if not killed:
                raise RuntimeError('In-flight authorization SSH process not found')
        if args.scenario in ('controller_exit', 'master_to_worker_loss',
                             'guard_exit', 'guard_crash_restart',
                             'guard_restart_then_controller_exit'):
            while time.time() < planned - 7:
                time.sleep(.1)
            if args.scenario == 'master_to_worker_loss':
                if isolated.exists():
                    raise RuntimeError('Existing isolated control socket')
                os.rename(SOCKET, isolated)
                moved = True
                result['master_worker_ssh_unavailable'] = not Path(SOCKET).exists()
            unit = (guard if args.scenario in ('guard_exit',
                                               'guard_crash_restart',
                                               'guard_restart_then_controller_exit')
                    else controller)
            previous_restarts = restart_count(guard) if args.scenario in (
                'guard_crash_restart', 'guard_restart_then_controller_exit') else None
            if args.scenario == 'guard_exit':
                call(['systemctl', 'stop', unit + '.service'])
                if active(unit):
                    raise RuntimeError('Guard remained active after explicit stop')
            else:
                call(['systemctl', 'kill', '--signal=SIGKILL', unit + '.service'])
                if args.scenario in ('guard_crash_restart',
                                     'guard_restart_then_controller_exit'):
                    wait(lambda: (restart_count(guard) > previous_restarts and
                                  active(guard)), 5,
                         'Guard restarted after SIGKILL')
                    result['guard_restart_count'] = restart_count(guard)
                    if args.scenario == 'guard_restart_then_controller_exit':
                        call(['systemctl', 'kill', '--signal=SIGKILL',
                              controller + '.service'])
        while time.time() < planned + 3.5:
            time.sleep(.1)
    except BaseException as exc:
        result['error'] = type(exc).__name__ + ': ' + str(exc)
    finally:
        if moved:
            os.rename(isolated, SOCKET)
            result['master_worker_socket_restored'] = Path(SOCKET).is_socket()
        try:
            inspected = remote(reg, 'inspect')
            result['worker_inspection'] = inspected
            result['final_worker_cancel'] = remote(reg, 'cancel')
            fired = inspected['fired']
            expected = args.scenario in ('normal', 'guard_crash_restart')
            result['expected_fired'] = expected
            triggered_as_planned = (inspected['started'] or
                                    args.scenario in ('controller_exit',
                                        'guard_restart_then_controller_exit'))
            no_pending = all(
                row['ActiveState'] not in ('active', 'activating', 'reloading')
                for row in inspected['units'].values())
            result['worker_entry_or_pretrigger_cancel_verified'] = triggered_as_planned
            result['no_pending_timer_or_service'] = no_pending
            result['status'] = ('LIVE_AUTH_MARKER_SCENARIO_PASS'
                                if not result.get('error') and fired == expected and
                                triggered_as_planned and no_pending and
                                result['final_worker_cancel']['status'] ==
                                'ATTEMPT_TIMER_REVOKED_AND_INACTIVE' else
                                'LIVE_AUTH_MARKER_SCENARIO_FAILED')
        except BaseException as exc:
            result['cleanup_error'] = type(exc).__name__ + ': ' + str(exc)
            result['status'] = 'LIVE_AUTH_MARKER_CLEANUP_UNVERIFIED'
        for unit in (controller, guard):
            subprocess.run(['systemctl', 'stop', unit + '.service'],
                           capture_output=True, text=True, timeout=10)
        state = ROOT / 'runtime' / reg['run_id'] / reg['action_id']
        result['master_grant_consumed'] = (state / 'consumed.json').exists()
        result['master_revoke_present'] = (state / 'REVOKED.json').exists()
        result['guard_terminal_present'] = (root / 'guard-terminal.json').exists()
        result['guard_failure_present'] = (root / 'guard-failure.json').exists()
        result['at_utc'] = datetime.now(timezone.utc).isoformat()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x') as stream:
            json.dump(result, stream, indent=2)
            stream.write('\n')
        print(json.dumps({'status': result['status'],
                          'scenario': args.scenario,
                          'fired': result.get('worker_inspection', {}).get('fired'),
                          'error': result.get('error')}), flush=True)
    if result['status'] != 'LIVE_AUTH_MARKER_SCENARIO_PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
