"""Start SF-013 ATTEMPT-01 only after capture readiness is proven."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import time

from run_registry import load_bundle, sha, stamp


FORMAL = Path('/root/ops-competition-2026/sf013-formal-20261019')
RUN_ID = 'SF-013-ATTEMPT-01'
KUBECONFIG = '/etc/kubernetes/admin.conf'


def identity_age(plan, now):
    path = Path(plan['identity_file'])
    if sha(path) != plan['identity_sha256']:
        raise RuntimeError('Registered target identity hash changed')
    identity = json.loads(path.read_text())
    age = now - stamp(identity['at_utc'])
    if identity['status'] != 'CANDIDATE_CGROUPS_INSPECTED_NOT_QUALIFIED' or not 0 <= age <= 180:
        raise RuntimeError('Target identity must be refreshed within 180 seconds of startup')
    return age


def wait_capture_ready(plan, run, timeout_seconds=20):
    unit = plan['capture_controller_unit'] + '.service'
    evidence = run / 'control/capture-initial-health.json'
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        active = subprocess.run(['systemctl', 'is-active', unit],
                                capture_output=True, text=True, timeout=5)
        if active.returncode or active.stdout.strip() != 'active':
            raise RuntimeError('Capture controller exited before readiness')
        if evidence.is_file():
            initial = json.loads(evidence.read_text())
            collector = initial.get('collector', {})
            if (collector.get('uid') != plan['collector_uid'] or
                    collector.get('refused') != {'grpc': 0.0, 'http': 0.0} or
                    initial.get('environment', {}).get('status') != 'LIVE_ENVIRONMENT_PASS'):
                raise RuntimeError('Capture readiness evidence does not match registered safety')
            return sha(evidence)
        time.sleep(.1)
    raise RuntimeError('Capture readiness handshake timed out')


def command(plan, manifest, manifest_sha, role):
    if plan['id'] != RUN_ID or plan['raw_root'] != (
            '/data/stage5-v3/ops-competition-2026/runs/' + RUN_ID):
        raise RuntimeError('Run identity changed before unit creation')
    shared = ['--setenv=SF013_RUN=' + plan['raw_root'],
              '--setenv=KUBECONFIG=' + KUBECONFIG]
    if role == 'capture':
        return ['systemd-run', '--unit=' + plan['capture_controller_unit'],
                '--collect', '--property=RuntimeMaxSec=2500', *shared,
                '/usr/bin/python3', '-B', str(FORMAL / 'scripts/capture_controller.py')]
    if role == 'controller':
        return ['systemd-run', '--unit=' + plan['controller_unit'],
                '--collect', '--property=RuntimeMaxSec=1800', *shared,
                '/usr/bin/python3', '-B',
                '/root/ops-competition-2026/sf013-adapter-20261019/scripts/sf003_controller.py',
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
    current_identity_age = identity_age(plan, time.time())
    run = Path(plan['raw_root'])
    if (run / 'terminal.json').exists():
        raise RuntimeError('Attempt already has a terminal state')
    heartbeat_path = run / 'control/five-host-storage.json'
    heartbeat = json.loads(heartbeat_path.read_text())
    budget = Path('/root/ops-competition-2026/final-cases-20261019/sf013-storage/case-budget.json')
    if (heartbeat['status'] != 'DYNAMIC_STORAGE_PASS' or
            heartbeat['run_id'] != RUN_ID or
            heartbeat['plan_sha256'] != manifest['plan_sha256'] or
            heartbeat.get('case_budget_sha256') != sha(budget) or
            heartbeat.get('archive_target') != 'worker1' or
            not 0 <= time.time() - stamp(heartbeat['at_utc']) <= 30):
        raise RuntimeError('Fresh five-host storage heartbeat required')
    capture = command(plan, args.manifest, args.manifest_sha, 'capture')
    controller = command(plan, args.manifest, args.manifest_sha, 'controller')
    if args.validate_only:
        os.environ['SF013_RUN'] = plan['raw_root']
        os.environ['KUBECONFIG'] = KUBECONFIG
        import sys
        sys.path.insert(0, str(FORMAL / 'scripts'))
        from capture_controller import prereq
        expected, initial = prereq()
        if len(expected) != 4 or initial['collector']['uid'] != plan['collector_uid']:
            raise RuntimeError('Capture preflight differs from registered candidates')
        print(json.dumps({'status': 'STARTUP_COMMANDS_AND_CAPTURE_PREREQ_VALIDATED_NO_UNITS_CREATED',
                          'capture_unit': plan['capture_controller_unit'],
                          'controller_unit': plan['controller_unit'],
                          'candidate_count': len(expected),
                          'run_id': RUN_ID}))
        return
    subprocess.run(capture, capture_output=True, text=True, check=True, timeout=15)
    try:
        capture_health_sha = wait_capture_ready(plan, run)
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
              'capture_initial_health_sha256': capture_health_sha,
              'identity_age_seconds_at_start': current_identity_age,
              'required_run_environment': 'SF013_RUN_AND_KUBECONFIG_BOTH_UNITS'}
    target = run / 'control/startup-record.json'
    with target.open('x') as stream:
        json.dump(record, stream, indent=2)
        stream.write('\n')
    target.chmod(0o600)
    print(json.dumps(record))


if __name__ == '__main__':
    main()
