"""Read-only live recovery audit for the aborted SF-009 attempt."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys


SCRIPTS = Path('/root/ops-competition-2026/sf009-formal-v4-20261017/scripts')
os.environ['SF009_RUN'] = '/data/stage5-v3/ops-competition-2026/runs/SF-009-ATTEMPT-03'
sys.path.insert(0, str(SCRIPTS))
from sf009_safety import safety
from pilot_common import telemetry_state


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--identity', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError('Recovery audit already exists')
    identity = json.loads(args.identity.read_text())
    if identity['status'] != 'CANDIDATE_CGROUPS_INSPECTED_NOT_QUALIFIED':
        raise RuntimeError('Candidate identity evidence missing')
    expected = [{key: row[key] for key in ('service', 'name', 'uid', 'node',
                'container_id', 'restart_count')} for row in identity['targets']]
    environment = safety(expected)
    collector = telemetry_state()
    result = {'status': 'SF009_ATT03_FINAL_LIVE_ENVIRONMENT_PASS',
              'run_id': 'SF-009-ATTEMPT-03',
              'at_utc': datetime.now(timezone.utc).isoformat(),
              'identity_sha256': sha(args.identity),
              'environment': environment, 'collector': collector}
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': result['status'],
                      'identity_sha256': result['identity_sha256'],
                      'collector_refused': collector['refused']}))


if __name__ == '__main__':
    main()
