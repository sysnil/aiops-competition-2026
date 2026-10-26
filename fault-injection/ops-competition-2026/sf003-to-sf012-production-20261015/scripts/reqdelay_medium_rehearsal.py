#!/usr/bin/env python3
"""Bounded MEDIUM business-flow delay rehearsal; never creates a formal case."""

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
HERE = OPS / 'sf003-to-sf012-production-20261015'
RUN = Path('/data/stage5-v3/ops-competition-2026/rehearsals/SF30-REQDELAY-MEDIUM-20261015')
WORKLOAD = Path('/root/cross-layer-observability-control-rehearsal/scripts/workload_generator_rehearsal.py')
MACHINE = Path('/root/stage5-v3-observability-diagnostics/snapshots/reference/workload-state-machine-c3.yaml')
PROFILES = Path('/root/stage5-v3-autonomous-preparation/design/load-profiles-c3.yaml')
PEOPLE = Path('/root/cross-layer-workload-generation/design/people.json')
CONTROLS = OPS / 'sf002-production-20261014/scripts/pilot_controls.py'
WORKLOAD_WRAPPER = HERE / 'scripts/reqdelay_medium_workload.py'
SOURCE_PLAN = OPS / 'sf002-production-20261014/plans/SF-PILOT-02-ATTEMPT-01/run-plan.json'
BASE = 'http://10.104.177.128:8080'


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('x') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(0o600)


def health():
    import sys
    sys.path.insert(0, str(OPS / '30min-feasibility-20261015/scripts'))
    os.environ['SF30_RUN'] = str(RUN)
    from pilot_common import telemetry_state
    collector = telemetry_state()
    nodes = json.loads(subprocess.check_output([
        'kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', 'get', 'nodes', '-o', 'json'],
        text=True, timeout=20))['items']
    if len(nodes) != 4 or any(not any(c['type'] == 'Ready' and c['status'] == 'True'
            for c in node['status']['conditions']) for node in nodes):
        raise RuntimeError('Four-node Ready gate failed')
    if os.stat('/').st_dev == os.stat('/data/stage5-v3').st_dev:
        raise RuntimeError('Master data mount missing')
    disks = {mount: shutil.disk_usage(mount).free for mount in ('/', '/data/stage5-v3')}
    if min(disks.values()) < 5_000_000_000:
        raise RuntimeError('Five GB floor reached')
    return {'at_utc': now(), 'collector_uid': collector['uid'],
            'collector_config_sha256': collector['config_sha256'],
            'receiver_refused': collector['refused'],
            'collector_working_set_bytes': collector['working_set_bytes'],
            'freshness': collector['freshness'], 'disks_free_bytes': disks}


def register(residue):
    if RUN.exists():
        raise RuntimeError('No overwrite of a prior qualification')
    audit = json.loads(Path(residue).read_text())
    age = (datetime.now(timezone.utc) -
           datetime.fromisoformat(audit['at_utc'])).total_seconds()
    if audit['status'] != 'HOST_RESIDUE_PASS' or not 0 <= age <= 300:
        raise RuntimeError('Fresh five-host residue gate required')
    units = subprocess.check_output(['systemctl', 'list-units', '--state=running',
        '--type=service', '--plain', '--no-legend'], text=True, timeout=10)
    if any(token in units for token in ('ops-sf30-', 'ops-sf002-', 'ops-b8-')):
        raise RuntimeError('Another competition experiment is active')
    initial = health()
    t0 = datetime.now(timezone.utc) + timedelta(seconds=45)
    files = {'generator': WORKLOAD, 'machine': MACHINE, 'profiles': PROFILES,
             'people': PEOPLE, 'wrapper': WORKLOAD_WRAPPER,
             'controls': CONTROLS, 'controller': Path(__file__)}
    RUN.mkdir(parents=True, mode=0o700)
    plan = {'id': RUN.name, 'formal_case': False, 'duration_seconds': 420,
            'profile': 'MEDIUM', 'seed': 20261015032, 'delay_ms': 250,
            'target_business_operation': 'home',
            'start_utc': t0.isoformat(),
            'fault_start_utc': (t0 + timedelta(seconds=180)).isoformat(),
            'fault_end_utc': (t0 + timedelta(seconds=240)).isoformat(),
            'end_utc': (t0 + timedelta(seconds=420)).isoformat(),
            'source_sha256': {name: sha(path) for name, path in files.items()},
            'residue_sha256': sha(residue), 'initial_health': initial,
            'watchdog': {'systemd_runtime_max_seconds': 510,
                         'kill_mode': 'control-group'},
            'case_created': False}
    write_new(RUN / 'registration.json', plan)
    print(json.dumps({'status': 'MEDIUM_REHEARSAL_REGISTERED',
                      'registration': str(RUN / 'registration.json'),
                      'sha256': sha(RUN / 'registration.json')}))


def run(registration, registration_sha):
    if os.getenv('INVOCATION_ID') is None:
        raise RuntimeError('Independent systemd watchdog required')
    if sha(registration) != registration_sha:
        raise RuntimeError('Registration hash mismatch')
    plan = json.loads(Path(registration).read_text())
    if plan['id'] != RUN.name or plan['source_sha256']['controller'] != sha(__file__):
        raise RuntimeError('Controller identity/source changed')
    if not 1 <= datetime.fromisoformat(plan['start_utc']).timestamp() - time.time() <= 60:
        raise RuntimeError('Stale start')
    current = health()
    initial = plan['initial_health']
    if (current['collector_uid'] != initial['collector_uid'] or
            current['collector_config_sha256'] != initial['collector_config_sha256'] or
            current['receiver_refused'] != initial['receiver_refused']):
        raise RuntimeError('Telemetry identity changed after registration')
    control_spec = json.loads(SOURCE_PLAN.read_text())['workload']
    control_spec['duration_seconds'] = 420
    write_new(RUN / 'control-plan.json', {'id': RUN.name,
              'start_utc': plan['start_utc'], 'workload': control_spec,
              'seed': plan['seed']})
    processes, handles = {}, []
    result = {'status': 'MEDIUM_REHEARSAL_FAILED', 'formal_case_created': False,
              'fault_persistence': False, 'started_utc': now()}

    def launch(name, command):
        stdout = (RUN / (name + '.stdout')).open('x')
        stderr = (RUN / (name + '.stderr')).open('x')
        handles.extend((stdout, stderr))
        processes[name] = subprocess.Popen(command, stdout=stdout, stderr=stderr)

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda s, f: (_ for _ in ()).throw(SystemExit(128+s)))
    try:
        launch('workload', ['/usr/bin/python3', '-B', str(WORKLOAD_WRAPPER),
            '--registration', str(registration), '--registration-sha256', registration_sha,
            '--output', str(RUN / 'state-machine')])
        launch('controls', ['/usr/bin/python3', '-B', str(CONTROLS),
            '--plan', str(RUN / 'control-plan.json'), '--seed', str(plan['seed']),
            '--duration', '420', '--start-utc', plan['start_utc'],
            '--base-url', BASE, '--output', str(RUN / 'controls')])
        end = datetime.fromisoformat(plan['end_utc']).timestamp()
        while time.time() < end + 30:
            check = health()
            if (check['collector_uid'] != initial['collector_uid'] or
                    check['receiver_refused'] != initial['receiver_refused']):
                raise RuntimeError('Collector changed or refused data')
            with (RUN / 'safety.jsonl').open('a') as stream:
                stream.write(json.dumps(check) + '\n')
            failed = {name: proc.returncode for name, proc in processes.items()
                      if proc.poll() not in (None, 0)}
            if failed:
                raise RuntimeError('Business process failed: ' + repr(failed))
            if all(proc.poll() == 0 for proc in processes.values()):
                break
            time.sleep(15)
        else:
            raise RuntimeError('Bounded rehearsal timed out')
        workload = json.loads((RUN / 'state-machine/delay-rehearsal-result.json').read_text())
        controls = json.loads((RUN / 'controls/result-manifest.json').read_text())
        if workload['status'] != 'MEDIUM_WORKLOAD_DELAY_REHEARSAL_COMPLETE':
            raise RuntimeError('Workload did not complete')
        if controls['status'] != 'CONTROL_STREAMS_COMPLETE' or any(
                row['missed_arrivals'] or row['successful_requests'] != row['planned_requests']
                for row in controls['streams'].values()):
            raise RuntimeError('Fixed normal business controls incomplete')
        result.update(status='MEDIUM_REQDELAY_REHEARSAL_COMPLETE_PENDING_TRACE_AUDIT',
                      workload=workload, controls=controls)
    except BaseException as exc:
        result['error'] = type(exc).__name__ + ': ' + str(exc)
    finally:
        for proc in processes.values():
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        for handle in handles:
            handle.close()
        result['completed_utc'] = now()
        result['child_exit_codes'] = {name: proc.returncode for name, proc in processes.items()}
        write_new(RUN / 'terminal.json', result)
        print(json.dumps({'status': result['status'], 'error': result.get('error')}), flush=True)
    if result['status'] != 'MEDIUM_REQDELAY_REHEARSAL_COMPLETE_PENDING_TRACE_AUDIT':
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='action', required=True)
    first = sub.add_parser('register')
    first.add_argument('--residue-audit', type=Path, required=True)
    second = sub.add_parser('run')
    second.add_argument('--registration', type=Path, required=True)
    second.add_argument('--registration-sha256', required=True)
    args = parser.parse_args()
    if args.action == 'register':
        register(args.residue_audit)
    else:
        run(args.registration, args.registration_sha256)


if __name__ == '__main__':
    main()
