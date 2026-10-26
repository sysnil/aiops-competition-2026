#!/usr/bin/env python3
"""Independent, bounded real-business mechanism qualification for final Cases."""

import argparse
import hashlib
import json
import os
import secrets
import shlex
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen


OPS = Path('/root/ops-competition-2026')
ROOT = OPS / 'final-case-mechanism-qualification-v1-20261020'
FORMAL = OPS / 'final-case-formal-entry-v5-20261019/scripts'
CAP = OPS / 'low-stable-v4-20261017/capture'
ADAPTER = OPS / 'sf013-adapter-v5-20261019/scripts/qualified_adapter.py'
HOLDER = OPS / 'scripts/resource_memory_holder.py'
CONTRACT = OPS / 'plans/competition-fault-admission.yaml'
WATCHDOGS = {
    'k8s-worker2': OPS / 'qualification/SF-FINAL-WATCHDOG-W2-V5-20261019.json',
    'k8s-worker3': OPS / 'qualification/SF-FINAL-WATCHDOG-W3-V5-20261019.json',
}
DATA = Path('/data/stage5-v3/ops-competition-2026/qualification-final-v5')
BASE = 'http://10.104.177.128:8080'
MACHINE = Path('/root/stage5-v3-observability-diagnostics/snapshots/reference/workload-state-machine-c3.yaml')
PROFILES = OPS / 'low-stable-v4-20261017/design/load-profiles-v4b.yaml'
PEOPLE = Path('/root/cross-layer-workload-generation/design/people.json')
WORKLOAD = Path('/root/cross-layer-observability-control-rehearsal/scripts/workload_generator_rehearsal.py')
CONTROLS = OPS / 'low-stable-v4-20261017/formal-v2/fixed_business_streams_plain.py'
SOCKETS = {
    'k8s-worker1': ('/run/ops-final-ssh/w1.sock', '192.168.53.53'),
    'k8s-worker2': ('/run/ops-final-ssh/w2.sock', '192.168.53.54'),
    'k8s-worker3': ('/run/ops-final-ssh/w3.sock', '192.168.53.55'),
}
CASES = {
    'SF-013': ('POD', 'k8s-worker2', 'frontend', 300),
    'SF-014': ('SERVICE', 'k8s-worker3', 'recommendation', 300),
    'SF-016': ('POD', 'k8s-worker2', 'frontend', 300),
    'SF-017': ('SERVICE', 'k8s-worker3', 'recommendation', 240),
}

sys.path.insert(0, str(FORMAL))
from safety_monitor import sample as safety_sample


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def utc(epoch=None):
    return datetime.fromtimestamp(time.time() if epoch is None else epoch,
                                  timezone.utc).isoformat()


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def save(path, value):
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write('\n')
    path.chmod(0o600)


def collector_counters():
    rows = {}
    for signal, metric in (
            ('spans', 'otelcol_receiver_refused_spans_total'),
            ('logs', 'otelcol_receiver_refused_log_records_total'),
            ('metrics', 'otelcol_receiver_refused_metric_points_total')):
        url = ('http://10.97.14.138:9090/api/v1/query?' + urlencode({
            'query': metric + '{service_name="otelcol-contrib"}'}))
        with urlopen(url, timeout=10) as response:
            payload = json.load(response)
        if payload.get('status') != 'success' or not payload['data']['result']:
            raise RuntimeError('QUALIFICATION_COLLECTOR_COUNTER_MISSING:' + signal)
        rows[signal] = {json.dumps(row['metric'], sort_keys=True):
                        float(row['value'][1])
                        for row in payload['data']['result']}
    return {'at_utc': utc(), 'series': rows}


def refused_delta(before, after):
    delta = 0
    for signal, initial in before['series'].items():
        final = after['series'].get(signal)
        if final is None or set(final) != set(initial):
            raise RuntimeError('QUALIFICATION_COLLECTOR_INSTANCE_CHANGED:' + signal)
        for identity, amount in initial.items():
            if final[identity] < amount:
                raise RuntimeError('QUALIFICATION_COLLECTOR_COUNTER_RESET:' + signal)
            delta += final[identity] - amount
    return delta


def remote(worker, argv, *, timeout=20):
    socket, host = SOCKETS[worker]
    result = subprocess.run(['ssh', '-S', socket, '-o', 'BatchMode=yes',
        '-o', 'ConnectTimeout=3', 'root@' + host, shlex.join(map(str, argv))],
        text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('QUALIFICATION_REMOTE_FAILED:' + worker + ':' +
                           result.stderr[-400:])
    return result.stdout


def copy_worker(worker, source, destination):
    socket, host = SOCKETS[worker]
    remote(worker, ['mkdir', '-p', '-m', '700', destination.parent])
    subprocess.run(['scp', '-q', '-o', 'BatchMode=yes',
        '-o', 'ControlPath=' + socket, str(source),
        'root@' + host + ':' + str(destination)], check=True,
        capture_output=True, timeout=20)
    if remote(worker, ['sha256sum', destination]).split()[0] != sha(source):
        raise RuntimeError('QUALIFICATION_WORKER_COPY_HASH_MISMATCH')


def copy_worker_directory(worker, source, destination):
    socket, host = SOCKETS[worker]
    if destination.exists():
        raise RuntimeError('QUALIFICATION_PRIVATE_ARCHIVE_ALREADY_EXISTS')
    subprocess.run(['scp', '-q', '-r', '-o', 'BatchMode=yes',
        '-o', 'ControlPath=' + socket,
        'root@' + host + ':' + str(source), str(destination)], check=True,
        capture_output=True, timeout=90)
    if not (destination / 'injection-result.json').is_file():
        raise RuntimeError('QUALIFICATION_PRIVATE_ARCHIVE_INCOMPLETE')
    return {str(path.relative_to(destination)): sha(path)
            for path in destination.rglob('*') if path.is_file()}


def inspect_targets(service):
    pods = json.loads(subprocess.check_output(['kubectl', '-n', 'otel-demo',
        'get', 'pods', '-l', 'opentelemetry.io/name=' + service,
        '-o', 'json'], text=True, timeout=20))['items']
    if len(pods) != 2:
        raise RuntimeError('QUALIFICATION_TWO_READY_REPLICAS_REQUIRED')
    targets = []
    for pod in pods:
        status = pod['status']['containerStatuses']
        if (len(status) != 1 or not status[0]['ready'] or
                pod['status']['phase'] != 'Running' or
                pod['metadata'].get('deletionTimestamp')):
            raise RuntimeError('QUALIFICATION_POD_NOT_STABLE')
        node = pod['spec']['nodeName']
        if node not in SOCKETS:
            raise RuntimeError('QUALIFICATION_UNKNOWN_NODE')
        container = status[0]['containerID']
        bare = container.removeprefix('containerd://')
        inventory = remote(node, ['ctr', '-n', 'k8s.io', 'tasks', 'list'])
        hits = [int(parts[1]) for line in inventory.splitlines()
                if (parts := line.split()) and len(parts) >= 3 and
                parts[0] == bare and parts[2] == 'RUNNING']
        if len(hits) != 1:
            raise RuntimeError('QUALIFICATION_CONTAINER_TASK_NOT_UNIQUE')
        pid = hits[0]
        process = remote(node, ['python3', '-c',
            'import json,pathlib; p=pathlib.Path("/proc/' + str(pid) + '"); '
            's=p.joinpath("stat").read_text().rsplit(")",1)[1].split(); '
            'c=p.joinpath("cgroup").read_text().strip().split("::",1)[1]; '
            'print(json.dumps({"start_ticks":s[19],"state":s[0],'
            '"cgroup":"/sys/fs/cgroup/"+c.lstrip("/")}))'])
        identity = json.loads(process)
        uid = pod['metadata']['uid']
        if (uid.replace('-', '_') not in identity['cgroup'] or
                bare not in identity['cgroup'] or
                identity['state'] in ('T', 't', 'Z')):
            raise RuntimeError('QUALIFICATION_POD_PROCESS_IDENTITY_MISMATCH')
        targets.append({'service': service, 'name': pod['metadata']['name'],
            'uid': uid, 'node': node, 'container_id': container,
            'restart_count': status[0]['restartCount'], 'pid': pid,
            'start_ticks': identity['start_ticks'],
            'cgroup': identity['cgroup']})
    return sorted(targets, key=lambda item: item['node'])


def registration(ident, case, targets):
    layer, worker, service, duration = CASES[case]
    selected = [row for row in targets if row['node'] == worker]
    if (len(selected) != (1 if layer == 'POD' else 2) or
            len({row['uid'] for row in selected}) != len(selected)):
        raise RuntimeError('QUALIFICATION_TARGET_SCOPE_CHANGED')
    source = {str(path): sha(path) for path in
              (ADAPTER, HOLDER, CONTRACT, Path(__file__),
               ROOT / 'scripts/qualify_worker.py',
               ROOT / 'scripts/assess_qualification.py', WORKLOAD,
               CONTROLS, MACHINE, PROFILES, PEOPLE,
               *sorted(CAP.glob('*.py')))}
    return {'id': ident, 'status': 'PRIVATE_FINAL_MECHANISM_QUALIFICATION',
            'layer': layer, 'host': worker, 'service': service,
            'targets': selected, 'targets_and_siblings': [{key: row[key]
                for key in ('service', 'name', 'uid', 'node',
                            'container_id', 'restart_count')} for row in targets],
            'duration_seconds': duration, 'allocation_bytes': 32 * 1024**2
                if layer == 'POD' else None,
            'contract_sha256': sha(CONTRACT), 'adapter_sha256': sha(ADAPTER),
            'holder_sha256': sha(HOLDER), 'source_sha256': source,
            'formal_case': False, 'at_utc': utc()}


def windows(start, duration):
    offsets = (0, 180, 180 + duration, 360 + duration)
    names = ('before', 'during', 'after')
    return {name: {'start_utc': utc(start + offsets[index]),
                   'end_utc': utc(start + offsets[index + 1])}
            for index, name in enumerate(names)}


def capture_commands(run, phase, window):
    first = run / 'capture/captures' / phase
    middle = run / 'capture/trace-completion' / phase
    final = run / 'capture/trace-final' / phase
    audit = run / 'capture/trace-late-audit' / phase
    start, end = window['start_utc'], window['end_utc']
    return {
        phase + '-first': ['/usr/bin/python3', '-B', str(CAP / 'capture_live_c2.py'),
            '--phase', phase, '--start-utc', start, '--end-utc', end,
            '--trace-source', 'JAEGER_OPENSEARCH_STORAGE', '--wait-until-end',
            '--output', str(first)],
        phase + '-plus60': ['/usr/bin/python3', '-B',
            str(CAP / 'schedule_trace_completion_v2.py'), '--phase', phase,
            '--start', start, '--end', end, '--first-capture', str(first),
            '--output', str(middle)],
        phase + '-plus120-plus240': ['/usr/bin/python3', '-B',
            str(CAP / 'schedule_trace_v2.py'), '--phase', phase,
            '--start', start, '--end', end, '--first', str(first),
            '--plus60', str(middle), '--output', str(final),
            '--audit-output', str(audit)],
    }


def run_case(case, *, seed=31013, no_fault=False):
    if case not in CASES or os.uname().nodename != 'k8s-master':
        raise ValueError('QUALIFICATION_CASE_OR_MASTER')
    os.environ['KUBECONFIG'] = '/etc/kubernetes/admin.conf'
    layer, worker, service, duration = CASES[case]
    ident = (('NOFAULT-' if no_fault else 'QUAL-') + case + '-' +
             layer + str(duration) + '-V5-' +
             secrets.token_hex(4).upper())
    run = DATA / ident
    private = ROOT / 'runs' / ident
    state = {'id': ident, 'case': case, 'status': 'QUALIFICATION_FAILED',
             'formal_case_created': False, 'at_utc': utc(),
             'fault_started': False, 'no_fault': no_fault}
    safety = safety_sample({'run_id': case + '-ATTEMPT-03', 'actions': []})
    if safety['status'] != 'DYNAMIC_STORAGE_AND_BASIC_HEALTH_PASS':
        raise RuntimeError('QUALIFICATION_FIVE_HOST_SAFETY_BLOCKED')
    targets = inspect_targets(service)
    reg = registration(ident, case, targets)
    proof_path = WATCHDOGS[worker]
    proof = json.loads(proof_path.read_text())
    if (proof['status'] != 'COMPETITION_WATCHDOG_SELFTEST_PASS' or
            proof['adapter_sha256'] != sha(ADAPTER)):
        raise RuntimeError('QUALIFICATION_WATCHDOG_SOURCE_MISMATCH')
    start = time.time() + 80
    schedule = {'id': ident, 'start_utc': utc(start),
                'end_utc': utc(start + 360 + duration),
                'windows': windows(start, duration),
                'faults_injected_by_observer': 0, 'formal_case': False,
                'source_sha256': reg['source_sha256']}
    run.mkdir(parents=True, mode=0o700, exist_ok=False)
    private.mkdir(parents=True, mode=0o700, exist_ok=False)
    save(private / 'registration.json', reg)
    save(run / 'schedule.json', schedule)
    save(run / 'preflight.json', safety)
    save(run / 'collector-before.json', collector_counters())
    if not no_fault:
        copy_worker(worker, private / 'registration.json', private / 'registration.json')
    policy = json.loads((OPS / 'sf013-formal-v4-20261019/design/workload-policy.json').read_text())
    policy['workload']['duration_seconds'] = 360 + duration
    save(run / 'control-plan.json', policy)
    env = dict(os.environ, KUBECONFIG='/etc/kubernetes/admin.conf',
        A04_CAPTURE_ROOT=str(run), A04_BUDGET_LEDGER=str(run / 'json-byte-reservations'),
        A04_JSON_CAP_BYTES=str(24 * 1024**3))
    children = {}
    handles = []
    def launch(name, command):
        stdout = (run / (name + '.stdout')).open('x')
        stderr = (run / (name + '.stderr')).open('x')
        handles.extend((stdout, stderr))
        children[name] = subprocess.Popen(command, stdout=stdout,
                                          stderr=stderr, env=env)

    unit = 'ops-qualification-' + ident.lower()
    worker_output = OPS / 'qualification/private-attempts' / ident
    adapter_state = OPS / 'qualification/private-runtime' / (ident + '.json')
    authorization = private / 'authorization.json'
    active = False
    started = False
    start_checked = False

    def recover():
        nonlocal active
        if no_fault:
            return
        status = remote(worker, ['systemctl', 'show', unit + '.service',
                                 '-p', 'LoadState', '--value']).strip()
        if status != 'not-found':
            remote(worker, ['systemctl', 'stop', unit + '.service'], timeout=25)
        probe = remote(worker, ['python3', '-c',
            'import pathlib; print(pathlib.Path(' + repr(str(adapter_state)) +
            ').is_file())']).strip()
        if probe == 'True':
            remote(worker, ['/usr/bin/python3', '-B', ADAPTER, 'cleanup',
                            '--state', adapter_state], timeout=30)
        active = False

    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, lambda code, frame: (_ for _ in ()).throw(SystemExit(128 + code)))
    try:
        launch('state-machine', ['/usr/bin/python3', '-B', str(WORKLOAD),
            '--machine', str(MACHINE), '--profiles', str(PROFILES),
            '--people', str(PEOPLE), '--profile', 'LOW', '--seed', str(seed),
            '--duration', str(360 + duration), '--start-utc', schedule['start_utc'],
            '--base-url', BASE, '--output', str(run / 'state-machine'),
            '--allow-errors'])
        launch('controls', ['/usr/bin/python3', '-B', str(CONTROLS),
            '--plan', str(run / 'control-plan.json'), '--seed', str(seed),
            '--duration', str(360 + duration), '--start-utc', schedule['start_utc'],
            '--base-url', BASE, '--output', str(run / 'controls')])
        launched = set()
        next_safety = time.time()
        fault_start = start + 180
        deadline = start + 360 + duration + 330
        while time.time() < deadline:
            now = time.time()
            for phase, window in schedule['windows'].items():
                end = stamp(window['end_utc'])
                if phase not in launched and now >= end - 100:
                    for name, command in capture_commands(run, phase, window).items():
                        launch(name, command)
                    launched.add(phase)
            if now >= next_safety:
                health = safety_sample({'run_id': case + '-ATTEMPT-03',
                    'actions': []})
                with (run / 'health.jsonl').open('a') as stream:
                    stream.write(json.dumps(health, sort_keys=True) + '\n')
                next_safety += 15
            if not no_fault and not started and now >= fault_start - 2:
                health = safety_sample({'run_id': case + '-ATTEMPT-03',
                    'actions': []})
                if health['status'] != 'DYNAMIC_STORAGE_AND_BASIC_HEALTH_PASS':
                    raise RuntimeError('QUALIFICATION_PREFAULT_SAFETY_BLOCKED')
                for source, digest in reg['source_sha256'].items():
                    if sha(source) != digest:
                        raise RuntimeError('QUALIFICATION_SOURCE_CHANGED')
                permit = {'status': 'COMPETITION_PREFLIGHT_AUTHORIZED',
                    'purpose': 'FINAL_CASE_V5_MECHANISM_QUALIFICATION',
                    'id': ident, 'registration_sha256': sha(private / 'registration.json'),
                    'at_utc': utc(), 'faults_allowed': True,
                    'actual_environment_pass': True,
                    'independent_watchdog_pass': True,
                    'watchdog_proof_file': str(proof_path),
                    'watchdog_proof_sha256': sha(proof_path),
                    'safety_sha256': sha(run / 'preflight.json')}
                save(authorization, permit)
                copy_worker(worker, authorization, authorization)
                started = True
            if not no_fault and started and not active and now >= fault_start:
                remote(worker, ['systemd-run', '--unit=' + unit,
                    '--property=RuntimeMaxSec=' + str(duration + 30),
                    '/usr/bin/python3', '-B',
                    ROOT / 'scripts/qualify_worker.py',
                    '--registration', private / 'registration.json',
                    '--authorization', authorization,
                    '--output', worker_output])
                active = True
                state['dispatch_utc'] = utc()
                print(json.dumps({'status': 'QUALIFICATION_EFFECT_DISPATCHED',
                    'id': ident, 'planned_utc': utc(fault_start),
                    'dispatch_utc': state['dispatch_utc']}), flush=True)
            if not no_fault and active and not start_checked and now >= fault_start + 5:
                raw = remote(worker, ['python3', '-c',
                    'import json,pathlib; p=pathlib.Path(' + repr(str(adapter_state)) +
                    '); print(p.read_text() if p.is_file() else "null")'])
                effect = json.loads(raw)
                if not effect or not effect.get('actual_start_utc'):
                    raise RuntimeError('QUALIFICATION_EFFECT_DID_NOT_START_PROMPTLY')
                delta = stamp(effect['actual_start_utc']) - fault_start
                if abs(delta) > 2:
                    raise RuntimeError('QUALIFICATION_EFFECT_START_DEVIATION:' +
                                       str(delta))
                state['fault_started'] = True
                state['actual_start_utc'] = effect['actual_start_utc']
                start_checked = True
            for name, child in children.items():
                code = child.poll()
                if code is not None and code != 0:
                    raise RuntimeError('QUALIFICATION_CHILD_FAILED:' + name + ':' + str(code))
            if not no_fault and active and now >= fault_start + duration + 10:
                raw = remote(worker, ['python3', '-c',
                    'import pathlib; p=pathlib.Path(' + repr(str(worker_output / 'injection-result.json')) +
                    '); print(p.read_text() if p.exists() else "null")'])
                result = json.loads(raw)
                if (not result or result['status'] !=
                        'COMPETITION_BOUNDED_INJECTION_COMPLETED_NOT_YET_QUALIFIED' or
                        result.get('cleanup', {}).get('status') !=
                        'COMPETITION_CLEANUP_ACTIONS_VERIFIED'):
                    raise RuntimeError('QUALIFICATION_EFFECT_OR_RECOVERY_FAILED')
                save(run / 'private-injection-result.json', result)
                hashes = copy_worker_directory(worker, worker_output,
                    run / 'private-injection')
                save(run / 'private-injection-hashes.json', hashes)
                active = False
            if len(launched) == 3 and all(child.poll() == 0 for child in children.values()):
                break
            if now >= start + 360 + duration + 30:
                print(json.dumps({'status': 'QUALIFICATION_CAPTURE_PENDING',
                    'id': ident, 'elapsed_seconds': int(now - start)}), flush=True)
            time.sleep(.5)
        else:
            raise TimeoutError('QUALIFICATION_CAPTURE_DEADLINE')
        if not no_fault and not (run / 'private-injection-result.json').is_file():
            raise RuntimeError('QUALIFICATION_EFFECT_TERMINAL_MISSING')
        for source, digest in reg['source_sha256'].items():
            if sha(source) != digest:
                raise RuntimeError('QUALIFICATION_SOURCE_DRIFT_AFTER_RUN')
        after = collector_counters()
        before = json.loads((run / 'collector-before.json').read_text())
        save(run / 'collector-counters.json', {
            'before': before, 'after': after,
            'refused_delta': refused_delta(before, after)})
        save(run / 'posthealth.json', safety_sample({
            'run_id': case + '-ATTEMPT-03', 'actions': []}))
        save(run / 'observation-result.json', {
            'status': 'OBSERVATION_COMPLETE_NOT_FAULT_QUALIFICATION',
            'at_utc': utc(), 'all_capture_processes_exited_zero': True,
            'formal_case': False})
        state['status'] = ('NOFAULT_CAPTURE_COMPLETE' if no_fault else
                           'QUALIFICATION_CAPTURE_COMPLETE_PENDING_ADMISSION')
    except BaseException as error:
        state['error'] = type(error).__name__ + ':' + str(error)
    finally:
        try:
            recover()
        except BaseException as error:
            state['status'] = 'QUALIFICATION_RECOVERY_UNVERIFIED'
            state['recovery_error'] = type(error).__name__ + ':' + str(error)
        for child in children.values():
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
        for handle in handles:
            handle.close()
        state['completed_utc'] = utc()
        save(run / 'terminal.json', state)
        print(json.dumps({'id': ident, 'status': state['status'],
            'error': state.get('error')}), flush=True)
    return state


def validate_only(case):
    if case not in CASES or os.uname().nodename != 'k8s-master':
        raise ValueError('QUALIFICATION_CASE_OR_MASTER')
    os.environ['KUBECONFIG'] = '/etc/kubernetes/admin.conf'
    layer, worker, service, duration = CASES[case]
    safety = safety_sample({'run_id': case + '-ATTEMPT-03', 'actions': []})
    targets = inspect_targets(service)
    selected = [row for row in targets if row['node'] == worker]
    if len(selected) != (1 if layer == 'POD' else 2):
        raise RuntimeError('QUALIFICATION_TARGET_SCOPE_CHANGED')
    proof = json.loads(WATCHDOGS[worker].read_text())
    if (proof['status'] != 'COMPETITION_WATCHDOG_SELFTEST_PASS' or
            proof['adapter_sha256'] != sha(ADAPTER)):
        raise RuntimeError('QUALIFICATION_WATCHDOG_SOURCE_MISMATCH')
    return {'status': 'QUALIFICATION_PREFLIGHT_PASS_NO_FAULT',
            'case': case, 'mechanism': layer, 'duration_seconds': duration,
            'worker': worker, 'targets': targets, 'safety': safety,
            'adapter_sha256': sha(ADAPTER),
            'watchdog_sha256': sha(WATCHDOGS[worker])}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--case', choices=tuple(CASES), required=True)
    parser.add_argument('--seed', type=int, default=31013)
    parser.add_argument('--validate-only', action='store_true')
    parser.add_argument('--no-fault-smoke', action='store_true')
    args = parser.parse_args()
    if args.validate_only:
        print(json.dumps(validate_only(args.case), sort_keys=True))
        return
    result = run_case(args.case, seed=args.seed,
                      no_fault=args.no_fault_smoke)
    expected = ('NOFAULT_CAPTURE_COMPLETE' if args.no_fault_smoke else
                'QUALIFICATION_CAPTURE_COMPLETE_PENDING_ADMISSION')
    if result['status'] != expected:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
