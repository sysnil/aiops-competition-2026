#!/usr/bin/env python3
"""Reproduce the formal first-tick checks with normal business load only."""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
RUN = Path('/data/stage5-v3/ops-competition-2026/rehearsals/SF010-FIRSTTICK-NOFAULT-V2-20261015')
FORMAL = OPS / 'sf010-formal-20261015-v7'
SOURCE = Path('/root/cross-layer-observability-control-rehearsal/scripts/workload_generator_rehearsal.py')
MACHINE = Path('/root/stage5-v3-observability-diagnostics/snapshots/reference/workload-state-machine-c3.yaml')
PROFILES = Path('/root/stage5-v3-autonomous-preparation/design/load-profiles-c3.yaml')
PEOPLE = Path('/root/cross-layer-workload-generation/design/people.json')
CONTROLS = OPS / 'sf002-production-20261014/scripts/pilot_controls.py'
BASE = 'http://10.104.177.128:8080'


def utc(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def main():
    if RUN.exists():
        raise RuntimeError('Diagnostic output already exists')
    os.environ['KUBECONFIG'] = '/etc/kubernetes/admin.conf'
    os.environ['SF010_RUN'] = '/data/stage5-v3/ops-competition-2026/runs/SF-010-ATTEMPT-04'
    sys.path.insert(0, str(FORMAL / 'scripts'))
    from sf010_safety import safety, storage
    from pilot_common import telemetry_state

    identity = json.loads((FORMAL / 'plans/current-identity.json').read_text())
    expected = [{key: row[key] for key in ('service', 'name', 'uid', 'node',
                'container_id', 'restart_count')} for row in identity['targets']]
    safety(expected)
    collector = telemetry_state()
    storage()
    t0 = time.time() + 20
    RUN.mkdir(parents=True, mode=0o700)
    plan = {'id': 'SF010-FIRSTTICK-NOFAULT-20261015', 'start_utc': utc(t0),
            'workload': {'duration_seconds': 120, 'profile': 'MEDIUM',
                         'rate_sessions_per_second': 1.4,
                         'control_streams': [
                             {'name': 'image_asset', 'rate_per_second': 1.0, 'seed_offset': 101},
                             {'name': 'cart_view', 'rate_per_second': 2.0, 'seed_offset': 103}],
                         'control_timeout_seconds': 8,
                         'control_concurrency_per_stream': {'image_asset': 2, 'cart_view': 4}},
            'seed': 41010}
    (RUN / 'plan.json').write_text(json.dumps(plan, indent=2) + '\n')
    commands = {
        'state-machine': ['/usr/bin/python3', '-B', str(SOURCE), '--machine', str(MACHINE),
                          '--profiles', str(PROFILES), '--people', str(PEOPLE),
                          '--profile', 'MEDIUM', '--seed', '41010', '--start-utc', utc(t0),
                          '--duration', '120', '--base-url', BASE, '--allow-errors',
                          '--output', str(RUN / 'state-machine')],
        'controls': ['/usr/bin/python3', '-B', str(CONTROLS), '--plan', str(RUN / 'plan.json'),
                     '--seed', '41010', '--duration', '120', '--start-utc', utc(t0),
                     '--base-url', BASE, '--output', str(RUN / 'controls')],
    }
    children = {}
    handles = []
    try:
        for name, command in commands.items():
            stdout = (RUN / (name + '.stdout')).open('x')
            stderr = (RUN / (name + '.stderr')).open('x')
            handles.extend((stdout, stderr))
            children[name] = subprocess.Popen(command, stdout=stdout, stderr=stderr)
        with (RUN / 'samples.jsonl').open('x') as stream:
            while time.time() < t0 + 140:
                now = time.time()
                row = {'at_utc': utc(now), 'relative_seconds': now - t0,
                       'children': {name: child.poll() for name, child in children.items()}}
                for name, check in (('safety', lambda: safety(expected)),
                                    ('collector', telemetry_state),
                                    ('storage', storage)):
                    try:
                        value = check()
                        row[name] = {'status': value.get('status', 'PASS')}
                        if name == 'collector':
                            row[name]['refused'] = value['refused']
                    except Exception as error:
                        row[name] = {'status': 'FAIL', 'error':
                                     type(error).__name__ + ': ' + str(error)}
                stream.write(json.dumps(row) + '\n')
                stream.flush()
                if any(child.poll() not in (None, 0) for child in children.values()):
                    break
                time.sleep(max(0, min(5, t0 + 140 - time.time())))
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
    rows = [json.loads(line) for line in (RUN / 'samples.jsonl').read_text().splitlines()]
    failures = [row for row in rows if any(row[key]['status'] == 'FAIL'
                for key in ('safety', 'collector', 'storage'))]
    complete = (not failures and len(rows) >= 20 and
                rows[-1]['relative_seconds'] >= 120 and
                all(child.returncode == 0 for child in children.values()))
    result = {'status': 'NOFAULT_FIRSTTICK_CHECKS_PASS' if complete else
              'NOFAULT_FIRSTTICK_CHECKS_FAILED', 'start_utc': utc(t0),
              'completed_utc': utc(time.time()), 'samples': len(rows),
              'failures': failures, 'faults_injected': 0,
              'children': {name: child.returncode for name, child in children.items()}}
    (RUN / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'status': result['status'], 'samples': len(rows),
                      'failures': len(failures), 'children': result['children']}))


if __name__ == '__main__':
    main()
