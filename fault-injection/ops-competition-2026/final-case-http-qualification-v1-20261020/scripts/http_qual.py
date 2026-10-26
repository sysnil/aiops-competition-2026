#!/usr/bin/env python3
"""Independent real HTTP delay qualification through the production request gate."""

import argparse
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import types
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
ROOT = OPS / 'final-case-http-qualification-v1-20261020'
FORMAL = OPS / 'final-case-formal-entry-v7-20261020/scripts'
SHARED = OPS / 'final-case-unified-execution-v2-20261019/scripts'
QUAL = OPS / 'final-case-mechanism-qualification-v2-20261020/scripts'
DATA = Path('/data/stage5-v3/ops-competition-2026/qualification-final-http')
CONTROLS = OPS / 'low-stable-v4-20261017/formal-v2/fixed_business_streams_plain.py'
POLICY = OPS / 'sf013-formal-v4-20261019/design/workload-policy.json'
BASE = 'http://10.104.177.128:8080'
sys.path.insert(0, str(FORMAL))
sys.path.insert(0, str(SHARED))
sys.path.insert(0, str(QUAL))
from action_decision import create_once, permit, revoke
from entry_policy import validate_action
from http_workload import (load_real_business_module, register_requester,
                           request_factory, start_ticks)
from safety_monitor import sample as safety_sample
from run_qualification import (capture_commands, collector_counters,
                               refused_delta, sha, utc, stamp, save)


CASES = {'SF-015': 300, 'SF-017': 240}


def load_plan(path, expected_sha):
    if path.is_symlink() or sha(path) != expected_sha:
        raise ValueError('HTTP_QUALIFICATION_PLAN_HASH')
    plan = json.loads(path.read_text())
    run = plan['run_id']
    case = run.split('-ATTEMPT-', 1)[0]
    if (case not in CASES or not run.startswith(case + '-ATTEMPT-QUALTEST-') or
            plan['action']['action_id'] != run + '-HTTP' or
            plan['action']['duration_seconds'] != CASES[case] or
            plan['action']['worker'] != 'k8s-master' or
            plan['action']['mechanism'] != 'HTTPReqDelay' or
            plan['action']['entrypoint'] != str(SHARED / 'http_gate.py') or
            plan['action']['capability'] != 'BOUND_QUALIFIED_FAULT' or
            plan['data_root'] != str(DATA / run)):
        raise ValueError('HTTP_QUALIFICATION_SCOPE')
    action = dict(plan['action'], manifest_sha256=expected_sha)
    validate_action(action)
    if plan.get('source_sha256') != source_lock():
        raise ValueError('HTTP_QUALIFICATION_SOURCE_SET_MISMATCH')
    for source, digest in plan['source_sha256'].items():
        if sha(source) != digest:
            raise ValueError('HTTP_QUALIFICATION_SOURCE_DRIFT:' + source)
    return plan, action


def source_lock():
    paths = [Path(__file__), FORMAL / 'http_workload.py',
             SHARED / 'http_gate.py', SHARED / 'action_decision.py',
             QUAL / 'run_qualification.py',
             ROOT / 'scripts/assess_http_qual.py',
             CONTROLS, POLICY,
             OPS / 'low-stable-v4-20261017/design/load-profiles-v4b.yaml',
             Path('/root/cross-layer-observability-control-rehearsal/scripts/workload_generator_rehearsal.py'),
             Path('/root/stage5-v3-observability-diagnostics/snapshots/reference/workload-state-machine-c3.yaml'),
             Path('/root/cross-layer-workload-generation/design/people.json'),
             *sorted((OPS / 'low-stable-v4-20261017/capture').glob('*.py'))]
    if any(not path.is_file() or path.is_symlink() for path in paths):
        raise RuntimeError('HTTP_QUALIFICATION_SOURCE_MISSING')
    return {str(path): sha(path) for path in paths}


def prepare(case, *, seed):
    if socket.gethostname() != 'k8s-master' or case not in CASES:
        raise ValueError('HTTP_QUALIFICATION_MASTER_OR_CASE')
    token = secrets.token_hex(4).upper()
    run = case + '-ATTEMPT-QUALTEST-' + token
    t0 = time.time() + 90
    duration = CASES[case]
    action = {'run_id': run, 'action_id': run + '-HTTP',
              'kind': 'HTTP', 'worker': 'k8s-master',
              'mechanism': 'HTTPReqDelay', 'duration_seconds': duration,
              'capability': 'BOUND_QUALIFIED_FAULT',
              'entrypoint': str(SHARED / 'http_gate.py'),
              'planned_start_utc': utc(t0 + 180),
              'min_fault_requests': 30 if duration == 240 else 40}
    controller = ('ops-' + case.lower().replace('-', '') + '-' +
                  run.lower() + '-httpqual-controller')
    plan = {'schema': 'FINAL_HTTP_REAL_QUALIFICATION_V1',
            'run_id': run, 'case': case, 'action': action,
            'controller_unit': controller,
            'guard_unit': controller + '-guard',
            'workload_unit': controller + '-workload',
            'start_utc': utc(t0), 'end_utc': utc(t0 + 360 + duration),
            'seed': seed, 'data_root': str(DATA / run),
            'source_sha256': source_lock(),
            'formal_case_created': False}
    basic = safety_sample({'run_id': case + '-ATTEMPT-01', 'actions': []})
    if basic['status'] != 'DYNAMIC_STORAGE_AND_BASIC_HEALTH_PASS':
        raise RuntimeError('HTTP_QUALIFICATION_PREPARE_SAFETY')
    folder = ROOT / 'runs' / run
    folder.mkdir(parents=True, mode=0o700, exist_ok=False)
    path = folder / 'plan.json'
    save(path, plan)
    return {'status': 'HTTP_QUALIFICATION_PREPARED_NO_FAULT',
            'run_id': run, 'plan': str(path), 'plan_sha256': sha(path),
            'start_utc': plan['start_utc'], 'preflight': basic}


def identity(action):
    return {key: action[key] for key in ('run_id', 'action_id',
                                         'manifest_sha256')}


def workload(plan, action):
    run = plan['run_id']
    runtime = ROOT / 'runtime' / run / action['action_id']
    register_requester(action, runtime)
    output = DATA / run / 'state-machine'
    if output.exists():
        raise ValueError('HTTP_QUALIFICATION_WORKLOAD_OUTPUT_EXISTS')
    events = runtime / 'header-events.jsonl'
    events.touch(exist_ok=False)
    lock = threading.Lock()

    def audit(row):
        with lock, events.open('a') as stream:
            stream.write(json.dumps(row, sort_keys=True) + '\n')

    source = load_real_business_module()
    start = stamp(action['planned_start_utc'])
    end = start + action['duration_seconds']
    source.urllib = types.SimpleNamespace(
        request=types.SimpleNamespace(Request=request_factory(
            action, runtime, urllib.request.Request, start=start, end=end,
            audit=audit), urlopen=urllib.request.urlopen),
        parse=urllib.parse, error=urllib.error)
    args = argparse.Namespace(
        machine=str(Path('/root/stage5-v3-observability-diagnostics/snapshots/reference/workload-state-machine-c3.yaml')),
        profiles=str(OPS / 'low-stable-v4-20261017/design/load-profiles-v4b.yaml'),
        people=str(Path('/root/cross-layer-workload-generation/design/people.json')),
        profile='LOW', seed=plan['seed'], start_utc=plan['start_utc'],
        duration=360 + action['duration_seconds'], base_url=BASE,
        allow_errors=True, output=str(output))
    result = source.run(args)
    print(json.dumps({'status': result['status'],
                      'run_id': run, 'header_events_sha256': sha(events)}), flush=True)


def guard(plan, action):
    runtime = ROOT / 'runtime' / plan['run_id'] / action['action_id']
    terminal = DATA / plan['run_id'] / 'terminal.json'
    deadline = stamp(plan['end_utc']) + 330
    started = time.monotonic()
    seen = False
    status = 'HTTP_QUALIFICATION_GUARD_FAILED'
    while time.time() < deadline:
        proc = subprocess.run(['systemctl', 'is-active',
            plan['controller_unit'] + '.service'],
            capture_output=True, text=True, timeout=3)
        live = proc.returncode == 0 and proc.stdout.strip() == 'active'
        seen |= live
        if terminal.exists():
            finished = json.loads(terminal.read_text())
            status = ('HTTP_QUALIFICATION_GUARD_FINALIZED'
                      if finished.get('status') ==
                      'HTTP_QUALIFICATION_CAPTURE_COMPLETE_PENDING_ADMISSION'
                      else 'HTTP_QUALIFICATION_GUARD_CONTROLLER_FAILED')
            break
        if (seen and not live) or (not seen and time.monotonic() - started > 15):
            status = 'HTTP_QUALIFICATION_GUARD_CONTROLLER_EXIT'
            break
        time.sleep(.25)
    if runtime.is_dir() and (runtime / 'state.json').is_file():
        revoke(runtime, identity(action), status)
    if status != 'HTTP_QUALIFICATION_GUARD_FINALIZED':
        subprocess.run(['systemctl', 'stop', plan['workload_unit'] + '.service'],
                       capture_output=True, timeout=20)
    save(ROOT / 'runs' / plan['run_id'] / 'guard-terminal.json',
         {'status': status, 'run_id': plan['run_id'], 'at_utc': utc()})
    return status


def header_events(runtime):
    path = runtime / 'header-events.jsonl'
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def controller(plan_path, digest, plan, action):
    if socket.gethostname() != 'k8s-master':
        raise RuntimeError('HTTP_QUALIFICATION_NOT_MASTER')
    os.environ['KUBECONFIG'] = '/etc/kubernetes/admin.conf'
    now = time.time()
    t0 = stamp(plan['start_utc'])
    if not 60 <= t0 - now <= 180:
        raise ValueError('HTTP_QUALIFICATION_T0_NOT_FRESH')
    if safety_sample({'run_id': plan['case'] + '-ATTEMPT-01', 'actions': []})[
            'status'] != 'DYNAMIC_STORAGE_AND_BASIC_HEALTH_PASS':
        raise RuntimeError('HTTP_QUALIFICATION_INITIAL_SAFETY')
    run = DATA / plan['run_id']
    run.mkdir(parents=True, mode=0o700, exist_ok=False)
    runtime = ROOT / 'runtime' / plan['run_id'] / action['action_id']
    runtime.mkdir(parents=True, mode=0o700, exist_ok=False)
    pid = os.getpid()
    create_once(runtime / 'state.json', {
        'status': 'REGISTERED', 'run_id': plan['run_id'],
        'action_id': action['action_id'], 'manifest_sha256': digest,
        'planned_start_epoch': stamp(action['planned_start_utc']),
        'planned_end_epoch': stamp(action['planned_start_utc']) +
            action['duration_seconds'],
        'controller_unit': plan['controller_unit'],
        'guard_unit': plan['guard_unit'], 'controller_pid': pid,
        'controller_start_ticks': start_ticks(pid),
        'requester_pid': None, 'requester_start_ticks': None})
    phases = ('before', 'during', 'after')
    offsets = (0, 180, 180 + action['duration_seconds'],
               360 + action['duration_seconds'])
    windows = {name: {'start_utc': utc(t0 + offsets[index]),
                      'end_utc': utc(t0 + offsets[index + 1])}
               for index, name in enumerate(phases)}
    save(run / 'schedule.json', {'id': plan['run_id'],
        'start_utc': plan['start_utc'], 'end_utc': plan['end_utc'],
        'windows': windows, 'formal_case': False,
        'source_sha256': plan['source_sha256']})
    policy = json.loads(POLICY.read_text())
    policy['workload']['duration_seconds'] = offsets[-1]
    save(run / 'control-plan.json', policy)
    initial = collector_counters()
    save(run / 'collector-before.json', initial)
    children = {}
    handles = []
    environment = dict(os.environ, KUBECONFIG='/etc/kubernetes/admin.conf',
        A04_CAPTURE_ROOT=str(run),
        A04_BUDGET_LEDGER=str(run / 'json-byte-reservations'),
        A04_JSON_CAP_BYTES=str(24 * 1024**3))
    result = {'status': 'HTTP_QUALIFICATION_FAILED',
              'run_id': plan['run_id'], 'fault_headers_applied': 0,
              'formal_case_created': False}

    def launch(name, command):
        out = (run / (name + '.stdout')).open('x')
        err = (run / (name + '.stderr')).open('x')
        handles.extend((out, err))
        children[name] = subprocess.Popen(command, stdout=out, stderr=err,
                                          env=environment)

    guard_started = workload_started = False
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, lambda code, frame: (_ for _ in ()).throw(SystemExit(128 + code)))
    try:
        subprocess.run(['systemd-run', '--unit=' + plan['guard_unit'],
            '--property=RuntimeMaxSec=1200', '/usr/bin/python3', '-B',
            str(Path(__file__)), 'guard', '--plan', str(plan_path),
            '--sha256', digest], check=True, capture_output=True, timeout=15)
        guard_started = True
        launch('controls', ['/usr/bin/python3', '-B', str(CONTROLS),
            '--plan', str(run / 'control-plan.json'), '--seed', str(plan['seed']),
            '--duration', str(offsets[-1]), '--start-utc', plan['start_utc'],
            '--base-url', BASE, '--output', str(run / 'controls')])
        subprocess.run(['systemd-run', '--unit=' + plan['workload_unit'],
            '--property=RuntimeMaxSec=1000', '/usr/bin/python3', '-B',
            str(Path(__file__)), 'workload', '--plan', str(plan_path),
            '--sha256', digest], check=True, capture_output=True, timeout=15)
        workload_started = True
        fault_start = stamp(action['planned_start_utc'])
        fault_end = fault_start + action['duration_seconds']
        launched = set()
        permitted = revoked = start_checked = False
        next_health = time.time()
        next_workload_check = time.time() + 5
        while time.time() <= stamp(plan['end_utc']) + 330:
            now = time.time()
            for phase, window in windows.items():
                if phase not in launched and now >= stamp(window['end_utc']) - 100:
                    for name, command in capture_commands(run, phase, window).items():
                        launch(name, command)
                    launched.add(phase)
            if now >= next_health:
                health = safety_sample({'run_id': plan['case'] + '-ATTEMPT-01',
                    'actions': []})
                with (run / 'health.jsonl').open('a') as stream:
                    stream.write(json.dumps(health, sort_keys=True) + '\n')
                next_health += 15
            if now >= next_workload_check:
                state = subprocess.check_output(['systemctl', 'show',
                    plan['workload_unit'] + '.service',
                    '-p', 'ActiveState', '--value'], text=True, timeout=5).strip()
                result_path = run / 'state-machine/result-manifest.json'
                if state == 'failed' or (state == 'inactive' and
                                          not result_path.is_file()):
                    raise RuntimeError('HTTP_QUALIFICATION_WORKLOAD_STOPPED_EARLY')
                next_workload_check += 5
            if not permitted and now >= fault_start - 5:
                if not (runtime / 'state.json').is_file() or \
                        json.loads((runtime / 'state.json').read_text())[
                            'requester_pid'] is None:
                    raise RuntimeError('HTTP_QUALIFICATION_REQUESTER_NOT_READY')
                for source, expected in plan['source_sha256'].items():
                    if sha(source) != expected:
                        raise RuntimeError('HTTP_QUALIFICATION_SOURCE_DRIFT')
                permit(runtime, identity(action), {
                    **identity(action), 'status': 'PERMITTED',
                    'issued_epoch': now, 'expires_epoch': fault_end})
                permitted = True
            if permitted and not start_checked and now >= fault_start + 5:
                applied = [event for event in header_events(runtime)
                           if event['status'] == 'AUTHORIZED_HEADER']
                if not applied or abs(stamp(applied[0]['at_utc']) - fault_start) > 2:
                    raise RuntimeError('HTTP_QUALIFICATION_HEADER_START_NOT_PROMPT')
                result['actual_first_header_utc'] = applied[0]['at_utc']
                start_checked = True
            if permitted and not revoked and now >= fault_end:
                revoke(runtime, identity(action), 'HTTP_QUALIFICATION_WINDOW_COMPLETE')
                revoked = True
            for name, child in children.items():
                code = child.poll()
                if code is not None and code != 0:
                    raise RuntimeError('HTTP_QUALIFICATION_CHILD_FAILED:' + name)
            if len(launched) == 3 and all(
                    child.poll() == 0 for child in children.values()):
                break
            time.sleep(.5)
        else:
            raise TimeoutError('HTTP_QUALIFICATION_CAPTURE_DEADLINE')
        events = header_events(runtime)
        workload_result = json.loads((run / 'state-machine/result-manifest.json').read_text())
        if workload_result['status'] not in ('LOAD_PASS', 'LOAD_COMPLETED_WITH_ERRORS'):
            raise RuntimeError('HTTP_QUALIFICATION_BUSINESS_LOAD_INCOMPLETE')
        applied = [event for event in events
                   if event['status'] == 'AUTHORIZED_HEADER']
        denied = [event for event in events if event['status'] != 'AUTHORIZED_HEADER']
        if len(applied) < action['min_fault_requests'] or denied:
            raise RuntimeError('HTTP_QUALIFICATION_HEADER_COVERAGE_OR_DENIAL')
        for source, expected in plan['source_sha256'].items():
            if sha(source) != expected:
                raise RuntimeError('HTTP_QUALIFICATION_SOURCE_DRIFT_AFTER_RUN')
        after = collector_counters()
        save(run / 'collector-counters.json', {'before': initial, 'after': after,
            'refused_delta': refused_delta(initial, after)})
        save(run / 'posthealth.json', safety_sample({
            'run_id': plan['case'] + '-ATTEMPT-01', 'actions': []}))
        result.update(status='HTTP_QUALIFICATION_CAPTURE_COMPLETE_PENDING_ADMISSION',
                      fault_headers_applied=len(applied),
                      denied_normal_requests=len(denied),
                      header_events_sha256=sha(runtime / 'header-events.jsonl'))
    except BaseException as error:
        result['error'] = type(error).__name__ + ':' + str(error)
    finally:
        if runtime.is_dir() and (runtime / 'state.json').is_file():
            try:
                revoke(runtime, identity(action), 'HTTP_QUALIFICATION_CONTROLLER_TERMINAL')
            except BaseException as error:
                result['status'] = 'HTTP_QUALIFICATION_REVOKE_UNVERIFIED'
                result['revoke_error'] = type(error).__name__ + ':' + str(error)
        if workload_started:
            subprocess.run(['systemctl', 'stop', plan['workload_unit'] + '.service'],
                           capture_output=True, timeout=20)
        for child in children.values():
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
        for handle in handles:
            handle.close()
        result['completed_utc'] = utc()
        save(run / 'terminal.json', result)
        if guard_started:
            deadline = time.monotonic() + 20
            guard_terminal = ROOT / 'runs' / plan['run_id'] / 'guard-terminal.json'
            while not guard_terminal.exists() and time.monotonic() < deadline:
                time.sleep(.25)
        print(json.dumps({'status': result['status'],
            'run_id': plan['run_id'], 'error': result.get('error')}), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('prepare', 'controller', 'guard', 'workload'))
    parser.add_argument('--case', choices=tuple(CASES))
    parser.add_argument('--seed', type=int, default=31015)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--sha256')
    args = parser.parse_args()
    os.environ['KUBECONFIG'] = '/etc/kubernetes/admin.conf'
    if args.mode == 'prepare':
        if args.case is None:
            parser.error('--case is required for prepare')
        print(json.dumps(prepare(args.case, seed=args.seed), sort_keys=True))
        return
    if args.plan is None or args.sha256 is None:
        parser.error('--plan and --sha256 are required')
    plan, action = load_plan(args.plan, args.sha256)
    if args.mode == 'workload':
        workload(plan, action)
    elif args.mode == 'guard':
        guard(plan, action)
    else:
        result = controller(args.plan, args.sha256, plan, action)
        if result['status'] != 'HTTP_QUALIFICATION_CAPTURE_COMPLETE_PENDING_ADMISSION':
            raise SystemExit(1)


if __name__ == '__main__':
    main()
