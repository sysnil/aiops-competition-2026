#!/usr/bin/env python3
"""One complete no-fault chunk through immutable trace completion and audit."""

import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
RUN = Path('/data/stage5-v3/ops-competition-2026/rehearsals/SF003-POD-NOFAULT-QUALITY-20261016')
HERE = Path(__file__).resolve().parent
CAP = OPS / 'sf002-production-20261014/capture'
CONTROLS = OPS / 'sf002-production-20261014/scripts/pilot_controls.py'
BASE = 'http://10.104.177.128:8080'


def utc(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    if RUN.exists():
        raise RuntimeError('Rehearsal output already exists')
    os.environ.update(SF003_RUN=str(RUN), KUBECONFIG='/etc/kubernetes/admin.conf',
                      A04_CAPTURE_ROOT=str(RUN),
                      A04_BUDGET_LEDGER=str(RUN / 'json-byte-reservations'),
                      A04_JSON_CAP_BYTES=str(8 * 1024**3))
    sys.path.insert(0, str(HERE))
    from pilot_common import telemetry_state
    sys.path.insert(0, '/root/ops-competition-2026/sf003-formal-v1-20261016/scripts')
    from sf003_safety import safety

    identity = json.loads((OPS / 'sf003-formal-v1-20261016/plans/current-identity.json').read_text())
    expected = [{key: row[key] for key in ('service', 'name', 'uid', 'node',
                'container_id', 'restart_count')} for row in identity['targets']]
    safety(expected)
    initial = telemetry_state()
    t0 = time.time() + 65
    end = t0 + 300
    RUN.mkdir(parents=True, mode=0o700)
    (RUN / 'schedule.json').write_text(json.dumps({
        'id': RUN.name, 'start_utc': utc(t0), 'end_utc': utc(end),
        'windows': {'chunk-00': {'start_utc': utc(t0), 'end_utc': utc(end)}},
        'trace_source': 'JAEGER_OPENSEARCH_STORAGE', 'faults_injected': 0}, indent=2) + '\n')
    control_plan = {'id': RUN.name, 'start_utc': utc(t0), 'seed': 72003,
        'workload': {'duration_seconds': 300, 'profile': 'MEDIUM',
                     'rate_sessions_per_second': 1.4,
                     'control_streams': [
                         {'name': 'image_asset', 'rate_per_second': 1.0, 'seed_offset': 101},
                         {'name': 'cart_view', 'rate_per_second': 2.0, 'seed_offset': 103}],
                     'control_timeout_seconds': 8,
                     'control_concurrency_per_stream': {'image_asset': 2, 'cart_view': 4}}}
    (RUN / 'control-plan.json').write_text(json.dumps(control_plan, indent=2) + '\n')
    first = RUN / 'capture/chunk-00/captures/during'
    plus60 = RUN / 'capture/chunk-00/trace-completion/during'
    final = RUN / 'capture/chunk-00/trace-final/during'
    late = RUN / 'capture/chunk-00/trace-late-audit/during'
    commands = {
        'state-machine': ['/usr/bin/python3', '-B', str(HERE / 'smoke_workload.py'),
                          '--start-utc', utc(t0), '--output', str(RUN / 'state-machine')],
        'controls': ['/usr/bin/python3', '-B', str(CONTROLS), '--plan', str(RUN / 'control-plan.json'),
                     '--seed', '72003', '--duration', '300', '--start-utc', utc(t0),
                     '--base-url', BASE, '--output', str(RUN / 'controls')],
        'boundary-start': ['/usr/bin/python3', '-B', str(HERE / 'pilot_boundary_guard.py'),
                           '--side', 'start'],
        'boundary-end': ['/usr/bin/python3', '-B', str(HERE / 'pilot_boundary_guard.py'),
                         '--side', 'end'],
        'capture': ['/usr/bin/python3', '-B', str(CAP / 'capture_live_c2.py'),
                    '--phase', 'during', '--start-utc', utc(t0), '--end-utc', utc(end),
                    '--trace-source', 'JAEGER_OPENSEARCH_STORAGE', '--wait-until-end',
                    '--output', str(first)],
        'plus60': ['/usr/bin/python3', '-B', str(CAP / 'schedule_trace_completion_v2.py'),
                   '--phase', 'during', '--start', utc(t0), '--end', utc(end),
                   '--first-capture', str(first), '--output', str(plus60)],
        'plus120-plus240': ['/usr/bin/python3', '-B', str(CAP / 'schedule_trace_v2.py'),
                            '--phase', 'during', '--start', utc(t0), '--end', utc(end),
                            '--first', str(first), '--plus60', str(plus60),
                            '--output', str(final), '--audit-output', str(late)],
        'quality': ['/usr/bin/python3', '-B', str(HERE / 'pilot_chunk_audit.py'),
                    '--chunk', 'chunk-00'],
    }
    handles = []
    children = {}
    result = {'status': 'NOFAULT_QUALITY_SMOKE_FAILED', 'faults_injected': 0,
              'start_utc': utc(t0), 'end_utc': utc(end),
              'source_sha256': {str(path): sha(path) for path in HERE.glob('*.py')}}
    try:
        for name, command in commands.items():
            stdout = (RUN / (name + '.stdout')).open('x')
            stderr = (RUN / (name + '.stderr')).open('x')
            handles.extend((stdout, stderr))
            children[name] = subprocess.Popen(command, stdout=stdout, stderr=stderr,
                                              env=os.environ.copy())
        while time.time() < end + 330:
            failures = {name: child.returncode for name, child in children.items()
                        if child.poll() not in (None, 0)}
            if failures:
                raise RuntimeError('Rehearsal child failed: ' + repr(failures))
            current = telemetry_state(initial['uid'])
            if current['refused'] != initial['refused']:
                raise RuntimeError('Collector refusal increased')
            if all(child.poll() == 0 for child in children.values()):
                quality = json.loads((RUN / 'capture/chunk-00/quality.json').read_text())
                if quality['status'] != 'CHUNK_QUALITY_PASS':
                    raise RuntimeError('Completed chunk quality failed')
                result.update(status='NOFAULT_REQUEST_START_QUALITY_PASS',
                              quality_sha256=sha(RUN / 'capture/chunk-00/quality.json'),
                              matched_requests=quality['matched_prefix_requests'],
                              final_spans=quality['final_spans'],
                              collector_refused=current['refused'])
                break
            time.sleep(15)
        else:
            raise RuntimeError('Rehearsal audit deadline exceeded')
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
        result['child_exit_codes'] = {name: child.returncode for name, child in children.items()}
        with (RUN / 'terminal.json').open('x') as stream:
            json.dump(result, stream, indent=2)
            stream.write('\n')
        print(json.dumps({'status': result['status'], 'error': result.get('error')}), flush=True)
    if result['status'] != 'NOFAULT_REQUEST_START_QUALITY_PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
