#!/usr/bin/env python3
"""Bound multi-action control sequence. Rehearsal effects remain harmless."""

import argparse
import json
import os
import shlex
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

SHARED = Path('/root/ops-competition-2026/final-case-unified-execution-v2-20261019/scripts')
sys.path.insert(0, str(SHARED))
import auth_protocol
from action_decision import create_once, permit, revoke
from control_contract import ROOT, command_for_worker, load_manifest, sha
from storage_policy import check as check_storage


SOCKETS = {'k8s-worker2': '/run/ops-final-ssh/w2.sock',
           'k8s-worker3': '/run/ops-final-ssh/w3.sock'}
HOSTS = {'k8s-worker2': '192.168.53.54',
         'k8s-worker3': '192.168.53.55'}
AUTH_RUNTIME = Path('/root/ops-competition-2026/final-case-unified-execution-v1-20261019/runtime')
HTTP_RUNTIME_MAX_SECONDS = 2400


def utc(value=None):
    return datetime.fromtimestamp(time.time() if value is None else value,
                                  timezone.utc).isoformat()


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


class SystemIO:
    def __init__(self, manifest, manifest_path, manifest_sha):
        if socket.gethostname() != 'k8s-master':
            raise ValueError('FORMAL_MASTER_ONLY')
        self.manifest = manifest
        self.path = Path(manifest_path)
        self.sha = manifest_sha
        self.control = ROOT / 'runtime' / manifest['run_id'] / 'master'
        self.control.mkdir(parents=True, exist_ok=True, mode=0o700)

    def remote(self, worker, argv, *, timeout=30):
        if worker not in SOCKETS or not Path(SOCKETS[worker]).is_socket():
            raise RuntimeError('FORMAL_REMOTE_CONTROL_SOCKET_MISSING')
        result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=3',
            '-S', SOCKETS[worker], 'root@' + HOSTS[worker], shlex.join(map(str, argv))],
            text=True, capture_output=True, timeout=timeout)
        if result.returncode:
            raise RuntimeError('FORMAL_REMOTE_FAILED:' + result.stderr[-350:])
        return result.stdout

    def put(self, worker, destination, value):
        self.remote(worker, ['mkdir', '-p', '-m', '700', Path(destination).parent])
        with tempfile.NamedTemporaryFile('w', delete=False) as stream:
            json.dump(value, stream, sort_keys=True)
            stream.write('\n')
            source = stream.name
        try:
            subprocess.run(['scp', '-q', '-o', 'BatchMode=yes',
                '-o', 'ControlPath=' + SOCKETS[worker], source,
                'root@' + HOSTS[worker] + ':' + str(destination)],
                check=True, capture_output=True, timeout=30)
        finally:
            Path(source).unlink(missing_ok=True)

    def copy_exact(self, worker, source, destination, expected_sha):
        self.remote(worker, ['mkdir', '-p', '-m', '700', Path(destination).parent])
        subprocess.run(['scp', '-q', '-p', '-o', 'BatchMode=yes',
            '-o', 'ControlPath=' + SOCKETS[worker], str(source),
            'root@' + HOSTS[worker] + ':' + str(destination)],
            check=True, capture_output=True, timeout=30)
        actual = self.remote(worker, ['sha256sum', destination]).split()[0]
        if actual != expected_sha:
            raise RuntimeError('FORMAL_REMOTE_EXACT_COPY_HASH_MISMATCH')

    def register(self, action):
        run = action['run_id']
        directory = AUTH_RUNTIME / run / action['action_id']
        directory.mkdir(parents=True, mode=0o700, exist_ok=False)
        local = ROOT / 'runtime' / run / action['action_id']
        local.mkdir(parents=True, mode=0o700, exist_ok=False)
        if action['worker'] == 'k8s-master':
            worker_boot = None
        else:
            worker_boot = self.remote(action['worker'],
                ['cat', '/proc/sys/kernel/random/boot_id']).strip()
            self.remote(action['worker'], ['mkdir', '-p', '-m', '700', local])
            self.copy_exact(action['worker'], self.path, self.path, self.sha)
            self.put(action['worker'], local / 'auth-binding.json', {
                'run_id': run, 'action_id': action['action_id'],
                'manifest_sha256': self.sha,
                'capability': action['capability'],
                'worker_host': action['worker'],
                'worker_boot_id': worker_boot,
                'master_boot_id': auth_protocol.boot_id()})
            self.put(action['worker'], local / 'cancel-binding.json', {
                'run_id': run, 'action_id': action['action_id'],
                'manifest_sha256': self.sha, 'worker_host': action['worker'],
                'unit': action['unit'],
                'adapter_sha256': action.get('qualification', {}).get('adapter_sha256')})
        state = {'status': 'REGISTERED', 'run_id': run,
            'action_id': action['action_id'], 'manifest_sha256': self.sha,
            'manifest_path': str(self.path), 'capability': action['capability'],
            'worker_host': action['worker'], 'worker_boot_id': worker_boot,
            'master_boot_id': auth_protocol.boot_id(),
            'planned_utc': action['planned_start_utc'],
            'controller_unit': self.manifest['controller_unit'],
            'controller_pid': os.getpid(),
            'guard_unit': self.manifest['guard_unit']}
        if action['worker'] == 'k8s-master':
            state.update({'planned_start_epoch': stamp(action['planned_start_utc']),
                          'planned_end_epoch': stamp(action['planned_start_utc']) +
                          action['duration_seconds'],
                          'controller_start_ticks': self._start_ticks(os.getpid()),
                          'requester_pid': None, 'requester_start_ticks': None})
        create_once(directory / 'state.json', state)
        if action['worker'] == 'k8s-master':
            create_once(local / 'state.json', state)
        return {'status': 'REGISTERED', 'master_state': str(directory)}

    @staticmethod
    def _start_ticks(pid):
        return int(Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19])

    def arm(self, action):
        if action['worker'] == 'k8s-master':
            unit = 'ops-' + action['run_id'].lower().replace('-', '') + '-http-workload'
            rehearsal = self.manifest['mode'] == 'HARMLESS_REHEARSAL'
            if rehearsal and self.manifest.get('rehearsal_duration_seconds') == 1800:
                output = Path(self.manifest['rehearsal_data_root']) / 'state-machine'
            else:
                output = ((ROOT / 'rehearsal' if rehearsal else
                           Path('/data/stage5-v3/ops-competition-2026/runs')) /
                          action['run_id'] / 'state-machine')
            command = ['systemd-run', '--unit=' + unit,
                '--property=RuntimeMaxSec=' + str(HTTP_RUNTIME_MAX_SECONDS),
                '/usr/bin/python3', '-B',
                str(ROOT / 'scripts/http_workload.py'), '--manifest', str(self.path),
                '--manifest-sha', self.sha, '--output', str(output)]
            if rehearsal:
                command.append('--no-fault-rehearsal')
            result = subprocess.run(command, capture_output=True, text=True, timeout=15)
            if result.returncode:
                raise RuntimeError('HTTP_WORKLOAD_UNIT_START_FAILED')
            return {'status': 'MASTER_HTTP_WORKLOAD_STARTED', 'unit': unit}
        local = ROOT / 'runtime' / action['run_id'] / action['action_id']
        self.put(action['worker'], local / 'arm-owner.json', {
            'run_id': action['run_id'], 'action_id': action['action_id'],
            'manifest_sha256': self.sha, 'role': 'MASTER_CONTROLLER_ONLY',
            'planned_utc': action['planned_start_utc'],
            'controller_pid': os.getpid(), 'issued_utc': utc()})
        result = self.remote(action['worker'], ['/usr/bin/python3', '-B',
            ROOT / 'scripts/worker_fault_entry.py', 'arm', '--manifest',
            self.path, '--manifest-sha', self.sha, '--action-id',
            action['action_id']])
        return json.loads(result.splitlines()[-1])

    def preflight(self, action):
        status = self.manifest.get('safety')
        expected = ('NOFAULT_BASIC_HEALTH_PASS' if self.manifest['mode'] ==
                    'HARMLESS_REHEARSAL' else 'FRESH_GLOBAL_SAFETY_PASS')
        if status != expected:
            raise RuntimeError('FORMAL_FRESH_GLOBAL_SAFETY_REQUIRED')
        if self.manifest['mode'] == 'FORMAL_CANDIDATE':
            proof = action['qualification']
            if proof['status'] != 'QUALIFIED_FOR_THIS_VERSION':
                raise RuntimeError('FORMAL_EFFECT_NOT_QUALIFIED')
            beat = self.live_heartbeat()
            if beat['status'] != 'DYNAMIC_STORAGE_AND_BASIC_HEALTH_PASS':
                raise RuntimeError('FORMAL_DYNAMIC_SAFETY_BLOCKED')
        return {'status': 'PRE_EFFECT_SAFETY_PASS', 'action_id': action['action_id']}

    def live_heartbeat(self):
        path = self.control / 'safety-heartbeat.json'
        if not path.is_file():
            raise RuntimeError('FORMAL_DYNAMIC_SAFETY_MISSING')
        beat = json.loads(path.read_text())
        if (beat.get('run_id') != self.manifest['run_id'] or
                not 0 <= time.time() - stamp(beat['at_utc']) <= 20 or
                beat.get('status') != 'DYNAMIC_STORAGE_AND_BASIC_HEALTH_PASS'):
            raise RuntimeError('FORMAL_DYNAMIC_SAFETY_STALE_OR_FAILED')
        free = {(item['host'], item['mount']): item['free_bytes']
                for item in beat['space']}
        if check_storage(self.manifest['run_id'].split('-ATTEMPT-')[0], free)[
                'status'] != 'STORAGE_PASS':
            raise RuntimeError('FORMAL_DYNAMIC_STORAGE_BLOCKED')
        return beat

    def permit(self, action):
        directory = AUTH_RUNTIME / action['run_id'] / action['action_id']
        state = json.loads((directory / 'state.json').read_text())
        if action['worker'] == 'k8s-master':
            if self.manifest['mode'] == 'HARMLESS_REHEARSAL':
                return {'status': 'HARMLESS_NO_HEADER_PERMIT'}
            local = ROOT / 'runtime' / action['run_id'] / action['action_id']
            local_state = json.loads((local / 'state.json').read_text())
            if local_state.get('requester_pid') is None:
                raise RuntimeError('HTTP_REQUESTER_NOT_READY')
            value = {'status': 'PERMITTED', 'run_id': action['run_id'],
                     'action_id': action['action_id'],
                     'manifest_sha256': self.sha,
                     'issued_epoch': time.time(),
                     'expires_epoch': stamp(action['planned_start_utc']) +
                     action['duration_seconds']}
            permit(local, self._identity(action), value)
            return value
        value = {'status': 'PERMITTED', 'run_id': action['run_id'],
                 'action_id': action['action_id'], 'manifest_sha256': self.sha,
                 'state_sha256': auth_protocol.digest(state),
                 'issued_utc': utc(),
                 'expires_utc': utc(stamp(action['planned_start_utc']) + 2)}
        if self.manifest['mode'] == 'FORMAL_CANDIDATE':
            self.live_heartbeat()
            qualification = action['qualification']
            authorization = {'run_id': action['run_id'],
                'action_id': action['action_id'], 'manifest_sha256': self.sha,
                'permission': 'BOUND_QUALIFIED_FAULT',
                'status': 'COMPETITION_PREFLIGHT_AUTHORIZED',
                'at_utc': utc(), 'faults_allowed': True,
                'actual_environment_pass': True,
                'independent_watchdog_pass': True,
                'registration_sha256': action['qualified_registration_sha256'],
                'watchdog_proof_file': qualification['watchdog_proof_file'],
                'watchdog_proof_sha256': qualification['watchdog_proof_sha256']}
            local = ROOT / 'runtime' / action['run_id'] / action['action_id']
            create_once(local / 'authorization.json', authorization)
            self.put(action['worker'], local / 'authorization.json', authorization)
        permit(directory, self._identity(action), value)
        return value

    def await_terminal(self, action, *, deadline):
        local = ROOT / 'runtime' / action['run_id'] / action['action_id']
        while time.time() < deadline:
            if action['worker'] == 'k8s-master':
                path = local / 'http-terminal.json'
                if path.is_file():
                    return json.loads(path.read_text())
            else:
                raw = self.remote(action['worker'], ['/usr/bin/python3', '-B',
                    '-c', 'import pathlib; p=pathlib.Path(' + repr(str(local / 'terminal.json')) +
                    '); print(p.read_text() if p.is_file() else "null")'], timeout=7)
                value = json.loads(raw)
                if value:
                    return value
            time.sleep(.25)
        raise TimeoutError('ACTION_TERMINAL_TIMEOUT')

    def revoke(self, action, reason):
        auth_dir = AUTH_RUNTIME / action['run_id'] / action['action_id']
        result = revoke(auth_dir, self._identity(action), reason)
        if action['worker'] == 'k8s-master':
            local = ROOT / 'runtime' / action['run_id'] / action['action_id']
            revoke(local, self._identity(action), reason)
            if reason in ('INDEPENDENT_GUARD_TERMINAL', 'CONTROLLER_TERMINAL'):
                unit = 'ops-' + action['run_id'].lower().replace('-', '') + '-http-workload'
                subprocess.run(['systemctl', 'stop', unit + '.service'],
                               capture_output=True, text=True, timeout=20)
            return {'master_revoke': result, 'status': 'MASTER_HTTP_REVOKED'}
        try:
            raw = self.remote(action['worker'], ['/usr/bin/python3', '-B',
                ROOT / 'scripts/worker_fault_entry.py', 'cancel', '--manifest',
                self.path, '--manifest-sha', self.sha, '--action-id',
                action['action_id']])
            return {'master_revoke': result, 'worker': json.loads(raw.splitlines()[-1]),
                    'entry': 'MANIFEST_BOUND_CANCEL'}
        except BaseException as first:
            raw = self.remote(action['worker'], ['/usr/bin/python3', '-B',
                ROOT / 'scripts/worker_fault_entry.py', 'emergency-cancel',
                '--manifest', self.path, '--manifest-sha', self.sha,
                '--action-id', action['action_id']])
            return {'master_revoke': result,
                    'worker': json.loads(raw.splitlines()[-1]),
                    'entry': 'IDENTITY_BOUND_EMERGENCY_CANCEL',
                    'normal_cancel_error': type(first).__name__ + ':' + str(first)}

    def guard_running(self):
        output = subprocess.check_output(['systemctl', 'show',
            self.manifest['guard_unit'] + '.service', '-p', 'ActiveState',
            '--value'], text=True, timeout=3).strip()
        if output != 'active':
            return False
        if self.manifest['mode'] == 'FORMAL_CANDIDATE':
            try:
                self.live_heartbeat()
            except BaseException:
                return False
        return True

    def _identity(self, action):
        return {key: action[key] for key in ('run_id', 'action_id',
                                             'manifest_sha256')}


class Sequence:
    def __init__(self, manifest, io, *, clock=time.time, sleep=time.sleep):
        self.manifest = manifest
        self.io = io
        self.clock = clock
        self.sleep = sleep
        self.registered = []
        self.results = {}

    def _wait(self, deadline):
        while self.clock() < deadline:
            if not self.io.guard_running():
                raise RuntimeError('FORMAL_GUARD_NOT_RUNNING')
            self.sleep(min(.25, max(0, deadline - self.clock())))

    def run(self):
        actions = sorted(self.manifest['actions'],
                         key=lambda item: stamp(item['planned_start_utc']))
        outcome = {'status': 'FAILED_CLOSED', 'run_id': self.manifest['run_id'],
                   'actions': self.results, 'cleanup': {}}
        try:
            for action in actions:
                self.registered.append(action)
                self.io.register(action)
                self.io.arm(action)
            for action in actions:
                self._wait(stamp(action['planned_start_utc']) - 5)
                self.io.preflight(action)
                self.io.permit(action)
                terminal = self.io.await_terminal(action,
                    deadline=stamp(action['planned_start_utc']) +
                    action['duration_seconds'] + 15)
                self.results[action['action_id']] = terminal
                if terminal.get('status') not in ('ACTION_TERMINAL',
                                                   'HTTP_ACTION_COMPLETE',
                                                   'HARMLESS_HTTP_WINDOW_COMPLETE'):
                    raise RuntimeError('ACTION_FAILED_' + action['action_id'])
                self.io.revoke(action, 'ACTION_COMPLETE')
            if 'window_end_utc' in self.manifest:
                self._wait(stamp(self.manifest['window_end_utc']))
            outcome['status'] = 'CONTROL_SEQUENCE_COMPLETE_PENDING_ADMISSION'
        except BaseException as error:
            outcome['error'] = type(error).__name__ + ':' + str(error)
        finally:
            final_reason = ('CONTROLLER_SUCCESS_FINALIZATION' if outcome['status'] ==
                            'CONTROL_SEQUENCE_COMPLETE_PENDING_ADMISSION' else
                            'CONTROLLER_TERMINAL')
            for action in reversed(self.registered):
                try:
                    outcome['cleanup'][action['action_id']] = self.io.revoke(
                        action, final_reason)
                except BaseException as error:
                    outcome['status'] = 'CLEANUP_UNVERIFIED'
                    outcome['cleanup'][action['action_id']] = {
                        'error': type(error).__name__ + ':' + str(error)}
        return outcome


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--manifest-sha', required=True)
    args = parser.parse_args()
    manifest = load_manifest(args.manifest, args.manifest_sha)
    io = SystemIO(manifest, args.manifest, args.manifest_sha)
    result = Sequence(manifest, io).run()
    terminal = ROOT / 'runtime' / manifest['run_id'] / 'master' / 'controller-terminal.json'
    create_once(terminal, result)
    print(json.dumps(result, sort_keys=True))
    if result['status'] != 'CONTROL_SEQUENCE_COMPLETE_PENDING_ADMISSION':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
