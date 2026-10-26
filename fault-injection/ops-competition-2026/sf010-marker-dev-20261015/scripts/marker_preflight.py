"""Read-only fresh safety and exact Worker Timer inventory for SF-010 markers."""

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from handshake import atomic
from master_transport import remote
from run_registry import load_bundle, sha, verify_timer_inventory
from timer_inventory import CODE


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
    sys.path.insert(0, '/root/ops-competition-2026/sf010-formal-20261015-v2/scripts')
    from sf010_safety import safety
    from pilot_common import telemetry_state
    capture = Path('/data/stage5-v3/ops-competition-2026/rehearsals/SF010-CONTROLLER-DRYRUN-02/schedule.json')
    if sha(capture) != plan['capture_schedule_sha256']:
        raise RuntimeError('Capture schedule changed')
    live = safety(plan['targets_and_siblings'])
    collector = telemetry_state(plan['collector_uid'])
    if collector['refused'] != json.loads(Path(plan['health_file']).read_text())['collector_refused']:
        raise RuntimeError('Collector refused increased')
    actual = json.loads(remote('w3', ['python3', '-c', CODE]))
    inventory = verify_timer_inventory(manifest, plan, args.manifest, args.manifest_sha,
                                       'k8s-worker3', list(actual), actual)
    registered_ids = {action['action_id'] for action in manifest['actions']}
    if not set(args.require_pending) <= registered_ids:
        raise RuntimeError('Unknown pending action requested')
    needed = {action['unit'] + '.timer' for action in manifest['actions']
              if action['action_id'] in args.require_pending}
    if not needed <= set(actual):
        raise RuntimeError('Registered pending Timer is absent')
    control = Path(plan['control_root'])
    result = {'status': 'SF010_MARKER_PREFLIGHT_PASS',
              'at_utc': datetime.now(timezone.utc).isoformat(),
              'tag': args.tag, 'manifest_sha256': args.manifest_sha,
              'environment': live, 'collector_uid': collector['uid'],
              'collector_refused': collector['refused'],
              'timer_inventory': inventory, 'required_pending': sorted(needed),
              'business_fault_permission': False}
    path = control / ('preflight-' + args.tag + '.json')
    atomic(path, result, True)
    print(json.dumps({'status': result['status'], 'path': str(path)}))


if __name__ == '__main__':
    main()
