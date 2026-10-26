#!/usr/bin/env python3
"""SF-012 LOW-STABLE business workload with complete request-start identity."""

import argparse
import hashlib
import json
import threading
import time
import types
from datetime import datetime, timezone
from pathlib import Path


SOURCE = Path('/root/cross-layer-observability-control-rehearsal/scripts/workload_generator_rehearsal.py')
MACHINE = Path('/root/stage5-v3-observability-diagnostics/snapshots/reference/workload-state-machine-c3.yaml')
PROFILES = Path('/root/ops-competition-2026/low-stable-v4-20261017/design/load-profiles-v4b.yaml')
PEOPLE = Path('/root/cross-layer-workload-generation/design/people.json')
BASE = 'http://10.104.177.128:8080'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def neutralize_user_agent(source):
    original = "'User-Agent': 'stage5-workload-rehearsal/1.0'"
    if source.count(original) != 1:
        raise RuntimeError('Qualified business workload source changed')
    return source.replace(original, "'User-Agent': 'business-workload/1.0'")


def execute_with_start(original, *args, **kwargs):
    started = datetime.now(timezone.utc).isoformat()
    row = original(*args, **kwargs)
    if 'request_started_utc' in row:
        raise RuntimeError('Qualified request-start schema changed')
    row['request_started_utc'] = started
    return row


def run(plan, output, plan_sha):
    if (output.exists() or plan.get('id') != 'SF-012-ATTEMPT-04' or
            plan.get('formal_case') is not True or
            plan.get('duration_seconds') != 1800 or plan.get('profile') != 'LOW' or
            'request_delay' in plan):
        raise RuntimeError('Wrong SF-012 run, profile or fault capability')
    if plan_sha != sha(Path(plan['registration_file'])):
        raise RuntimeError('Registered workload plan hash changed')
    for source in (SOURCE, MACHINE, PROFILES, PEOPLE, Path(__file__)):
        if sha(source) != plan['source_sha256'].get(str(source)):
            raise RuntimeError('Workload source changed: ' + str(source))
    if not 1 <= stamp(plan['start_utc']) - time.time() <= 180:
        raise RuntimeError('Scheduled workload start is stale')
    module = types.ModuleType('registered_sf008_business_workload')
    module.__file__ = str(SOURCE)
    exec(compile(neutralize_user_agent(SOURCE.read_text()), str(SOURCE), 'exec'),
         module.__dict__)
    original = module.execute_request
    lock = threading.Lock()

    def recorded(*args, **kwargs):
        row = execute_with_start(original, *args, **kwargs)
        with lock:
            with (output / 'request-events-live.jsonl').open('a') as stream:
                stream.write(json.dumps(row) + '\n')
        return row

    module.execute_request = recorded
    args = argparse.Namespace(machine=str(MACHINE), profiles=str(PROFILES),
        people=str(PEOPLE), profile='LOW', seed=plan['seed'],
        start_utc=plan['start_utc'], duration=1800, base_url=BASE,
        allow_errors=True, output=str(output))
    outcome = module.run(args)
    rows = [json.loads(line) for line in
            (output / 'request-events-live.jsonl').read_text().splitlines()]
    if not rows or any('request_started_utc' not in row for row in rows):
        raise RuntimeError('Real business dispatch time missing')
    summary = {'status': 'SF012_BUSINESS_WORKLOAD_COMPLETE',
               'generator_status': outcome['status'],
               'planned_sessions': outcome['planned_sessions'],
               'request_count': outcome['request_count'],
               'request_events_sha256': sha(output / 'request-events-live.jsonl'),
               'faults_injected_by_workload': 0}
    with (output / 'workload-result.json').open('x') as stream:
        json.dump(summary, stream, indent=2)
        stream.write('\n')
    print(json.dumps(summary), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--registration', required=True, type=Path)
    parser.add_argument('--registration-sha256', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if sha(args.registration) != args.registration_sha256:
        raise RuntimeError('Registered workload plan hash mismatch')
    plan = json.loads(args.registration.read_text())
    plan['registration_file'] = str(args.registration)
    run(plan, args.output, args.registration_sha256)


if __name__ == '__main__':
    main()
