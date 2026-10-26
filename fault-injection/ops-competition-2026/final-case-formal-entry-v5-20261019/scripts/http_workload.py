#!/usr/bin/env python3
"""Qualified state-machine workload with Master-local per-request delay gate."""

import argparse
import json
import os
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

SHARED = Path('/root/ops-competition-2026/final-case-unified-execution-v2-20261019/scripts')
sys.path.insert(0, str(SHARED))
from action_decision import create_once, decision_lock, read_bound
from http_gate import controlled_request_factory
from control_contract import ROOT, load_manifest, sha


SOURCE = Path('/root/cross-layer-observability-control-rehearsal/scripts/workload_generator_rehearsal.py')
MACHINE = Path('/root/stage5-v3-observability-diagnostics/snapshots/reference/workload-state-machine-c3.yaml')
PROFILES = Path('/root/ops-competition-2026/low-stable-v4-20261017/design/load-profiles-v4b.yaml')
PEOPLE = Path('/root/cross-layer-workload-generation/design/people.json')
BASE = 'http://10.104.177.128:8080'


def start_ticks(pid):
    return int(Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19])


def unit_state(unit):
    output = subprocess.check_output(
        ['systemctl', 'show', unit + '.service', '-p', 'ActiveState',
         '-p', 'MainPID'], text=True, timeout=2)
    return dict(row.split('=', 1) for row in output.splitlines() if '=' in row)


def register_requester(action, runtime, *, pid=os.getpid(), ticks=start_ticks):
    with decision_lock(runtime) as root:
        state = read_bound(root / 'state.json')
        if (state['run_id'] != action['run_id'] or
                state['action_id'] != action['action_id'] or
                state['manifest_sha256'] != action['manifest_sha256'] or
                (root / 'REVOKED.json').exists() or
                (root / 'permit.json').exists()):
            raise ValueError('HTTP_REQUESTER_REGISTRATION_CLOSED')
        state['requester_pid'] = pid
        state['requester_start_ticks'] = ticks(pid)
        candidate = root / 'state.next'
        if candidate.exists():
            raise ValueError('HTTP_STATE_PENDING_REPLACEMENT')
        candidate.write_text(json.dumps(state, sort_keys=True) + '\n')
        candidate.chmod(0o600)
        os.replace(candidate, root / 'state.json')
        return {'status': 'HTTP_REQUESTER_BOUND', 'pid': pid}


def request_factory(action, runtime, original, *, start, end,
                    audit, now=time.time, units=unit_state, ticks=start_ticks):
    controlled = controlled_request_factory(action, runtime, original,
                                             unit_state=units,
                                             clock=now, pid_start=ticks)

    def request(url, data=None, headers=None, method=None, **kwargs):
        at = now()
        parsed = urllib.parse.urlparse(url)
        eligible = method == 'GET' and parsed.path == '/' and not parsed.query
        if not (eligible and start <= at < end):
            return original(url, data=data, headers=dict(headers or {}), method=method,
                            **kwargs)
        try:
            request_value = controlled(url, data=data, headers=headers,
                                       method=method, **kwargs)
            applied = request_value.get_header('X-envoy-fault-delay-request') is not None
            audit({'at_utc': datetime.fromtimestamp(at, timezone.utc).isoformat(),
                   'path': '/', 'status': 'AUTHORIZED_HEADER' if applied else 'NO_HEADER',
                   'traceparent': (headers or {}).get('traceparent')})
            return request_value
        except (OSError, ValueError, KeyError, RuntimeError) as error:
            audit({'at_utc': datetime.fromtimestamp(at, timezone.utc).isoformat(),
                   'path': '/', 'status': 'DENIED_NORMAL_REQUEST',
                   'reason': type(error).__name__ + ':' + str(error),
                   'traceparent': (headers or {}).get('traceparent')})
            return original(url, data=data, headers=dict(headers or {}), method=method,
                            **kwargs)
    return request


def load_real_business_module():
    source = SOURCE.read_text()
    anchor = "'User-Agent': 'stage5-workload-rehearsal/1.0'"
    if source.count(anchor) != 1:
        raise ValueError('BUSINESS_SOURCE_ANCHOR_CHANGED')
    source = source.replace(anchor, "'User-Agent': 'business-workload/1.0'")
    module = types.ModuleType('qualified_state_machine_workload')
    module.__file__ = str(SOURCE)
    exec(compile(source, str(SOURCE), 'exec'), module.__dict__)
    return module


def run(manifest_path, manifest_sha, output, *, module_loader=load_real_business_module):
    manifest = load_manifest(manifest_path, manifest_sha)
    matches = [a for a in manifest['actions'] if a['kind'] == 'HTTP']
    if len(matches) != 1 or manifest['run_id'].split('-ATTEMPT-')[0] not in ('SF-015', 'SF-017'):
        raise ValueError('HTTP_CASE_ACTION_SCOPE')
    action = matches[0]
    if manifest['mode'] != 'FORMAL_CANDIDATE':
        raise ValueError('HTTP_REAL_REQUESTS_FORBIDDEN_IN_REHEARSAL')
    if output.exists():
        raise ValueError('HTTP_OUTPUT_ALREADY_EXISTS')
    for path in (SOURCE, MACHINE, PROFILES, PEOPLE):
        if sha(path) != manifest['source_sha256'].get(str(path)):
            raise ValueError('HTTP_BUSINESS_SOURCE_CHANGED')
    runtime = ROOT / 'runtime' / manifest['run_id'] / action['action_id']
    register_requester(action, runtime)
    output.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    audit_path = runtime / 'header-events.jsonl'
    audit_path.touch(exist_ok=False)
    audit_lock = threading.Lock()

    def audit(event):
        with audit_lock:
            with audit_path.open('a') as stream:
                stream.write(json.dumps(event, sort_keys=True) + '\n')

    source_module = module_loader()
    start = datetime.fromisoformat(action['planned_start_utc']).timestamp()
    end = start + action['duration_seconds']
    terminal_path = runtime / 'http-terminal.json'

    def finish_window():
        while time.time() < end + 2:
            time.sleep(min(.25, max(.01, end + 2 - time.time())))
        with audit_lock:
            events = [json.loads(line) for line in audit_path.read_text().splitlines()]
        applied = sum(row['status'] == 'AUTHORIZED_HEADER' for row in events)
        denied = sum(row['status'] == 'DENIED_NORMAL_REQUEST' for row in events)
        minimum = action.get('min_fault_requests', 1)
        create_once(terminal_path, {'status': 'HTTP_ACTION_COMPLETE'
                    if applied >= minimum and denied == 0 else 'HTTP_ACTION_FAILED',
                    'run_id': action['run_id'], 'action_id': action['action_id'],
                    'manifest_sha256': manifest_sha,
                    'authorized_headers': applied, 'denied_requests': denied,
                    'audit_sha256': sha(audit_path)})

    reviewer = threading.Thread(target=finish_window, daemon=True)
    reviewer.start()
    source_module.urllib = types.SimpleNamespace(
        request=types.SimpleNamespace(Request=request_factory(action, runtime,
            urllib.request.Request, start=start, end=end, audit=audit),
            urlopen=urllib.request.urlopen),
        parse=urllib.parse, error=urllib.error)
    args = argparse.Namespace(machine=str(MACHINE), profiles=str(PROFILES),
        people=str(PEOPLE), profile='LOW', seed=manifest['workload_seed'],
        start_utc=manifest['start_utc'], duration=1800, base_url=BASE,
        allow_errors=True, output=str(output))
    try:
        result = source_module.run(args)
    except BaseException:
        if not terminal_path.exists():
            create_once(terminal_path, {'status': 'HTTP_ACTION_FAILED',
                'run_id': action['run_id'], 'action_id': action['action_id'],
                'manifest_sha256': manifest_sha,
                'reason': 'BUSINESS_WORKLOAD_TERMINATED'})
        raise
    reviewer.join(timeout=max(0, end + 5 - time.time()))
    if not terminal_path.is_file():
        raise RuntimeError('HTTP_WINDOW_AUDIT_NOT_FINISHED')
    return {'status': 'HTTP_WORKLOAD_COMPLETE_PENDING_AUDIT',
            'generator_status': result['status'],
            'header_events_sha256': sha(audit_path)}


def run_nofault_rehearsal(manifest_path, manifest_sha, output,
                          *, module_loader=load_real_business_module):
    manifest = load_manifest(manifest_path, manifest_sha)
    if manifest['mode'] != 'HARMLESS_REHEARSAL' or output.exists():
        raise ValueError('HTTP_REHEARSAL_SCOPE')
    action = next(a for a in manifest['actions'] if a['kind'] == 'HTTP')
    output.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    source_module = module_loader()
    source_module.urllib = types.SimpleNamespace(
        request=types.SimpleNamespace(Request=urllib.request.Request,
                                      urlopen=urllib.request.urlopen),
        parse=urllib.parse, error=urllib.error)
    runtime = ROOT / 'runtime' / action['run_id'] / action['action_id']
    terminal_path = runtime / 'http-terminal.json'
    window_end = datetime.fromisoformat(action['planned_start_utc']).timestamp() + action['duration_seconds']

    def finish_window():
        while time.time() < window_end + 2:
            time.sleep(min(.25, max(.01, window_end + 2 - time.time())))
        if not terminal_path.exists():
            create_once(terminal_path, {'status': 'HARMLESS_HTTP_WINDOW_COMPLETE',
                'run_id': action['run_id'], 'action_id': action['action_id'],
                'fault_headers_sent': 0})

    reviewer = threading.Thread(target=finish_window, daemon=True)
    reviewer.start()
    start_epoch = datetime.fromisoformat(manifest['start_utc']).timestamp()
    minimum_duration = int(window_end - start_epoch) + 3
    duration = max(manifest.get('rehearsal_duration_seconds', action['duration_seconds']),
                   minimum_duration)
    args = argparse.Namespace(machine=str(MACHINE), profiles=str(PROFILES),
        people=str(PEOPLE), profile='LOW', seed=manifest['workload_seed'],
        start_utc=manifest['start_utc'],
        duration=duration, base_url=BASE,
        allow_errors=True, output=str(output))
    try:
        result = source_module.run(args)
        terminal = {'status': 'HARMLESS_HTTP_WINDOW_COMPLETE',
                    'generator_status': result['status'], 'fault_headers_sent': 0,
                    'run_id': action['run_id'], 'action_id': action['action_id']}
    except BaseException as error:
        terminal = {'status': 'HARMLESS_HTTP_WINDOW_FAILED',
                    'error': type(error).__name__ + ':' + str(error),
                    'fault_headers_sent': 0,
                    'run_id': action['run_id'], 'action_id': action['action_id']}
        raise
    finally:
        if terminal['status'] == 'HARMLESS_HTTP_WINDOW_FAILED' and not terminal_path.exists():
            create_once(terminal_path, terminal)
    reviewer.join(timeout=max(3, window_end + 5 - time.time()))
    if not terminal_path.exists():
        raise RuntimeError('HARMLESS_HTTP_WINDOW_TERMINAL_MISSING')
    master = ROOT / 'runtime' / action['run_id'] / 'master'
    create_once(master / 'workload-terminal.json', terminal)
    return terminal


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--manifest-sha', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--no-fault-rehearsal', action='store_true')
    args = parser.parse_args()
    function = run_nofault_rehearsal if args.no_fault_rehearsal else run
    print(json.dumps(function(args.manifest, args.manifest_sha, args.output)))


if __name__ == '__main__':
    main()
