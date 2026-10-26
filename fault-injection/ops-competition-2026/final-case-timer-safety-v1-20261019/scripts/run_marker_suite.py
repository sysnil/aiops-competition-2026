#!/usr/bin/env python3
"""Run preregistered harmless Marker cases through real Master/Worker systemd."""

import argparse
import hashlib
import json
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from marker_controller import ROOT, remote


CONTRACT = ROOT / 'design/marker-test-contract.json'
RUNS = ROOT / 'markers'
SCRIPT = ROOT / 'scripts'
SCENARIOS = ('normal', 'duplicate_arm', 'arm_partial_failure',
             'identity_failure', 'capture_failure', 'capacity_failure',
             'sigterm', 'sigkill', 'restart_first', 'cancel_then_wait')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(argv, timeout=20):
    result = subprocess.run(list(map(str, argv)), capture_output=True,
                            text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('COMMAND_FAILED ' + ' '.join(map(str, argv[:3])) +
                           ': ' + result.stderr[-700:])
    return result.stdout


def wait_until(predicate, seconds, message):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.15)
    raise RuntimeError('WAIT_TIMEOUT: ' + message)


def registration(scenario, contract_sha, batch_token):
    run_id = 'SF013-TIMER-MARKER-' + batch_token + '-' + scenario
    root = RUNS / run_id
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    planned = datetime.now(timezone.utc) + timedelta(seconds=7)
    value = {'status': 'PREREGISTERED_HARMLESS_MARKER', 'run_id': run_id,
        'action_id': run_id + '-MARK',
        'timer_unit': 'ops-sf013-' + (run_id + '-MARK').lower(),
        'controller_unit': 'ops-sf013-marker-control-' + batch_token + '-' + scenario,
        'guard_unit': 'ops-sf013-marker-guard-' + batch_token + '-' + scenario,
        'planned_utc': planned.isoformat(), 'scenario': scenario,
        'contract_sha256': contract_sha, 'real_fault_adapter_allowed': False}
    target = root / 'registration.json'
    with target.open('x') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
    value['registration_sha256'] = sha(target)
    return value, target


def systemd_start(unit, script, registration_path, digest, extra=(),
                  restart_on_failure=False):
    properties = (['--property=Restart=on-failure', '--property=RestartSec=1s']
                  if restart_on_failure else [])
    return run(['systemd-run', '--unit=' + unit, '--collect',
        '--property=RuntimeMaxSec=30', *properties, '/usr/bin/python3', '-B', script,
        '--registration', registration_path, '--registration-sha', digest, *extra])


def unit_stop(unit):
    subprocess.run(['systemctl', 'stop', unit + '.service'],
                   capture_output=True, timeout=15)


def marker_inactive(value):
    state = remote(value, 'inspect')
    return (state['revoked'] and all(
        item['ActiveState'] not in ('active', 'activating', 'reloading')
        for item in state['units'].values()))


def one_scenario(scenario, contract_sha, batch_token):
    value, path = registration(scenario, contract_sha, batch_token)
    controller = value['controller_unit']
    guard = value['guard_unit']
    record = {'scenario': scenario, 'run_id': value['run_id'],
              'registration_sha256': value['registration_sha256'],
              'planned_utc': value['planned_utc'], 'status': 'STARTED'}
    try:
        systemd_start(guard, SCRIPT / 'marker_guard.py', path,
                      value['registration_sha256'], restart_on_failure=True)
        systemd_start(controller, SCRIPT / 'marker_controller.py', path,
                      value['registration_sha256'])
        if scenario in ('sigterm', 'sigkill', 'restart_first', 'cancel_then_wait'):
            wait_until(lambda: remote(value, 'inspect')['armed'], 4,
                       'Marker did not arm before controller signal')
            signal = 'SIGKILL' if scenario == 'sigkill' else 'SIGTERM'
            planned = datetime.fromisoformat(value['planned_utc']).timestamp()
            while time.time() < planned - 3:
                time.sleep(0.1)
            run(['systemctl', 'kill', '--signal=' + signal,
                 controller + '.service'])
        wait_until(lambda: (path.parent / 'guard-terminal.json').exists(), 16,
                   'Independent guard terminal missing')
        if scenario != 'sigkill':
            wait_until(lambda: (path.parent / 'controller-terminal.json').exists(), 5,
                       'Controller terminal missing')
        if scenario == 'restart_first':
            systemd_start(controller + '-restart', SCRIPT / 'marker_controller.py',
                          path, value['registration_sha256'], ('--restart-reuse',))
            wait_until(lambda: (path.parent / 'controller-restart-terminal.json').exists(),
                       5, 'Restart terminal missing')
        planned = datetime.fromisoformat(value['planned_utc']).timestamp()
        while time.time() < planned + 3:
            time.sleep(0.1)
        worker = remote(value, 'inspect')
        expected_fire = scenario == 'normal'
        record.update(status='MARKER_LIFECYCLE_PASS' if
                      (worker['fired'] is expected_fire and marker_inactive(value))
                      else 'MARKER_LIFECYCLE_FAIL',
                      fired=worker['fired'], expected_fired=expected_fire,
                      worker_inspection=worker,
                      controller_terminal=(json.loads((path.parent /
                          'controller-terminal.json').read_text()) if
                          (path.parent / 'controller-terminal.json').exists() else None),
                      guard_terminal=json.loads((path.parent /
                          'guard-terminal.json').read_text()),
                      observed_after_planned_utc=datetime.now(timezone.utc).isoformat())
        if record['status'] != 'MARKER_LIFECYCLE_PASS':
            raise RuntimeError('MARKER_POST_TRIGGER_INVARIANT_FAILED')
    except BaseException as exc:
        record['status'] = 'MARKER_LIFECYCLE_FAIL'
        record['error'] = type(exc).__name__ + ': ' + str(exc)
        unit_stop(controller)
        try:
            record['emergency_cancel'] = remote(value, 'cancel')
        except BaseException as cleanup:
            record['cleanup_error'] = str(cleanup)
        unit_stop(guard)
    result = path.parent / 'assessment.json'
    with result.open('x') as stream:
        json.dump(record, stream, indent=2)
        stream.write('\n')
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--batch-token', required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError('Marker suite output already exists')
    if not args.batch_token.startswith('20261019') or not args.batch_token.isalnum():
        raise RuntimeError('Marker batch token must be a unique 20261019 name')
    contract = json.loads(CONTRACT.read_text())
    if (contract['status'] != 'PREREGISTERED_HARMLESS_TIMER_LIFECYCLE_TEST' or
            tuple(contract['cases']) != SCENARIOS or
            contract['real_fault_adapter_allowed'] is not False):
        raise RuntimeError('Marker contract changed')
    source = {str(path): sha(path) for path in (
        CONTRACT, SCRIPT / 'marker_worker.py', SCRIPT / 'marker_controller.py',
        SCRIPT / 'marker_guard.py', SCRIPT / 'run_marker_suite.py',
        Path('/root/ops-competition-2026/sf013-adapter-v3-20261019/scripts/timer_lifecycle.py'),
        Path('/root/ops-competition-2026/sf013-adapter-v3-20261019/scripts/timer_guard.py'))}
    results = []
    for scenario in SCENARIOS:
        outcome = one_scenario(scenario, sha(CONTRACT), args.batch_token)
        results.append({key: outcome[key] for key in
                        ('scenario', 'status', 'fired', 'expected_fired')
                        if key in outcome})
        print(json.dumps(results[-1]), flush=True)
        if outcome['status'] != 'MARKER_LIFECYCLE_PASS':
            break
    decision = {'status': 'HARMLESS_MARKER_SUITE_PASS' if len(results) == len(SCENARIOS)
                and all(row['status'] == 'MARKER_LIFECYCLE_PASS' for row in results)
                else 'HARMLESS_MARKER_SUITE_FAIL',
                'at_utc': datetime.now(timezone.utc).isoformat(),
                'contract_sha256': sha(CONTRACT), 'source_sha256': source,
                'results': results, 'real_faults_injected': 0,
                'business_resources_modified': False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(decision, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': decision['status'], 'completed': len(results)}), flush=True)
    if decision['status'] != 'HARMLESS_MARKER_SUITE_PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
