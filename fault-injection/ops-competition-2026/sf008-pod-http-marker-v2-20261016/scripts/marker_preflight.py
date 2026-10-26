"""Read-only safety and exact Worker Timer inventory for SF-009 markers."""

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from handshake import atomic
from master_transport import remote
from run_registry import load_bundle, sha, verify_timer_inventory
from timer_inventory import CODE


def wait_for_quality(paths, cutoff):
    while any(not path.is_file() for path in paths) and time.time() < cutoff:
        time.sleep(.25)
    if time.time() >= cutoff or any(not path.is_file() or
            json.loads(path.read_text())['status'] != 'CHUNK_QUALITY_PASS'
            for path in paths):
        raise RuntimeError('Mature pre-fault chunk quality missing or failed')
    return {str(path): sha(path) for path in paths}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--manifest-sha', required=True)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--require-pending', action='append', default=[])
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Z0-9-]+', args.tag):
        raise RuntimeError('Invalid preflight label')
    manifest, plan = load_bundle(args.manifest, args.manifest_sha)
    import sys
    import os
    sys.path.insert(0, '/root/ops-competition-2026/sf008-pod-http-nofault-v2-20261016/scripts')
    os.environ['SF008_RUN'] = '/data/stage5-v3/ops-competition-2026/rehearsals/SF008-POD-HTTP-NOFAULT-02'
    from sf008_safety import safety
    from pilot_common import telemetry_state
    capture = Path('/data/stage5-v3/ops-competition-2026/rehearsals/SF008-POD-HTTP-NOFAULT-02/schedule.json')
    if sha(capture) != plan['capture_schedule_sha256']:
        raise RuntimeError('Capture schedule changed')
    quality_files = []
    quality_hashes = {}
    if args.tag in ('E1', 'E2'):
        count = 1 if args.tag == 'E1' else 2
        deadline = manifest['actions'][count - 1]['planned_utc']
        from run_registry import stamp
        cutoff = stamp(deadline) - 8
        quality_files = [capture.parent / 'capture' / f'chunk-{index:02d}' / 'quality.json'
                         for index in range(count)]
        quality_hashes = wait_for_quality(quality_files, cutoff)
    live = safety(plan['targets_and_siblings'])
    collector = telemetry_state(plan['collector_uid'])
    if collector['refused'] != json.loads(Path(plan['health_file']).read_text())['collector_refused']:
        raise RuntimeError('Collector refused increased')
    actual = json.loads(remote('w2', ['python3', '-c', CODE]))
    inventory = verify_timer_inventory(manifest, plan, args.manifest, args.manifest_sha,
                                       'k8s-worker2', list(actual), actual)
    registered_ids = {action['action_id'] for action in manifest['actions']}
    if not set(args.require_pending) <= registered_ids:
        raise RuntimeError('Unknown pending action requested')
    needed = {action['unit'] + '.timer' for action in manifest['actions']
              if action['action_id'] in args.require_pending}
    if not needed <= set(actual):
        raise RuntimeError('Registered pending Timer is absent')
    control = Path(plan['control_root'])
    result = {'status': 'SF008_MARKER_PREFLIGHT_PASS',
              'at_utc': datetime.now(timezone.utc).isoformat(),
              'tag': args.tag, 'manifest_sha256': args.manifest_sha,
              'environment': live, 'collector_uid': collector['uid'],
              'collector_refused': collector['refused'],
              'timer_inventory': inventory, 'required_pending': sorted(needed),
              'capture_quality_sha256': quality_hashes,
              'business_fault_permission': False}
    path = control / ('preflight-' + args.tag + '.json')
    atomic(path, result, True)
    print(json.dumps({'status': result['status'], 'path': str(path)}))


if __name__ == '__main__':
    main()
