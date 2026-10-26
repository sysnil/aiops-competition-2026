#!/usr/bin/env python3
"""Launch one full harmless Controller/Guard/Timer chain on Master."""

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
ROOT = '/root/ops-competition-2026/final-case-formal-entry-v6-20261020'
SHARED = '/root/ops-competition-2026/final-case-unified-execution-v2-20261019/scripts'
SOCKET = os.environ.get('SF_MASTER_SOCK', '/tmp/sf-unified-master.sock')
WORKER_SOCKETS = {
    'k8s-worker2': (os.environ.get('SF_WORKER2_SOCK', '/tmp/sf-unified-w2.sock'),
                    '192.168.53.54'),
    'k8s-worker3': (os.environ.get('SF_WORKER3_SOCK', '/tmp/sf-unified-w3.sock'),
                    '192.168.53.55')}
CASES = {
    'SF-013': {'POD': ('k8s-worker2', 'PodMemoryStress', 300)},
    'SF-014': {'SERVICE': ('k8s-worker3', 'ServicePause', 300)},
    'SF-015': {'HTTP': ('k8s-master', 'HTTPReqDelay', 300)},
    'SF-016': {'POD': ('k8s-worker2', 'PodMemoryStress', 300)},
    'SF-017': {'HTTP': ('k8s-master', 'HTTPReqDelay', 240),
               'SERVICE': ('k8s-worker3', 'ServicePause', 240)},
}


def ssh(host, args, *, check=True):
    if host == 'k8s-master':
        sock, ip = SOCKET, '192.168.53.52'
    else:
        sock, ip = WORKER_SOCKETS[host]
    command = shlex.join(map(str, args))
    result = subprocess.run(['ssh', '-S', sock, '-o', 'BatchMode=yes',
                           'root@' + ip, command], capture_output=True,
                          text=True, timeout=30)
    if check and result.returncode:
        raise RuntimeError(host + ':' + result.stderr[-1200:] + result.stdout[-500:])
    return result


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def source_hashes():
    names = ['control_contract.py', 'worker_fault_entry.py',
             'timer_lifecycle.py', 'qualified_effect.py', 'http_workload.py',
             'formal_controller.py', 'formal_guard.py', 'start_formal.py',
             'safety_monitor.py']
    shared = ['action_decision.py', 'auth_protocol.py', 'entry_policy.py',
              'http_gate.py', 'worker_auth.py',
              'worker_entry.py']
    base = PROJECT.parent / 'final-case-unified-execution-v2-20261019' / 'scripts'
    auth = PROJECT.parent / 'final-case-unified-execution-v1-20261019' / 'scripts'
    auth_root = '/root/ops-competition-2026/final-case-unified-execution-v1-20261019/scripts'
    adapter = PROJECT.parent / 'sf013-adapter-v5-20261019' / 'scripts/qualified_adapter.py'
    adapter_root = '/root/ops-competition-2026/sf013-adapter-v5-20261019/scripts/qualified_adapter.py'
    return {**{ROOT + '/scripts/' + name:
                hashlib.sha256((PROJECT / 'scripts' / name).read_bytes()).hexdigest()
                for name in names},
            **{SHARED + '/' + name:
                hashlib.sha256((base / name).read_bytes()).hexdigest()
                for name in shared},
            **{auth_root + '/' + name:
                hashlib.sha256((auth / name).read_bytes()).hexdigest()
                for name in ('master_authorizer.py', 'auth_protocol.py',
                             'action_decision.py')},
            adapter_root: hashlib.sha256(adapter.read_bytes()).hexdigest()}


def create_manifest(case):
    token = uuid.uuid4().hex[:8].upper()
    run = case + '-ATTEMPT-MARKERTEST-' + token
    start = time.time() + 10
    actions = []
    for index, (kind, (worker, mechanism, duration)) in enumerate(CASES[case].items()):
        ident = run + '-' + kind
        action = {'run_id': run, 'action_id': ident, 'kind': kind,
                  'worker': worker, 'mechanism': mechanism,
                  'duration_seconds': duration, 'capability': 'MARKER_ONLY',
                  'effect': 'MARKER', 'entrypoint': SHARED + '/marker_cli.py',
                  'timer_command': ROOT + '/scripts/' +
                                   ('http_workload.py' if kind == 'HTTP' else
                                    'worker_fault_entry.py'),
                  'planned_start_utc': iso(start + 20 + index * 360),
                  'runtime': ROOT + '/runtime/' + run + '/' + ident}
        if worker != 'k8s-master':
            action['unit'] = 'ops-' + case.lower().replace('-', '') + '-' + ident.lower()
        actions.append(action)
    controller = 'ops-' + case.lower().replace('-', '') + '-' + run.lower() + '-controller'
    manifest = {'schema': 'FINAL_CASE_UNIFIED_ENTRY_V1', 'mode': 'HARMLESS_REHEARSAL',
                'run_id': run, 'actions': actions,
                'controller_unit': controller, 'guard_unit': controller + '-guard',
                'monitor_unit': controller + '-monitor',
                'workload_seed': 31, 'start_utc': iso(start),
                'safety': 'NOFAULT_BASIC_HEALTH_PASS',
                'source_sha256': source_hashes()}
    if case == 'SF-017':
        manifest['rehearsal_duration_seconds'] = 1800
        manifest['window_end_utc'] = iso(start + 1800)
        manifest['rehearsal_data_root'] = (
            '/data/stage5-v3/ops-competition-2026/rehearsals/' + run + '-full1800')
    return manifest


def remote_json(host, path):
    cmd = ['python3', '-c', 'import pathlib; p=pathlib.Path(' + repr(str(path)) +
           '); print(p.read_text() if p.is_file() else "null")']
    return json.loads(ssh(host, cmd).stdout)


def run_case(case, *, abort_before_first=False):
    manifest = create_manifest(case)
    run = manifest['run_id']
    remote_dir = ROOT + '/rehearsal/' + run
    remote_path = remote_dir + '/manifest.json'
    ssh('k8s-master', ['mkdir', '-p', '-m', '700', remote_dir])
    with tempfile.NamedTemporaryFile('w', delete=False) as stream:
        json.dump(manifest, stream, sort_keys=True)
        stream.write('\n')
        local = Path(stream.name)
    try:
        digest = hashlib.sha256(local.read_bytes()).hexdigest()
        subprocess.run(['rsync', '-a', '-e', 'ssh -S ' + SOCKET + ' -o BatchMode=yes',
            str(local), 'root@192.168.53.52:' + remote_path], check=True,
            capture_output=True, timeout=30)
    finally:
        local.unlink()
    validation = ssh('k8s-master', ['/usr/bin/python3', '-B',
        ROOT + '/scripts/start_formal.py', '--manifest', remote_path,
        '--manifest-sha', digest, '--validate-only'])
    validated = json.loads(validation.stdout)
    if validated['status'] != 'COMMANDS_VALIDATED_NO_UNITS_CREATED':
        raise RuntimeError('NO_FAULT_LAUNCH_VALIDATION_FAILED')
    commands = validated['commands']
    try:
        ssh('k8s-master', commands['guard'])
        ssh('k8s-master', commands['controller'])
        if abort_before_first:
            time.sleep(4)
            ssh('k8s-master', ['systemctl', 'stop',
                manifest['controller_unit'] + '.service'])
            guard_path = ROOT + '/runtime/' + run + '/master/guard-terminal.json'
            guard = None
            guard_deadline = time.time() + 20
            while time.time() < guard_deadline:
                guard = remote_json('k8s-master', guard_path)
                if guard is not None:
                    break
                time.sleep(.25)
            trigger = max(datetime.fromisoformat(a['planned_start_utc']).timestamp()
                          for a in manifest['actions'])
            time.sleep(max(0, trigger - time.time() + 3))
            actions = {}
            for action in manifest['actions']:
                path = ROOT + '/runtime/' + run + '/' + action['action_id']
                observed = (remote_json(action['worker'], path + '/fired.json')
                    if action['worker'] != 'k8s-master' else
                    remote_json('k8s-master', path + '/http-terminal.json'))
                actions[action['kind']] = {
                    'fired_after_abort': observed if action['worker'] != 'k8s-master'
                        or (observed and observed.get('fault_headers_sent', 0) > 0)
                        else None,
                    'terminal_after_abort': observed,
                    'revoked': remote_json(action['worker'], path + '/REVOKED.json')}
            return {'status': 'HARMLESS_ABORT_REHEARSAL', 'case': case,
                    'run_id': run, 'manifest_sha256': digest,
                    'controller': remote_json('k8s-master', ROOT + '/runtime/' + run +
                                              '/master/controller-terminal.json'),
                    'guard': guard, 'actions': actions,
                    'real_faults_injected': 0}
        deadline = max(datetime.fromisoformat(a['planned_start_utc']).timestamp() +
                       (a['duration_seconds'] if a['kind'] == 'HTTP' else 4)
                       for a in manifest['actions']) + 15
        terminal_path = ROOT + '/runtime/' + run + '/master/controller-terminal.json'
        while time.time() < deadline:
            terminal = remote_json('k8s-master', terminal_path)
            if terminal is not None:
                break
            time.sleep(.5)
        else:
            terminal = None
        guard_path = ROOT + '/runtime/' + run + '/master/guard-terminal.json'
        guard = None
        guard_deadline = time.time() + 12
        while time.time() < guard_deadline:
            guard = remote_json('k8s-master', guard_path)
            if guard is not None:
                break
            time.sleep(.25)
        actions = {}
        for action in manifest['actions']:
            path = ROOT + '/runtime/' + run + '/' + action['action_id']
            actions[action['kind']] = {
                'worker_terminal': remote_json(action['worker'], path + '/terminal.json')
                    if action['worker'] != 'k8s-master' else
                    remote_json('k8s-master', path + '/http-terminal.json'),
                'revoke': remote_json(action['worker'], path + '/REVOKED.json')}
        result = {'status': 'HARMLESS_CONTROL_REHEARSAL', 'case': case,
                  'run_id': run, 'manifest_sha256': digest,
                  'controller': terminal, 'guard': guard,
                  'actions': actions, 'real_faults_injected': 0}
        return result
    finally:
        for unit in (manifest['controller_unit'], manifest['guard_unit']):
            ssh('k8s-master', ['systemctl', 'stop', unit + '.service'], check=False)
        for action in manifest['actions']:
            if action['worker'] != 'k8s-master':
                for suffix in ('.timer', '.service'):
                    ssh(action['worker'], ['systemctl', 'stop', action['unit'] + suffix],
                        check=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--case', choices=tuple(CASES), required=True)
    parser.add_argument('--abort-before-first', action='store_true')
    args = parser.parse_args()
    result = run_case(args.case, abort_before_first=args.abort_before_first)
    label = '-abort' if args.abort_before_first else ''
    out = PROJECT / 'results' / (args.case.lower() + label + '-harmless-control.json')
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.abort_before_first:
        okay = (result['guard'] is not None and result['guard']['status'] ==
                'GUARD_CLEANUP_VERIFIED' and
                all(row['fired_after_abort'] is None and row['revoked'] is not None
                    for row in result['actions'].values()))
    else:
        okay = (result['controller'] is not None and result['controller']['status'] ==
                'CONTROL_SEQUENCE_COMPLETE_PENDING_ADMISSION' and
                result['guard'] is not None and result['guard']['status'] ==
                'GUARD_CLEANUP_VERIFIED')
    if not okay:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
