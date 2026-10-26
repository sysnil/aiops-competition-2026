"""Read-only final environment and Collector audit for SF-011 ATTEMPT-04."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys


SCRIPTS = Path('/root/ops-competition-2026/sf011-http-formal-v4-20261018/scripts')
os.environ['SF011_RUN'] = '/data/stage5-v3/ops-competition-2026/runs/SF-011-ATTEMPT-04'
sys.path.insert(0, str(SCRIPTS))
from sf004_safety import safety
from pilot_common import telemetry_state


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--identity', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError('Final health record already exists')
    identity = json.loads(args.identity.read_text())
    if (identity['status'] != 'CANDIDATE_CGROUPS_INSPECTED_NOT_QUALIFIED' or
            len(identity['targets']) != 4):
        raise RuntimeError('Current candidate identity is incomplete')
    expected = [{key: row[key] for key in ('service', 'name', 'uid', 'node',
                'container_id', 'restart_count')} for row in identity['targets']]
    environment = safety(expected)
    collector = telemetry_state()
    initial_path = Path(os.environ['SF011_RUN']) / 'control/capture-initial-health.json'
    initial = json.loads(initial_path.read_text())['collector']
    if collector['uid'] != initial['uid'] or collector['refused'] != initial['refused']:
        raise RuntimeError('Collector identity changed or refusal counter increased')
    result = {'status': 'SF011_ATT04_FINAL_LIVE_ENVIRONMENT_PASS',
              'run_id': 'SF-011-ATTEMPT-04',
              'at_utc': datetime.now(timezone.utc).isoformat(),
              'identity_sha256': sha(args.identity),
              'capture_initial_health_sha256': sha(initial_path),
              'environment': environment, 'collector': collector}
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': result['status'],
                      'identity_sha256': result['identity_sha256'],
                      'collector_refused': collector['refused']}))


if __name__ == '__main__':
    main()
