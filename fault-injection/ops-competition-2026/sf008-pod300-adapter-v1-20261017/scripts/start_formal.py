"""Start both SF-008 ATTEMPT-06 units with the same bound run environment."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import time

from run_registry import load_bundle, sha, stamp


FORMAL = Path('/root/ops-competition-2026/sf008-pod300-formal-v1-20261017')
RUN_ID = 'SF-008-ATTEMPT-06'
KUBECONFIG = '/etc/kubernetes/admin.conf'


def command(plan, manifest, manifest_sha, role):
    if plan['id'] != RUN_ID or plan['raw_root'] != (
            '/data/stage5-v3/ops-competition-2026/runs/' + RUN_ID):
        raise RuntimeError('Run identity changed before unit creation')
    shared = ['--setenv=SF008_RUN=' + plan['raw_root'],
              '--setenv=KUBECONFIG=' + KUBECONFIG]
    if role == 'capture':
        return ['systemd-run', '--unit=' + plan['capture_controller_unit'],
                '--collect', '--property=RuntimeMaxSec=2500', *shared,
                '/usr/bin/python3', '-B', str(FORMAL / 'scripts/capture_controller.py')]
    if role == 'controller':
        return ['systemd-run', '--unit=' + plan['controller_unit'],
                '--collect', '--property=RuntimeMaxSec=1800', *shared,
                '/usr/bin/python3', '-B',
                '/root/ops-competition-2026/sf008-pod300-adapter-v1-20261017/scripts/sf003_controller.py',
                '--manifest', str(manifest), '--manifest-sha', manifest_sha]
    raise ValueError('Unknown formal unit role')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--manifest-sha', required=True)
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    if socket.gethostname() != 'k8s-master' or not Path(KUBECONFIG).is_file():
        raise RuntimeError('Master Kubernetes identity required')
    manifest, plan = load_bundle(args.manifest, args.manifest_sha)
    if any(sha(path) != expected for path, expected in plan['source_sha256'].items()):
        raise RuntimeError('Frozen source hash changed')
    health = json.loads(Path(plan['health_file']).read_text())
    if (sha(plan['health_file']) != plan['health_sha256'] or
            health['current_environment_blockers'] or
            not 0 <= time.time() - stamp(health['at_utc']) <= 180):
        raise RuntimeError('Fresh formal safety decision required')
    if not 90 <= stamp(plan['start_utc']) - time.time() <= 240:
        raise RuntimeError('T0 is outside capture startup window')
    run = Path(plan['raw_root'])
    if (run / 'terminal.json').exists():
        raise RuntimeError('Attempt already has a terminal state')
    heartbeat_path = run / 'control/five-host-storage.json'
    heartbeat = json.loads(heartbeat_path.read_text())
    if (heartbeat['status'] != 'DYNAMIC_STORAGE_PASS' or
            heartbeat['run_id'] != RUN_ID or
            heartbeat['plan_sha256'] != manifest['plan_sha256'] or
            not 0 <= time.time() - stamp(heartbeat['at_utc']) <= 30):
        raise RuntimeError('Fresh five-host storage heartbeat required')
    capture = command(plan, args.manifest, args.manifest_sha, 'capture')
    controller = command(plan, args.manifest, args.manifest_sha, 'controller')
    if args.validate_only:
        print(json.dumps({'status': 'STARTUP_COMMANDS_VALIDATED_NO_UNITS_CREATED',
                          'capture_unit': plan['capture_controller_unit'],
                          'controller_unit': plan['controller_unit'],
                          'run_id': RUN_ID}))
        return
    subprocess.run(capture, capture_output=True, text=True, check=True, timeout=15)
    try:
        subprocess.run(controller, capture_output=True, text=True, check=True, timeout=15)
    except Exception:
        subprocess.run(['systemctl', 'stop', plan['capture_controller_unit'] + '.service'],
                       capture_output=True, timeout=25)
        raise
    record = {'status': 'FORMAL_UNITS_STARTED_PENDING_READY', 'run_id': RUN_ID,
              'at_utc': datetime.now(timezone.utc).isoformat(),
              'manifest_sha256': args.manifest_sha,
              'capture_unit': plan['capture_controller_unit'],
              'controller_unit': plan['controller_unit'],
              'required_run_environment': 'SF008_RUN_AND_KUBECONFIG_BOTH_UNITS'}
    target = run / 'control/startup-record.json'
    with target.open('x') as stream:
        json.dump(record, stream, indent=2)
        stream.write('\n')
    target.chmod(0o600)
    print(json.dumps(record))


if __name__ == '__main__':
    main()
