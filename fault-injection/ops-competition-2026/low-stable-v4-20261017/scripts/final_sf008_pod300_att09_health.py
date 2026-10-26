"""Record the SF-008 Pod300 live safety gate after a formal attempt."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys


SCRIPTS = Path('/root/ops-competition-2026/sf008-pod300-formal-v4-20261017/scripts')
sys.path.insert(0, str(SCRIPTS))
from sf003_safety import safety


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
    result = {'status': 'FINAL_LIVE_ENVIRONMENT_PASS',
              'run_id': 'SF-008-ATTEMPT-09',
              'at_utc': datetime.now(timezone.utc).isoformat(),
              'identity_sha256': sha(args.identity), 'environment': environment}
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': result['status'],
                      'identity_sha256': result['identity_sha256']}))


if __name__ == '__main__':
    main()
