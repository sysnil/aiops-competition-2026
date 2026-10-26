#!/usr/bin/env python3
"""SF-010 controller dry-run; contains no business fault capability."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pilot_common import CONTROL, OFFSETS, RUN, EXPECTED_CONFIG, boundary_match, live_requests, telemetry_state


OPS = Path('/root/ops-competition-2026')
PROD = OPS / 'sf002-production-20261014'
HERE = Path(__file__).resolve().parent
CAP = PROD / 'capture'
DATA = Path('/data/stage5-v3/ops-competition-2026')
BASE = 'http://10.104.177.128:8080'
MACHINE = Path('/root/stage5-v3-observability-diagnostics/snapshots/reference/workload-state-machine-c3.yaml')
PROFILES = Path('/root/stage5-v3-autonomous-preparation/design/load-profiles-c3.yaml')
PEOPLE = Path('/root/cross-layer-workload-generation/design/people.json')
RUN_ID = 'SF010-CONTROLLER-DRYRUN'
SEED = 20261015033
IDENTITY = OPS / 'sf010-formal-20261015/plans/current-identity.json'
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


def read(path: Path) -> dict:
    return json.loads(path.read_text())


def source_lock() -> dict:
    source_files = [*HERE.glob('*.py'), IDENTITY, PROD / 'scripts/pilot_workload.py',
        PROD / 'scripts/pilot_controls.py', PROD / 'scripts/bounded_storage.py',
        CAP / 'capture_live_c2.py', CAP / 'schedule_trace_completion_v2.py',
        CAP / 'schedule_trace_v2.py', MACHINE, PROFILES, PEOPLE]
    return {str(path): sha(path) for path in source_files}


def prereq() -> tuple[list[dict], dict]:
    from sf010_safety import safety, storage

    if RUN.exists():
        raise RuntimeError('Never overwrite a rehearsal')
    if RUN.name != RUN_ID or not IDENTITY.is_file():
        raise RuntimeError('Exact dry-run and current identity required')
    identity = read(IDENTITY)
    if (identity['status'] != 'CANDIDATE_CGROUPS_INSPECTED_NOT_QUALIFIED' or
            not 0 <= time.time() - stamp(identity['at_utc']) <= 300):
        raise RuntimeError('Fresh current target inspection required')
    storage()
    units = subprocess.check_output(
        ['systemctl', 'list-units', '--type=service', '--state=running', '--no-legend', '--no-pager'],
        text=True, timeout=10,
    )
    allowed_self = 'ops-sf010-controller-dryrun-20261015.service'
    if any(('ops-sf002-' in line or 'ops-sf30-' in line or 'ops-b8-' in line or
             'ops-sf010-' in line)
           and allowed_self not in line for line in units.splitlines()):
        raise RuntimeError('Another competition run is active')
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
    os.environ.pop('SF_RUN_MANIFEST', None)
    os.environ.pop('SF_RUN_MANIFEST_SHA', None)
    expected, initial = prereq()
    RUN.mkdir(parents=True, mode=0o700)
    CONTROL.mkdir(mode=0o700)
    lock = source_lock()
    t0 = time.time() + 45
    windows = {
        f'chunk-{i:02d}': {
            'start_utc': utc(t0 + OFFSETS[i]),
            'end_utc': utc(t0 + OFFSETS[i + 1]),
        } for i in range(len(OFFSETS) - 1)
    }
    schedule = {
        'id': RUN_ID, 'formal_case': False, 'faults_injected': 0,
        'start_utc': utc(t0), 'end_utc': utc(t0 + 1800),
        'duration_seconds': 1800, 'seed': SEED, 'windows': windows,
        'trace_source': 'JAEGER_OPENSEARCH_STORAGE',
        'capture_offsets_seconds': OFFSETS, 'source_sha256': lock,
        'initial': initial, 'load': {'profile': 'MEDIUM', 'sessions_per_second': 1.4,
                                    'image_per_second': 1, 'cart_per_second': 2},
    }
    write(RUN / 'schedule.json', schedule)
    os.environ.update({
        'SF010_RUN': str(RUN), 'A04_CAPTURE_ROOT': str(RUN),
        'A04_BUDGET_LEDGER': str(CONTROL / 'json-byte-reservations'),
        'A04_JSON_CAP_BYTES': str(24 * GIB),
    })
    children: dict[str, subprocess.Popen] = {}
    handles = []
    result: dict = {'id': RUN_ID, 'status': 'NOFAULT_REHEARSAL_FAILED',
                    'faults_injected': 0, 'formal_case_created': False}

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
        launch('state-machine', ['/usr/bin/python3', '-B', str(PROD / 'scripts/pilot_workload.py'),
            '--machine', str(MACHINE), '--profiles', str(PROFILES), '--people', str(PEOPLE),
            '--profile', 'MEDIUM', '--seed', str(SEED), '--duration', '1800',
            '--start-utc', schedule['start_utc'], '--base-url', BASE,
            '--output', str(RUN / 'state-machine'), '--allow-errors'])
        plan = CONTROL / 'control-plan.json'
        registered_workload = read(PROD / 'plans/SF-PILOT-02-ATTEMPT-01/run-plan.json')['workload']
        registered_workload['duration_seconds'] = 1800
        write(plan, {'id': RUN_ID, 'start_utc': schedule['start_utc'],
                     'workload': registered_workload, 'seed': SEED})
        launch('controls', ['/usr/bin/python3', '-B', str(PROD / 'scripts/pilot_controls.py'),
            '--plan', str(plan), '--seed', str(SEED), '--duration', '1800',
            '--start-utc', schedule['start_utc'], '--base-url', BASE,
            '--output', str(RUN / 'controls')])
        for side in ('start', 'end'):
            launch('boundary-' + side, ['/usr/bin/python3', '-B',
                str(HERE / 'pilot_boundary_guard.py'), '--side', side])
        from competition_observation import safety
        spawned: set[str] = set()
        initial_refused = initial['collector']['refused']
        tick = t0
        while time.time() <= t0 + 1800 + 420:
            time.sleep(max(0, tick - time.time()))
            if time.time() - tick > 5:
                raise RuntimeError('Safety monitor deadline missed')
            environment = safety(expected)
            collector = telemetry_state(initial['collector']['uid'])
            if collector['refused'] != initial_refused:
                raise RuntimeError('Collector refused-counter increased')
            used = int((CONTROL / 'json-byte-reservations').read_text()) if (CONTROL / 'json-byte-reservations').exists() else 0
            disk = {'root_free_bytes': shutil.disk_usage('/').free,
                    'data_free_bytes': shutil.disk_usage('/data/stage5-v3').free,
                    'raw_reserved_bytes': used}
            if disk['root_free_bytes'] < 5_000_000_000 or disk['data_free_bytes'] < 5_000_000_000:
                raise RuntimeError('Absolute five GB disk floor crossed')
            with (RUN / 'safety.jsonl').open('a') as stream:
                stream.write(json.dumps({'at_utc': utc(time.time()), 'environment': environment,
                                         'collector': collector, 'disk': disk}) + '\n')
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
            if len(spawned) == 6 and all(child.poll() is not None for child in children.values()):
                break
            tick += 15
        else:
            raise RuntimeError('Thirty-minute capture did not finish by audit deadline')

        quality = {name: read(RUN / 'capture' / name / 'quality.json') for name in windows}
        if any(row['status'] != 'CHUNK_QUALITY_PASS' for row in quality.values()):
            raise RuntimeError('One or more completed chunk audits failed')
        controls = read(RUN / 'controls/result-manifest.json')['streams']
        if any(controls[name]['successful_requests'] != 1800 * rate or
               controls[name]['planned_requests'] != 1800 * rate or
               controls[name]['missed_arrivals'] for name, rate in (('image_asset', 1), ('cart_view', 2))):
            raise RuntimeError('Fixed-rate ordinary control incomplete')
        requests = live_requests(RUN)
        if any(not item['success'] for item in requests):
            raise RuntimeError('Unexpected no-fault ordinary request failure')
        proxies = {trace for row in quality.values() for trace in row['proxy_trace_ids']}
        import sys
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
            raise RuntimeError('No-fault request-to-Proxy identity gaps: ' + str(len(missing)))
        if any(sha(Path(path)) != digest for path, digest in lock.items()):
            raise RuntimeError('Capture source hash changed during rehearsal')
        result.update(status='NOFAULT_30MIN_CAPTURE_PASS',
                      completed_requests=len(requests), all_chunks_pass=True,
                      public_window_proxy_matched=len(requests) - len(explained_boundary),
                      private_boundary_verified_count=len(explained_boundary),
                      raw_reserved_bytes=int((CONTROL / 'json-byte-reservations').read_text()),
                      child_exit_codes={name: child.returncode for name, child in children.items()})
    except BaseException as error:
        result['error'] = type(error).__name__ + ': ' + str(error)
    finally:
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
    if result['status'] != 'NOFAULT_30MIN_CAPTURE_PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
