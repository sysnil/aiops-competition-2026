#!/usr/bin/env python3
"""Formal SF-011 thirty-minute capture, coupled to a separate fault controller."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pilot_common import CONTROL, OFFSETS, RUN, EXPECTED_CONFIG, boundary_match, live_requests, telemetry_state


OPS = Path('/root/ops-competition-2026')
PROD = OPS / 'sf002-production-20261014'
HERE = Path(__file__).resolve().parent
CAP = OPS / 'low-stable-v4-20261017/capture'
DATA = Path('/data/stage5-v3/ops-competition-2026')
BASE = 'http://10.104.177.128:8080'
MACHINE = Path('/root/stage5-v3-observability-diagnostics/snapshots/reference/workload-state-machine-c3.yaml')
PROFILES = OPS / 'low-stable-v4-20261017/design/load-profiles-v4b.yaml'
FIXED_STREAMS = OPS / 'low-stable-v4-20261017/formal-v2/fixed_business_streams_faultable.py'
PEOPLE = Path('/root/cross-layer-workload-generation/design/people.json')
WORKLOAD_POLICY = OPS / 'sf011-http-formal-v5-20261018/design/workload-policy.json'
RUN_ID = 'SF-011-ATTEMPT-05'
IDENTITY = OPS / 'sf011-http-formal-v5-20261018/plans/current-identity.json'
FORMAL_PLAN = OPS / 'sf011-http-adapter-v5-20261018/plans/SF-011-ATTEMPT-05/run-plan.json'
FORMAL_MANIFEST = OPS / 'sf011-http-adapter-v5-20261018/plans/SF-011-ATTEMPT-05/run-manifest.json'
FAULT_CONTROL = RUN / 'control'
FAULT_CONTROLLER_UNIT = 'ops-sf011-formal-controller05-20261017.service'
REAL_SCRIPTS = OPS / 'sf011-http-adapter-v5-20261018/scripts'
GIB = 1024**3


def utc(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def stamp(value: str) -> float:
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024**2), b''):
            h.update(block)
    return h.hexdigest()


def write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('x') as stream:
        json.dump(payload, stream, indent=2)
        stream.write('\n')
    path.chmod(0o600)


def read(path: Path | str) -> dict:
    return json.loads(Path(path).read_text())


def validate_fault_binding(plan: dict, schedule_sha: str, ready: dict,
                           manifest_sha: str) -> None:
    if (plan['id'] != RUN_ID or plan['raw_root'] != str(RUN) or
            plan['capture_schedule_sha256'] != schedule_sha or
            plan['authorization_scope'] != 'ONE_SF011_HTTP_HOME_DELAY60_ONLY' or
            ready['manifest_sha256'] != manifest_sha):
        raise RuntimeError('Fault controller identity/schedule mismatch')


def reject_other_competition_units(units: str) -> None:
    allowed = {'ops-sf011-formal-capture05-20261017.service',
               'ops-sf011-formal-controller05-20261017.service',
               'ops-sf011-formal-observer05-20261017.service'}
    prefixes = ('ops-sf', 'ops-b8-')
    for line in units.splitlines():
        unit = line.split(None, 1)[0] if line.strip() else ''
        if unit.startswith(prefixes) and unit not in allowed:
            raise RuntimeError('Another competition run is active: ' + unit)


def source_lock() -> dict:
    source_files = [*HERE.glob('*.py'), IDENTITY, WORKLOAD_POLICY,
        REAL_SCRIPTS / 'sf004_workload.py', REAL_SCRIPTS / 'sf004_controller.py',
        REAL_SCRIPTS / 'sf004_preflight.py',
        FIXED_STREAMS, PROD / 'scripts/bounded_storage.py',
        CAP / 'capture_live_c2.py', CAP / 'schedule_trace_completion_v2.py',
        CAP / 'schedule_trace_completion.py', CAP / 'schedule_trace_v2.py',
        CAP / 'trace_storage_reader.py', CAP / 'trace_identity.py',
        CAP / 'evaluate_trace_v2.py',
        MACHINE, PROFILES, PEOPLE]
    return {str(path): sha(path) for path in source_files}


def prereq() -> tuple[list[dict], dict]:
    from sf004_safety import safety, storage

    if os.environ.get('KUBECONFIG') != '/etc/kubernetes/admin.conf':
        raise RuntimeError('Explicit Kubernetes credential context required before capture')
    if not Path(os.environ['KUBECONFIG']).is_file():
        raise RuntimeError('Kubernetes credential file missing')
    if not RUN.is_dir() or not (RUN/'schedule.json').is_file() or (RUN/'terminal.json').exists():
        raise RuntimeError('Exact preregistered case directory and new terminal required')
    if RUN.name != RUN_ID or not IDENTITY.is_file():
        raise RuntimeError('Exact SF-011 and current identity required')
    identity = read(IDENTITY)
    if (identity['status'] != 'CANDIDATE_CGROUPS_INSPECTED_NOT_QUALIFIED' or
            not 0 <= time.time() - stamp(identity['at_utc']) <= 300):
        raise RuntimeError('Fresh current target inspection required')
    storage()
    units = subprocess.check_output(
        ['systemctl', 'list-units', '--type=service', '--state=running', '--no-legend', '--no-pager'],
        text=True, timeout=10,
    )
    reject_other_competition_units(units)
    expected = [{key: row[key] for key in ('service', 'name', 'uid', 'node',
                'container_id', 'restart_count')} for row in identity['targets']]
    environmental = safety(expected)
    collector = telemetry_state()
    if collector['config_sha256'] != EXPECTED_CONFIG:
        raise RuntimeError('Collector version changed')
    return expected, {'environment': environmental, 'collector': collector, 'storage': storage()}


def main() -> None:
    if __import__('socket').gethostname() != 'k8s-master':
        raise RuntimeError('Master only')
    if not FORMAL_MANIFEST.is_file() or not FORMAL_PLAN.is_file():
        raise RuntimeError('Frozen SF-011 run registry missing')
    sys.path.insert(0, str(REAL_SCRIPTS))
    from run_registry import load_bundle
    manifest, plan = load_bundle(FORMAL_MANIFEST, sha(FORMAL_MANIFEST))
    expected, initial = prereq()
    CONTROL.mkdir(mode=0o700, exist_ok=True)
    lock = source_lock()
    schedule = read(RUN / 'schedule.json')
    t0 = stamp(schedule['start_utc'])
    windows = schedule['windows']
    if (schedule['id'] != RUN_ID or schedule['formal_case'] is not True or
            schedule['duration_seconds'] != 1800 or schedule['source_sha256'] != lock or
            schedule['start_utc'] != plan['start_utc'] or
            sha(RUN/'schedule.json') != plan['capture_schedule_sha256'] or
            sorted(windows) != [f'chunk-{i:02d}' for i in range(6)] or
            not 90 <= t0 - time.time() <= 240):
        raise RuntimeError('Preregistered 30-minute capture schedule changed or stale')
    for index, name in enumerate(sorted(windows)):
        if (stamp(windows[name]['start_utc']) != t0 + OFFSETS[index] or
                stamp(windows[name]['end_utc']) != t0 + OFFSETS[index+1]):
            raise RuntimeError('Capture partition deviates from 300-second contract')
    write(CONTROL / 'capture-initial-health.json', initial)
    os.environ.update({
        'SF011_RUN': str(RUN), 'A04_CAPTURE_ROOT': str(RUN),
        'A04_BUDGET_LEDGER': str(CONTROL / 'json-byte-reservations'),
        'A04_JSON_CAP_BYTES': str(24 * GIB),
    })
    children: dict[str, subprocess.Popen] = {}
    handles = []
    phase = 'BEFORE_WORKLOAD_LAUNCH'
    result: dict = {'id': RUN_ID, 'status': 'SF011_FORMAL_CAPTURE_FAILED',
                    'formal_case_created': True, 'public_created': False,
                    'private_gt_created': False}

    def launch(name: str, command: list[str]) -> None:
        stdout = (RUN / (name + '.stdout')).open('x')
        stderr = (RUN / (name + '.stderr')).open('x')
        handles.extend((stdout, stderr))
        children[name] = subprocess.Popen(command, stdout=stdout, stderr=stderr,
                                          env=os.environ.copy())

    def chunk(name: str, window: dict) -> None:
        base = RUN / 'capture' / name
        first = base / 'captures/during'
        middle = base / 'trace-completion/during'
        launch(name + '-first', ['/usr/bin/python3', '-B', str(CAP / 'capture_live_c2.py'),
            '--phase', 'during', '--start-utc', window['start_utc'],
            '--end-utc', window['end_utc'], '--trace-source', 'JAEGER_OPENSEARCH_STORAGE',
            '--wait-until-end', '--output', str(first)])
        launch(name + '-completion', ['/usr/bin/python3', '-B', str(CAP / 'schedule_trace_completion_v2.py'),
            '--phase', 'during', '--start', window['start_utc'], '--end', window['end_utc'],
            '--first-capture', str(first), '--output', str(middle)])
        launch(name + '-late-audit', ['/usr/bin/python3', '-B', str(CAP / 'schedule_trace_v2.py'),
            '--phase', 'during', '--start', window['start_utc'], '--end', window['end_utc'],
            '--first', str(first), '--plus60', str(middle),
            '--output', str(base / 'trace-final/during'),
            '--audit-output', str(base / 'trace-late-audit/during')])
        launch(name + '-quality', ['/usr/bin/python3', '-B', str(HERE / 'pilot_chunk_audit.py'),
            '--chunk', name])

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, lambda signum, frame: (_ for _ in ()).throw(SystemExit(128 + signum)))
    try:
        phase = 'WAITING_FOR_FAULT_READY'
        fault_ready = FAULT_CONTROL / 'fault-ready.json'
        while time.time() < t0 - 60 and not fault_ready.is_file():
            time.sleep(.25)
        if not fault_ready.is_file():
            raise RuntimeError('Formal fault controller READY missing before T0')
        validate_fault_binding(plan, sha(RUN/'schedule.json'), read(fault_ready),
                               sha(FORMAL_MANIFEST))
        time.sleep(max(0, t0 - 170 - time.time()))
        heartbeat = read(FAULT_CONTROL/'heartbeat.json')
        if (heartbeat['run_id'] != RUN_ID or
                heartbeat['status'] != 'LIVE_ENVIRONMENT_PASS' or
                not 0 <= time.time()-stamp(heartbeat['completed_utc']) <= 30):
            raise RuntimeError('Fault observer heartbeat stale before T0')
        result['fault_plan_sha256'] = sha(FORMAL_PLAN)
        launch('state-machine', ['/usr/bin/python3', '-B', str(REAL_SCRIPTS/'sf004_workload.py'),
            '--registration', str(FORMAL_PLAN), '--registration-sha256', sha(FORMAL_PLAN),
            '--output', str(RUN / 'state-machine')])
        control_plan_path = CONTROL / 'control-plan.json'
        registered_workload = read(WORKLOAD_POLICY)['workload']
        if (registered_workload['duration_seconds'] != 1800 or
                registered_workload['profile'] != 'LOW' or
                registered_workload['policy_name'] != 'LOW_STABLE'):
            raise RuntimeError('Frozen SF-011 workload policy changed')
        write(control_plan_path, {'id': RUN_ID, 'start_utc': schedule['start_utc'],
                     'run_id': RUN_ID, 'fault_plan_sha256': sha(FORMAL_PLAN),
                     'minimum_fixed_stream_delayed_home_requests': 45,
                     'workload': registered_workload, 'seed': schedule['seed']})
        launch('controls', ['/usr/bin/python3', '-B', str(FIXED_STREAMS),
            '--plan', str(control_plan_path), '--seed', str(schedule['seed']), '--duration', '1800',
            '--registration', str(FORMAL_PLAN), '--registration-sha256', sha(FORMAL_PLAN),
            '--start-utc', schedule['start_utc'], '--base-url', BASE,
            '--output', str(RUN / 'controls')])
        for side in ('start', 'end'):
            launch('boundary-' + side, ['/usr/bin/python3', '-B',
                str(HERE / 'pilot_boundary_guard.py'), '--side', side])
        from sf004_safety import safety
        from sf004_preflight import check_five_host_heartbeat
        spawned: set[str] = set()
        initial_refused = initial['collector']['refused']
        tick = t0
        while time.time() <= t0 + 1800 + 420:
            time.sleep(max(0, tick - time.time()))
            if time.time() - tick > 5:
                raise RuntimeError('Safety monitor deadline missed')
            phase = 'SAFETY_CHECK_AT_' + utc(tick)
            environment = safety(expected)
            phase = 'COLLECTOR_CHECK_AT_' + utc(tick)
            collector = telemetry_state(initial['collector']['uid'])
            if collector['refused'] != initial_refused:
                raise RuntimeError('Collector refused-counter increased')
            phase = 'FIVE_HOST_HEARTBEAT_AT_' + utc(tick)
            check_five_host_heartbeat(read(plan['five_host_heartbeat_path']),
                                      manifest['plan_sha256'], time.time(),
                                      plan['five_host_monitor_sha256'])
            phase = 'MASTER_STORAGE_AT_' + utc(tick)
            used = int((CONTROL / 'json-byte-reservations').read_text()) if (CONTROL / 'json-byte-reservations').exists() else 0
            disk = {'root_free_bytes': shutil.disk_usage('/').free,
                    'data_free_bytes': shutil.disk_usage('/data/stage5-v3').free,
                    'raw_reserved_bytes': used}
            if disk['root_free_bytes'] < 5_000_000_000 or disk['data_free_bytes'] < 5_000_000_000:
                raise RuntimeError('Absolute five GB disk floor crossed')
            with (RUN / 'safety.jsonl').open('a') as stream:
                stream.write(json.dumps({'at_utc': utc(time.time()), 'environment': environment,
                                         'collector': collector, 'disk': disk}) + '\n')
            phase = 'CAPTURE_AND_CHILD_CHECK_AT_' + utc(tick)
            for name, window in windows.items():
                end = stamp(window['end_utc'])
                if time.time() > end + 420 and not (RUN / 'capture' / name / 'quality.json').exists():
                    raise RuntimeError('Mature chunk audit missing: ' + name)
                if name not in spawned and stamp(window['start_utc']) <= tick:
                    chunk(name, window)
                    spawned.add(name)
            failed = {name: child.returncode for name, child in children.items()
                      if child.poll() not in (None, 0)}
            if failed:
                raise RuntimeError('No-fault workload/capture failed: ' + repr(failed))
            if any((RUN / 'controls' / (name + '-failure.json')).exists()
                   for name in ('image_asset', 'cart_view')):
                raise RuntimeError('Ordinary control failed')
            fault_terminal = FAULT_CONTROL / 'fault-controller-terminal.json'
            if fault_terminal.is_file() and read(fault_terminal)['status'] != 'SF011_FAULT_SEQUENCE_COMPLETE_PENDING_CAPTURE':
                raise RuntimeError('Formal fault controller failed; capture cannot pass')
            if time.time() > t0 + 1200 and not fault_terminal.is_file():
                raise RuntimeError('Formal HTTP fault stage unresolved by deadline')
            if len(spawned) == 6 and all(child.poll() is not None for child in children.values()):
                break
            tick += 15
        else:
            raise RuntimeError('Thirty-minute capture did not finish by audit deadline')

        fault = read(FAULT_CONTROL / 'fault-controller-terminal.json')
        if (fault['status'] != 'SF011_FAULT_SEQUENCE_COMPLETE_PENDING_CAPTURE' or
                fault['faults'] or fault['stages']['http_delay']['delayed_requests'] < 60 or
                fault['stages'].get('http_recovered') is not True):
            raise RuntimeError('Real HTTP fault and recovery must finish before capture passes')

        quality = {name: read(RUN / 'capture' / name / 'quality.json') for name in windows}
        if any(row['status'] != 'CHUNK_QUALITY_PASS' for row in quality.values()):
            raise RuntimeError('One or more completed chunk audits failed')
        controls = read(RUN / 'controls/result-manifest.json')['streams']
        if any(controls[name]['successful_requests'] != 1800 or
               controls[name]['planned_requests'] != 1800 or
               controls[name]['missed_arrivals'] for name in
               ('image_asset', 'cart_view', 'home', 'recommendations')):
            raise RuntimeError('Fixed-rate ordinary control incomplete')
        requests = live_requests(RUN)
        business_errors = [item for item in requests if item['stream'] == 'business'
                           and not item['success']]
        proxies = {trace for row in quality.values() for trace in row['proxy_trace_ids']}
        sys.path.insert(0, str(CAP))
        from trace_identity import span_map
        boundary = {}
        for side in ('start', 'end'):
            path = RUN / 'private-boundary' / side
            if read(path / 'trace-storage-source.json')['unique_spans']:
                boundary.update(span_map(path))
        explained_boundary = [item['trace_id'] for item in requests
            if item['trace_id'].lower() not in proxies and any(
                boundary_match(item, span, t0, t0 + 1800) for _, span in boundary.values())]
        missing = [item['trace_id'] for item in requests
            if item['trace_id'].lower() not in proxies and item['trace_id'] not in explained_boundary]
        if missing:
            raise RuntimeError('Unexplained request-to-Proxy identity gaps: ' + str(len(missing)))
        if any(sha(Path(path)) != digest for path, digest in lock.items()):
            raise RuntimeError('Formal capture source hash changed')
        result.update(status='SF011_FORMAL_CAPTURE_PASS_PENDING_EXPORT',
                      completed_requests=len(requests), all_chunks_pass=True,
                      business_error_requests=len(business_errors),
                      http_delay=fault['stages']['http_delay'],
                      public_window_proxy_matched=len(requests) - len(explained_boundary),
                      private_boundary_verified_count=len(explained_boundary),
                      raw_reserved_bytes=int((CONTROL / 'json-byte-reservations').read_text()),
                      child_exit_codes={name: child.returncode for name, child in children.items()})
    except BaseException as error:
        result['error'] = type(error).__name__ + ': ' + str(error)
        failure = {'at_utc': utc(time.time()), 'phase': phase,
                   'error': result['error'], 'traceback': traceback.format_exc(),
                   'faults_injected_by_capture': 0}
        with (CONTROL / 'capture-failure-onset.json').open('x') as stream:
            json.dump(failure, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if result['status'] != 'SF011_FORMAL_CAPTURE_PASS_PENDING_EXPORT':
            subprocess.run(['systemctl', 'stop', FAULT_CONTROLLER_UNIT],
                           capture_output=True, text=True, timeout=20)
        for child in children.values():
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        for handle in handles:
            handle.close()
        result['completed_utc'] = utc(time.time())
        write(RUN / 'terminal.json', result)
        print(json.dumps(result), flush=True)
    if result['status'] != 'SF011_FORMAL_CAPTURE_PASS_PENDING_EXPORT':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
