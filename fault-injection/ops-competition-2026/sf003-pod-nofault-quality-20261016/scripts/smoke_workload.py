#!/usr/bin/env python3
"""Normal MEDIUM traffic with the prospective request-start instrumentation."""

import argparse
import json
import sys
import threading
import types
from pathlib import Path


sys.path.insert(0, '/root/ops-competition-2026/sf003-real-adapter-v1-20261016/scripts')
from sf003_workload import (MACHINE, PEOPLE, PROFILES, SOURCE, execute_with_start,
                            neutralize_user_agent)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start-utc', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError('New independent workload output required')
    module = types.ModuleType('qualified_business_workload_nofault')
    module.__file__ = str(SOURCE)
    exec(compile(neutralize_user_agent(SOURCE.read_text()), str(SOURCE), 'exec'),
         module.__dict__)
    original = module.execute_request
    lock = threading.Lock()

    def recorded(*positional, **keywords):
        row = execute_with_start(original, *positional, **keywords)
        with lock:
            with (args.output / 'request-events-live.jsonl').open('a') as stream:
                stream.write(json.dumps(row) + '\n')
        return row

    module.execute_request = recorded
    settings = argparse.Namespace(machine=str(MACHINE), profiles=str(PROFILES),
        people=str(PEOPLE), profile='MEDIUM', seed=72003,
        start_utc=args.start_utc, duration=300,
        base_url='http://10.104.177.128:8080', allow_errors=True,
        output=str(args.output))
    result = module.run(settings)
    rows = [json.loads(line) for line in
            (args.output / 'request-events-live.jsonl').read_text().splitlines()]
    if not rows or any('request_started_utc' not in row for row in rows):
        raise RuntimeError('Business request start identity missing')
    print(json.dumps({'status': 'NOFAULT_REQUEST_START_COVERAGE_PASS',
                      'requests': len(rows), 'generator': result['status']}))


if __name__ == '__main__':
    main()
