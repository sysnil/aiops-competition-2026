#!/usr/bin/env python3
"""Exercise real module imports and plan-field contracts before creating a Case."""

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
FORMAL = OPS / 'sf013-formal-20261019'
IDENTITY = FORMAL / 'plans/current-identity.json'
RAW = Path('/data/stage5-v3/ops-competition-2026/runs/SF-013-ATTEMPT-01')
OUTPUT = OPS / 'sf013-adapter-20261019/results/entry-smoke.json'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    if RAW.exists() or OUTPUT.exists():
        raise RuntimeError('Entry smoke must precede a new formal Case')
    identity = json.loads(IDENTITY.read_text())
    age = time.time() - datetime.fromisoformat(identity['at_utc']).timestamp()
    if identity['status'] != 'CANDIDATE_CGROUPS_INSPECTED_NOT_QUALIFIED' or not 0 <= age <= 300:
        raise RuntimeError('Fresh current Pod/PID inspection required')
    os.environ['SF013_RUN'] = str(RAW)
    os.environ['KUBECONFIG'] = '/etc/kubernetes/admin.conf'
    sys.path.insert(0, str(FORMAL/'scripts'))
    from capture_controller import main as capture_main, source_lock
    from sf003_safety import safety
    from pilot_common import telemetry_state
    from sf003_contract import validate_plan_fields
    from sf003_observer import validate_plan_fields as observer_contract
    collector = telemetry_state()
    expected = [{key: row[key] for key in ('service','name','uid','node',
                'container_id','restart_count')} for row in identity['targets']]
    fields = {'id': 'SF-013-ATTEMPT-01', 'raw_root': str(RAW),
              'control_root': str(RAW/'control'),
              'collector_uid': collector['uid'],
              'targets_and_siblings': expected}
    validate_plan_fields(fields)
    observer_contract(fields)
    environment = safety(expected)
    if 'sys' in capture_main.__code__.co_varnames:
        raise RuntimeError('Capture entry still shadows module-level sys')
    locked = source_lock()
    if not locked or any(sha(path) != value for path, value in locked.items()):
        raise RuntimeError('Capture source lock changed during import smoke')
    payload = {'status': 'SF013_ENTRY_IMPORT_AND_PLAN_FIELDS_PASS_NO_CASE',
               'at_utc': datetime.now(timezone.utc).isoformat(),
               'identity_sha256': sha(IDENTITY),
               'targets': len(expected), 'collector_uid': collector['uid'],
               'environment_status': environment['status'],
               'capture_source_count': len(locked),
               'raw_case_directory_created': False,
               'faults_injected': 0, 'source_sha256': sha(__file__)}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open('x') as stream:
        json.dump(payload, stream, indent=2)
        stream.write('\n')
    print(json.dumps(payload))


if __name__ == '__main__':
    main()
