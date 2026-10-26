#!/usr/bin/env python3
"""Explicit Master launch. --validate-only creates no Attempt or systemd unit."""

import argparse
import json
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from control_contract import ROOT, load_manifest, read_file
from storage_policy import check


def commands(manifest_path, manifest_sha, manifest):
    return {
        role: ['systemd-run', '--unit=' + manifest[role + '_unit'],
               '--collect', '--property=RuntimeMaxSec=2300',
               '--property=Environment=KUBECONFIG=/etc/kubernetes/admin.conf',
               '/usr/bin/python3', '-B', str(ROOT / 'scripts' / script),
               '--manifest', str(manifest_path), '--manifest-sha', manifest_sha]
        for role, script in (('monitor', 'safety_monitor.py'),
                             ('guard', 'formal_guard.py'),
                             ('controller', 'formal_controller.py'))
    }


def check_start(manifest, *, clock=time.time):
    if socket.gethostname() != 'k8s-master':
        raise ValueError('FORMAL_MASTER_ONLY')
    if manifest.get('safety') != 'FRESH_GLOBAL_SAFETY_PASS':
        raise ValueError('FRESH_GLOBAL_SAFETY_MISSING')
    t0 = datetime.fromisoformat(manifest['start_utc']).timestamp()
    if not 60 <= t0 - clock() <= 180:
        raise ValueError('CAPTURE_START_NOT_DEPLOYED_EARLY')
    if min(datetime.fromisoformat(a['planned_start_utc']).timestamp()
           for a in manifest['actions']) < t0 + 600:
        raise ValueError('FORMAL_BASELINE_SHORTER_THAN_600_SECONDS')
    approval = read_file(manifest['human_approval']['path'],
                         manifest['human_approval']['sha256'])
    if (approval.get('status') != 'HUMAN_PRODUCTION_APPROVED' or
            approval.get('run_id') != manifest['run_id']):
        raise ValueError('HUMAN_PRODUCTION_APPROVAL_MISSING')
    safety = read_file(manifest['safety_snapshot']['path'],
                       manifest['safety_snapshot']['sha256'])
    if (safety.get('status') != 'FRESH_GLOBAL_SAFETY_PASS' or
            safety.get('run_id') != manifest['run_id'] or
            not 0 <= clock() - datetime.fromisoformat(safety['at_utc']).timestamp() <= 60):
        raise ValueError('FRESH_GLOBAL_SAFETY_MISSING')
    snapshot = read_file(manifest['storage_snapshot']['path'],
                         manifest['storage_snapshot']['sha256'])
    if (snapshot.get('status') != 'FIVE_HOST_STORAGE_MEASURED' or
            snapshot.get('run_id') != manifest['run_id'] or
            not 0 <= clock() - datetime.fromisoformat(snapshot['at_utc']).timestamp() <= 30):
        raise ValueError('FIVE_HOST_STORAGE_SNAPSHOT_STALE')
    free = {(row['host'], row['mount']): row['free_bytes']
            for row in snapshot['filesystems']}
    result = check(manifest['run_id'].split('-ATTEMPT-')[0], free)
    if result['status'] != 'STORAGE_PASS':
        raise ValueError('FIVE_HOST_STORAGE_BLOCKED')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--manifest-sha', required=True)
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    manifest = load_manifest(args.manifest, args.manifest_sha)
    planned = commands(args.manifest, args.manifest_sha, manifest)
    if args.validate_only:
        print(json.dumps({'status': 'COMMANDS_VALIDATED_NO_UNITS_CREATED',
                          'commands': planned, 'run_id': manifest['run_id']}))
        return
    if manifest['mode'] != 'FORMAL_CANDIDATE':
        raise RuntimeError('REHEARSAL_USES_SEPARATE_HARMLESS_LAUNCHER')
    check_start(manifest)
    if any((ROOT / 'runtime' / manifest['run_id']).glob('**/terminal.json')):
        raise RuntimeError('ATTEMPT_ALREADY_HAS_TERMINAL')
    subprocess.run(planned['monitor'], check=True, capture_output=True,
                   text=True, timeout=15)
    try:
        path = ROOT / 'runtime' / manifest['run_id'] / 'master' / 'safety-heartbeat.json'
        deadline = time.monotonic() + 15
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(.1)
        if not path.exists() or json.loads(path.read_text()).get('status') != \
                'DYNAMIC_STORAGE_AND_BASIC_HEALTH_PASS':
            raise RuntimeError('FORMAL_INITIAL_MONITOR_BLOCKED')
        subprocess.run(planned['guard'], check=True, capture_output=True,
                       text=True, timeout=15)
        subprocess.run(planned['controller'], check=True, capture_output=True,
                       text=True, timeout=15)
    except BaseException:
        subprocess.run(['systemctl', 'stop', manifest['guard_unit'] + '.service'],
                       capture_output=True, timeout=20)
        subprocess.run(['systemctl', 'stop', manifest['monitor_unit'] + '.service'],
                       capture_output=True, timeout=20)
        raise
    print(json.dumps({'status': 'FORMAL_CONTROL_UNITS_STARTED_PENDING_SAFETY',
                      'run_id': manifest['run_id']}))


if __name__ == '__main__':
    main()
