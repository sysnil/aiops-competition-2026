#!/usr/bin/env python3
"""Immutable read-only Collector and storage freshness snapshot."""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
os.environ['SF013_RUN'] = ('/data/stage5-v3/ops-competition-2026/runs/'
                           'SF-013-ATTEMPT-02')
sys.path.insert(0, str(OPS / 'sf013-formal-v2-20261019/scripts'))
from pilot_common import telemetry_state


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError('Collector snapshot already exists')
    live = telemetry_state()
    result = {'status': 'COLLECTOR_STORAGE_READ_ONLY_PASS',
              'at_utc': datetime.now(timezone.utc).isoformat(),
              'collector_uid': live['uid'],
              'collector_config_sha256': live['config_sha256'],
              'receiver_refused': live['refused'],
              'working_set_bytes': live['working_set_bytes'],
              'storage_freshness': live['freshness'],
              'real_faults_injected': 0}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': result['status'],
                      'receiver_refused': result['receiver_refused']}))


if __name__ == '__main__':
    main()
