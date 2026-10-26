#!/usr/bin/env python3
"""Bind formal v6 control, six captures, observer and admission preparation."""

import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
ROOT = OPS / 'final-case-production-v4-20261020'
FORMAL = OPS / 'final-case-formal-entry-v7-20261020'
OBSERVATION = OPS / 'final-case-observation-v2-20261020'
DATA = Path('/data/stage5-v3/ops-competition-2026/runs')
BASE = 'http://10.104.177.128:8080'
if not FORMAL.is_dir():
    FORMAL = Path(__file__).resolve().parents[2] / 'final-case-formal-entry-v7-20261020'
if not OBSERVATION.is_dir():
    OBSERVATION = Path(__file__).resolve().parents[2] / 'final-case-observation-v2-20261020'
sys.path.insert(0, str(FORMAL / 'scripts'))
sys.path.insert(0, str(OBSERVATION / 'scripts'))
from control_contract import load_manifest, sha
from safety_monitor import sample as safety_sample
from assess_collection import assess, collect
from capture_commands import capture_commands


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def utc(epoch=None):
    return datetime.fromtimestamp(time.time() if epoch is None else epoch,
                                  timezone.utc).isoformat()


def save(path, value):
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write('\n')
    path.chmod(0o600)


def read_optional(path):
    return json.loads(path.read_text()) if path.is_file() else None


def schedule(manifest):
    start = stamp(manifest['start_utc'])
    return {'run_id': manifest['run_id'], 'start_utc': manifest['start_utc'],
            'end_utc': manifest['window_end_utc'], 'duration_seconds': 1800,
            'windows': {f'chunk-{index:02d}': {
                'start_utc': utc(start + index * 300),
                'end_utc': utc(start + (index + 1) * 300)}
                for index in range(6)},
            'formal_case': True,
            'trace_source': 'JAEGER_OPENSEARCH_STORAGE'}


def verify_freeze(path, manifest_path, digest):
    value = json.loads(path.read_text())
    if (value.get('status') != 'FROZEN_FORMAL_CANDIDATE' or
            value.get('manifest_path') != str(manifest_path) or
            value.get('manifest_sha256') != digest or
            value.get('trace_source') != 'JAEGER_OPENSEARCH_STORAGE'):
        raise ValueError('FORMAL_FREEZE_IDENTITY_MISMATCH')
    for source, expected in value['source_sha256'].items():
        if sha(source) != expected:
            raise ValueError('FORMAL_SOURCE_DRIFT:' + source)
    return value


def worker_command(action, argv):
    channels = {
        'k8s-worker2': ('/run/ops-final-ssh/w2.sock', '192.168.53.54'),
        'k8s-worker3': ('/run/ops-final-ssh/w3.sock', '192.168.53.55'),
    }
    socket, host = channels[action['worker']]
    result = subprocess.run(['ssh', '-S', socket, '-o', 'BatchMode=yes',
        '-o', 'ConnectTimeout=3', 'root@' + host, shlex.join(map(str, argv))],
        capture_output=True, text=True, timeout=25)
    if result.returncode:
        raise RuntimeError('FORMAL_WORKER_READ_OR_CLEANUP_FAILED:' + host)
    return result.stdout


def action_result(action, digest):
    root = FORMAL / 'runtime' / action['run_id'] / action['action_id']
    if action['worker'] == 'k8s-master':
        value = json.loads((root / 'http-terminal.json').read_text())
        if (value['status'] != 'HTTP_ACTION_COMPLETE' or
                value['authorized_headers'] < action['min_fault_requests']):
            raise RuntimeError('FORMAL_HTTP_EFFECT_NOT_VERIFIED')
        return value
    raw = worker_command(action, ['python3', '-c',
        'import pathlib; print(pathlib.Path(' + repr(str(root / 'terminal.json')) +
        ').read_text())'])
    value = json.loads(raw)
    effect = value.get('effect', {})
    planned = stamp(action['planned_start_utc'])
    if (value.get('status') != 'ACTION_TERMINAL' or
            effect.get('status') !=
            'COMPETITION_BOUNDED_INJECTION_COMPLETED_NOT_YET_QUALIFIED' or
            effect.get('cleanup', {}).get('status') !=
            'COMPETITION_CLEANUP_ACTIONS_VERIFIED' or
            abs(stamp(effect['actual_start_utc']) - planned) > 2 or
            abs(effect['duration_seconds'] - action['duration_seconds']) > 2):
        raise RuntimeError('FORMAL_WORKER_EFFECT_OR_RECOVERY_NOT_VERIFIED')
    return value


def checked_start(actual, planned, *, tolerance=2):
    offset = stamp(actual) - stamp(planned)
    if abs(offset) > tolerance:
        raise RuntimeError('FORMAL_ACTION_START_DEVIATION:' + str(offset))
    return offset


def current_action_start(action):
    if action['worker'] == 'k8s-master':
        runtime = FORMAL / 'runtime' / action['run_id'] / action['action_id']
        events = runtime / 'header-events.jsonl'
        if not events.is_file():
            raise RuntimeError('FORMAL_HTTP_HEADER_AUDIT_MISSING_AT_START')
        for line in events.read_text().splitlines():
            item = json.loads(line)
            if item.get('status') == 'AUTHORIZED_HEADER':
                return item['at_utc'], 'FIRST_AUTHORIZED_HOME_REQUEST'
        raise RuntimeError('FORMAL_HTTP_HEADER_NOT_APPLIED_PROMPTLY')
    state = (OPS / 'qualification/private-runtime' /
             (action['action_id'] + '.json'))
    raw = worker_command(action, ['python3', '-c',
        'import pathlib; p=pathlib.Path(' + repr(str(state)) +
        '); print(p.read_text() if p.is_file() else "null")'])
    value = json.loads(raw)
    if not value or value.get('status') != 'ACTIVE' or \
            not value.get('actual_start_utc'):
        raise RuntimeError('FORMAL_WORKER_EFFECT_NOT_ACTIVE_PROMPTLY')
    return value['actual_start_utc'], 'WORKER_ADAPTER_EFFECT_START'


def dynamic_safety(manifest, *, sampler=safety_sample):
    result = sampler(manifest)
    if result['status'] != 'DYNAMIC_STORAGE_AND_BASIC_HEALTH_PASS':
        raise RuntimeError('FORMAL_DYNAMIC_SAFETY_BLOCKED')
    return result


def run(manifest_path, digest, freeze_path):
    manifest = load_manifest(manifest_path, digest, allow_rehearsal=False)
    if manifest['mode'] != 'FORMAL_CANDIDATE':
        raise ValueError('FORMAL_PRODUCTION_MODE_REQUIRED')
    verify_freeze(freeze_path, manifest_path, digest)
    t0 = stamp(manifest['start_utc'])
    if not 60 <= t0 - time.time() <= 180:
        raise ValueError('FORMAL_CAPTURE_T0_NOT_FRESH')
    dynamic_safety(manifest)
    data = DATA / manifest['run_id']
    data.mkdir(parents=True, mode=0o700, exist_ok=False)
    control = data / 'control'
    control.mkdir(mode=0o700)
    fixed = schedule(manifest)
    save(data / 'schedule.json', fixed)
    save(control / 'freeze-reference.json', {
        'manifest_path': str(manifest_path), 'manifest_sha256': digest,
        'freeze_path': str(freeze_path), 'freeze_sha256': sha(freeze_path)})
    policy = json.loads((OPS / 'sf013-formal-v4-20261019/design/workload-policy.json').read_text())
    if (policy['workload']['duration_seconds'] != 1800 or
            policy['workload']['profile'] != 'LOW'):
        raise ValueError('FORMAL_WORKLOAD_POLICY_CHANGED')
    save(control / 'control-plan.json', {'id': manifest['run_id'],
        'start_utc': manifest['start_utc'], 'seed': manifest['workload_seed'],
        'workload': policy['workload']})
    env = dict(os.environ, KUBECONFIG='/etc/kubernetes/admin.conf',
        A04_CAPTURE_ROOT=str(data),
        A04_BUDGET_LEDGER=str(control / 'json-byte-reservations'),
        A04_JSON_CAP_BYTES=str(24 * 1024**3))
    children = {}
    handles = []
    controller_started = False
    result = {'status': 'FORMAL_CAPTURE_FAILED', 'run_id': manifest['run_id'],
              'manifest_sha256': digest, 'freeze_sha256': sha(freeze_path),
              'started_utc': utc(), 'data_root': str(data),
              'public_created': False, 'private_gt_created': False}

    def launch(name, argv):
        stdout = (control / (name + '.stdout')).open('x')
        stderr = (control / (name + '.stderr')).open('x')
        handles.extend((stdout, stderr))
        children[name] = subprocess.Popen(argv, stdout=stdout, stderr=stderr,
                                          env=env)

    def stop_controller():
        auth_root = (OPS / 'final-case-unified-execution-v1-20261019/runtime' /
                     manifest['run_id'])
        if not controller_started and not auth_root.exists():
            return True
        subprocess.run(['systemctl', 'stop', manifest['controller_unit'] + '.service'],
                       capture_output=True, timeout=25)
        terminal = (FORMAL / 'runtime' / manifest['run_id'] /
                    'master/guard-terminal.json')
        deadline = time.monotonic() + 30
        while not terminal.exists() and time.monotonic() < deadline:
            time.sleep(.25)
        status = read_optional(terminal)
        if status and status.get('status') == 'GUARD_CLEANUP_VERIFIED':
            subprocess.run(['systemctl', 'stop', manifest['guard_unit'] + '.service'],
                           capture_output=True, timeout=20)
            return True
        emergency = {}
        for action in manifest['actions']:
            if action['worker'] == 'k8s-master':
                continue
            try:
                raw = worker_command(action, ['/usr/bin/python3', '-B',
                    FORMAL / 'scripts/worker_fault_entry.py',
                    'emergency-cancel', '--manifest', manifest_path,
                    '--manifest-sha', digest, '--action-id', action['action_id']])
                emergency[action['action_id']] = json.loads(raw.splitlines()[-1])
            except BaseException as error:
                emergency[action['action_id']] = {
                    'status': 'EMERGENCY_CANCEL_UNVERIFIED',
                    'error': type(error).__name__ + ':' + str(error)}
        save(control / 'emergency-cancel.json', emergency)
        return False

    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, lambda code, frame: (_ for _ in ()).throw(SystemExit(128 + code)))
    try:
        launch('observer', ['/usr/bin/python3', '-B',
            str(ROOT / 'scripts/production_observer.py'), '--manifest',
            str(manifest_path), '--sha256', digest,
            '--output', str(data / 'observer')])
        launch('controls', ['/usr/bin/python3', '-B',
            str(OPS / 'low-stable-v4-20261017/formal-v2/fixed_business_streams_plain.py'),
            '--plan', str(control / 'control-plan.json'), '--seed',
            str(manifest['workload_seed']), '--duration', '1800',
            '--start-utc', manifest['start_utc'], '--base-url', BASE,
            '--output', str(data / 'controls')])
        if not any(action['kind'] == 'HTTP' for action in manifest['actions']):
            launch('state-machine', ['/usr/bin/python3', '-B',
                str(ROOT / 'scripts/normal_workload.py'),
                '--seed', str(manifest['workload_seed']),
                '--start-utc', manifest['start_utc'],
                '--output', str(data / 'state-machine')])
        launch_command = ['/usr/bin/python3', '-B',
            str(FORMAL / 'scripts/start_formal.py'), '--manifest',
            str(manifest_path), '--manifest-sha', digest]
        launch_result = subprocess.run(launch_command, capture_output=True,
                                       text=True, timeout=25, env=env)
        (control / 'start-formal.stdout').write_text(launch_result.stdout)
        (control / 'start-formal.stderr').write_text(launch_result.stderr)
        if launch_result.returncode:
            raise RuntimeError('FORMAL_START_REJECTED:' +
                               launch_result.stderr[-1200:])
        controller_started = True
        launched = set()
        start_checked = set()
        deadline = t0 + 1800 + 330
        next_note = time.time() + 60
        next_safety = time.time()
        while time.time() <= deadline:
            now = time.time()
            if now >= next_safety:
                state = dynamic_safety(manifest)
                with (control / 'dynamic-safety.jsonl').open('a') as stream:
                    stream.write(json.dumps(state, sort_keys=True) + '\n')
                next_safety += 15
            for index, (name, window) in enumerate(fixed['windows'].items()):
                if name not in launched and now >= t0 + index * 300 + 200:
                    for task, argv in capture_commands(data, {'name': name,
                                                               **window}).items():
                        launch(task, argv)
                    launched.add(name)
            for name, child in children.items():
                code = child.poll()
                if code is not None and code != 0:
                    raise RuntimeError('FORMAL_CHILD_FAILED:' + name + ':' + str(code))
            for action in manifest['actions']:
                ident = action['action_id']
                if (ident not in start_checked and now >=
                        stamp(action['planned_start_utc']) + 5):
                    actual, source = current_action_start(action)
                    offset = checked_start(actual, action['planned_start_utc'])
                    save(control / (ident + '-start-check.json'), {
                        'status': 'FORMAL_ACTION_START_WITHIN_TWO_SECONDS',
                        'action_id': ident, 'planned_start_utc':
                            action['planned_start_utc'],
                        'actual_start_utc': actual,
                        'offset_seconds': offset,
                        'evidence_source': source,
                        'checked_utc': utc()})
                    start_checked.add(ident)
            master = FORMAL / 'runtime' / manifest['run_id'] / 'master'
            terminal = read_optional(master / 'controller-terminal.json')
            if terminal and terminal['status'] != \
                    'CONTROL_SEQUENCE_COMPLETE_PENDING_ADMISSION':
                raise RuntimeError('FORMAL_CONTROLLER_TERMINAL_FAILED')
            if now >= next_note:
                print(json.dumps({'status': 'FORMAL_CAPTURE_RUNNING',
                    'run_id': manifest['run_id'],
                    'elapsed_seconds': int(now - t0),
                    'capture_chunks_started': len(launched)}), flush=True)
                next_note += 60
            if (len(launched) == 6 and terminal and
                    all(child.poll() == 0 for child in children.values())):
                break
            time.sleep(.5)
        else:
            raise TimeoutError('FORMAL_CAPTURE_AUDIT_DEADLINE')
        master = FORMAL / 'runtime' / manifest['run_id'] / 'master'
        controller = json.loads((master / 'controller-terminal.json').read_text())
        guard = json.loads((master / 'guard-terminal.json').read_text())
        if (controller['status'] != 'CONTROL_SEQUENCE_COMPLETE_PENDING_ADMISSION' or
                guard['status'] != 'GUARD_CLEANUP_VERIFIED' or
                read_optional(data / 'observer/terminal.json')['status'] !=
                'OBSERVER_COMPLETE'):
            raise RuntimeError('FORMAL_CONTROL_OBSERVER_OR_GUARD_FAILED')
        save(control / 'controller-terminal.json', controller)
        save(control / 'guard-terminal.json', guard)
        effects = {action['action_id']: action_result(action, digest)
                   for action in manifest['actions']}
        save(control / 'action-results.json', effects)
        residue = {}
        for action in manifest['actions']:
            if action['worker'] == 'k8s-master':
                continue
            raw = worker_command(action, ['systemctl', 'show',
                action['unit'] + '.timer', action['unit'] + '.service',
                '--property=ActiveState', '--no-pager'])
            states = [line.partition('=')[2] for line in raw.splitlines()
                      if line.startswith('ActiveState=')]
            if len(states) != 2 or any(state in
                    ('active', 'activating', 'reloading') for state in states):
                raise RuntimeError('FORMAL_WORKER_TIMER_OR_SERVICE_RESIDUE')
            residue[action['action_id']] = states
        save(control / 'worker-residue.json', residue)
        final_health = dynamic_safety(manifest)
        save(control / 'final-health.json', final_health)
        contract = json.loads((OBSERVATION / 'design/collection-contract-draft.json').read_text())
        quality = assess(collect(data, contract), contract)
        save(data / 'quality-audit.json', quality)
        if quality['status'] != 'DATA_COLLECTION_ACCEPTED_FOR_ADMISSION':
            raise RuntimeError('FORMAL_COLLECTION_GATE_BLOCKED')
        verify_freeze(freeze_path, manifest_path, digest)
        result.update(status='FORMAL_CAPTURE_COMPLETE_PENDING_ADMISSION',
                      quality_status=quality['status'],
                      action_count=len(effects), completed_utc=utc())
    except BaseException as error:
        result.update(error=type(error).__name__ + ':' + str(error),
                      completed_utc=utc())
    finally:
        if not stop_controller():
            result['status'] = 'FORMAL_CLEANUP_UNVERIFIED'
            result['cleanup_error'] = 'Guard did not confirm exact action cleanup'
        for child in children.values():
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
        for handle in handles:
            handle.close()
        save(data / 'capture-terminal.json', result)
        print(json.dumps({'status': result['status'],
            'run_id': manifest['run_id'], 'error': result.get('error')}), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--manifest-sha', required=True)
    parser.add_argument('--freeze', required=True, type=Path)
    args = parser.parse_args()
    result = run(args.manifest, args.manifest_sha, args.freeze)
    if result['status'] != 'FORMAL_CAPTURE_COMPLETE_PENDING_ADMISSION':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
