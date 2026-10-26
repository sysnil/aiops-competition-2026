#!/usr/bin/env python3
"""One frozen 1800-second, six-chunk, no-fault SF-017 joint rehearsal."""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/root/ops-competition-2026/final-case-last-gate-v1-20261019')
FORMAL = Path('/root/ops-competition-2026/final-case-formal-entry-v1-20261019')
CAP = Path('/root/ops-competition-2026/low-stable-v4-20261017/capture')
DATA = Path('/data/stage5-v3/ops-competition-2026/rehearsals')
sys.path.insert(0, str(FORMAL / 'scripts'))
from control_contract import load_manifest
from safety_monitor import sample
from start_formal import commands
from audit_full_window import audit, sha
from archive_rehearsal import archive


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def read_optional(path):
    path = Path(path)
    return json.loads(path.read_text()) if path.is_file() else None


def create_once(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write('\n')
    path.chmod(0o600)


def verify_freeze(freeze_path, manifest_path, manifest_sha):
    freeze = json.loads(Path(freeze_path).read_text())
    if (freeze['status'] != 'FROZEN_HARMLESS_1800_CANDIDATE' or
            freeze['manifest_path'] != str(manifest_path) or
            freeze['manifest_sha256'] != manifest_sha or
            freeze['real_faults_injected'] != 0 or
            freeze['formal_attempt_created'] is not False):
        raise ValueError('NOFAULT_FREEZE_IDENTITY')
    for source, digest in freeze['source_sha256'].items():
        if sha(source) != digest:
            raise ValueError('NOFAULT_SOURCE_HASH_DRIFT:' + source)
    return freeze


def schedule(manifest):
    start = stamp(manifest['start_utc'])
    windows = {f'chunk-{index:02d}': {
        'start_utc': iso(start + index * 300),
        'end_utc': iso(start + (index + 1) * 300)}
        for index in range(6)}
    return {'run_id': manifest['run_id'], 'start_utc': manifest['start_utc'],
            'end_utc': manifest['window_end_utc'], 'duration_seconds': 1800,
            'windows': windows, 'faults_injected': 0}


def capture_commands(run, window):
    chunk = run / 'capture' / window['name']
    first = chunk / 'captures/during'
    middle = chunk / 'trace-completion/during'
    final = chunk / 'trace-final/during'
    late = chunk / 'trace-late-audit/during'
    start, end = window['start_utc'], window['end_utc']
    return {
        window['name'] + '-first': ['/usr/bin/python3', '-B', str(CAP / 'capture_live_c2.py'),
            '--phase', 'during', '--start-utc', start, '--end-utc', end,
            '--trace-source', 'JAEGER_OPENSEARCH_STORAGE', '--wait-until-end',
            '--output', str(first)],
        window['name'] + '-plus60': ['/usr/bin/python3', '-B',
            str(CAP / 'schedule_trace_completion_v2.py'), '--phase', 'during',
            '--start', start, '--end', end, '--first-capture', str(first),
            '--output', str(middle)],
        window['name'] + '-plus120-plus240': ['/usr/bin/python3', '-B',
            str(CAP / 'schedule_trace_v2.py'), '--phase', 'during',
            '--start', start, '--end', end, '--first', str(first),
            '--plus60', str(middle), '--output', str(final),
            '--audit-output', str(late)],
    }


def run(manifest_path, manifest_sha, freeze_path):
    manifest = load_manifest(manifest_path, manifest_sha)
    if (manifest['run_id'].split('-ATTEMPT-')[0] != 'SF-017' or
            manifest['mode'] != 'HARMLESS_REHEARSAL' or
            manifest.get('rehearsal_duration_seconds') != 1800):
        raise ValueError('FULL_WINDOW_CASE_SCOPE')
    verify_freeze(freeze_path, manifest_path, manifest_sha)
    start = stamp(manifest['start_utc'])
    if not 60 <= start - time.time() <= 180:
        raise ValueError('FULL_WINDOW_T0_NOT_FRESH')
    if sample(manifest)['status'] != 'DYNAMIC_STORAGE_AND_BASIC_HEALTH_PASS':
        raise RuntimeError('FULL_WINDOW_PREFLIGHT_BLOCKED')
    data = DATA / (manifest['run_id'] + '-full1800')
    data.mkdir(parents=True, mode=0o700, exist_ok=False)
    control = data / 'control'
    control.mkdir(mode=0o700)
    fixed = schedule(manifest)
    create_once(data / 'schedule.json', fixed)
    policy = json.loads(Path('/root/ops-competition-2026/sf013-formal-v4-20261019/design/workload-policy.json').read_text())
    if (policy['workload']['duration_seconds'] != 1800 or
            policy['workload']['profile'] != 'LOW'):
        raise ValueError('NOFAULT_WORKLOAD_POLICY_CHANGED')
    create_once(control / 'control-plan.json', {
        'id': manifest['run_id'], 'start_utc': manifest['start_utc'],
        'seed': manifest['workload_seed'], 'workload': policy['workload']})
    env = dict(os.environ, KUBECONFIG='/etc/kubernetes/admin.conf',
        A04_CAPTURE_ROOT=str(data),
        A04_BUDGET_LEDGER=str(control / 'json-byte-reservations'),
        A04_JSON_CAP_BYTES=str(24 * 1024**3))
    children = {}
    handles = []
    units = commands(manifest_path, manifest_sha, manifest)
    result = {'status': 'FULL_1800_NOFAULT_FAILED', 'run_id': manifest['run_id'],
              'manifest_sha256': manifest_sha, 'freeze_sha256': sha(freeze_path),
              'run_dir': str(data), 'faults_injected': 0,
              'formal_attempt_created': False, 'started_utc': iso(time.time())}

    def launch(name, argv):
        out = (control / (name + '.stdout')).open('x')
        err = (control / (name + '.stderr')).open('x')
        handles.extend((out, err))
        children[name] = subprocess.Popen(argv, stdout=out, stderr=err, env=env)

    def stop_units():
        subprocess.run(['systemctl', 'stop', manifest['controller_unit'] + '.service'],
                       capture_output=True, timeout=20)
        guard_terminal = (FORMAL / 'runtime' / manifest['run_id'] /
                          'master/guard-terminal.json')
        deadline = time.monotonic() + 25
        while not guard_terminal.is_file() and time.monotonic() < deadline:
            time.sleep(.25)
        if guard_terminal.is_file() and json.loads(guard_terminal.read_text()).get(
                'status') == 'GUARD_CLEANUP_VERIFIED':
            subprocess.run(['systemctl', 'stop', manifest['guard_unit'] + '.service'],
                           capture_output=True, timeout=20)
            return True
        return False

    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, lambda code, frame: (_ for _ in ()).throw(SystemExit(128 + code)))
    try:
        launch('observer', ['/usr/bin/python3', '-B',
            str(ROOT / 'scripts/rehearsal_observer.py'), '--manifest', str(manifest_path),
            '--sha256', manifest_sha, '--output', str(data / 'observer')])
        launch('controls', ['/usr/bin/python3', '-B',
            '/root/ops-competition-2026/low-stable-v4-20261017/formal-v2/fixed_business_streams_plain.py',
            '--plan', str(control / 'control-plan.json'), '--seed',
            str(manifest['workload_seed']), '--duration', '1800',
            '--start-utc', manifest['start_utc'], '--base-url',
            'http://10.104.177.128:8080', '--output', str(data / 'controls')])
        subprocess.run(units['guard'], check=True, capture_output=True, timeout=15)
        subprocess.run(units['controller'], check=True, capture_output=True, timeout=15)
        launched = set()
        watchdog_launched = False
        deadline = start + 1800 + 330
        next_note = time.time() + 60
        while time.time() <= deadline:
            now = time.time()
            for index, (name, window) in enumerate(fixed['windows'].items()):
                if name not in launched and now >= start + index * 300 + 200:
                    for task, argv in capture_commands(data, {'name': name, **window}).items():
                        launch(task, argv)
                    launched.add(name)
            if not watchdog_launched and now >= start + 900:
                launch('sacrificial-watchdog', ['ssh', '-S', '/run/ops-final-ssh/w3.sock',
                    '-o', 'BatchMode=yes', 'root@192.168.53.55',
                    '/usr/bin/python3 -B ' + str(ROOT / 'scripts/sacrificial_watchdog.py') +
                    ' run --run-id ' + manifest['run_id']])
                watchdog_launched = True
            for name, child in children.items():
                code = child.poll()
                if code is not None and code != 0:
                    raise RuntimeError('NOFAULT_CHILD_FAILED:' + name + ':' + str(code))
            terminal = read_optional(FORMAL / 'runtime' / manifest['run_id'] /
                                     'master/controller-terminal.json')
            if terminal and terminal['status'] != 'CONTROL_SEQUENCE_COMPLETE_PENDING_ADMISSION':
                raise RuntimeError('NOFAULT_CONTROLLER_TERMINAL_FAILED')
            if now >= next_note:
                print(json.dumps({'status': 'NOFAULT_RUNNING', 'run_id': manifest['run_id'],
                                  'elapsed_seconds': round(now - start),
                                  'capture_chunks_started': len(launched)}), flush=True)
                next_note += 60
            if len(launched) == 6 and watchdog_launched and all(
                    child.poll() == 0 for child in children.values()):
                break
            time.sleep(.5)
        else:
            raise TimeoutError('NOFAULT_CAPTURE_AUDIT_DEADLINE')
        for child in children.values():
            child.wait(timeout=3)
        master_control = FORMAL / 'runtime' / manifest['run_id'] / 'master'
        if not (master_control / 'controller-terminal.json').is_file():
            raise RuntimeError('NOFAULT_CONTROLLER_TERMINAL_MISSING')
        if not (master_control / 'guard-terminal.json').is_file():
            raise RuntimeError('NOFAULT_GUARD_TERMINAL_MISSING')
        create_once(control / 'controller-terminal.json',
                    json.loads((master_control / 'controller-terminal.json').read_text()))
        create_once(control / 'guard-terminal.json',
                    json.loads((master_control / 'guard-terminal.json').read_text()))
        proof_path = (ROOT / 'runtime' / manifest['run_id'] /
                      'sacrificial-watchdog-result.json')
        response = subprocess.check_output(['ssh', '-S', '/run/ops-final-ssh/w3.sock',
            '-o', 'BatchMode=yes', 'root@192.168.53.55',
            'cat ' + str(proof_path)], text=True, timeout=15)
        create_once(control / 'watchdog-result.json', json.loads(response))
        quality = audit(data, freeze_path)
        create_once(data / 'quality-audit.json', quality)
        result.update(status='FULL_1800_NOFAULT_CAPTURE_PASS',
                      quality=quality, completed_utc=iso(time.time()))
        archived = archive(data)
        result.update(status='FULL_1800_NOFAULT_ARCHIVE_PASS', archive=archived)
    except BaseException as error:
        result.update(status='FULL_1800_NOFAULT_ARCHIVE_FAILED'
                      if result['status'] == 'FULL_1800_NOFAULT_CAPTURE_PASS'
                      else 'FULL_1800_NOFAULT_FAILED',
                      error=type(error).__name__ + ':' + str(error),
                      completed_utc=iso(time.time()))
    finally:
        if not stop_units():
            result['status'] = 'FULL_1800_NOFAULT_CLEANUP_UNVERIFIED'
            result['cleanup_error'] = 'Guard terminal absent or not verified; Guard left running'
        for child in children.values():
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=10)
        for handle in handles:
            handle.close()
        create_once(data / 'rehearsal-terminal.json', result)
        print(json.dumps({'status': result['status'], 'run_id': manifest['run_id'],
                          'error': result.get('error')}), flush=True)
    if result['status'] != 'FULL_1800_NOFAULT_ARCHIVE_PASS':
        raise SystemExit(1)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--manifest-sha', required=True)
    parser.add_argument('--freeze', type=Path, required=True)
    args = parser.parse_args()
    run(args.manifest, args.manifest_sha, args.freeze)


if __name__ == '__main__':
    main()
