#!/usr/bin/env python3
"""One-shot harmless systemd/SSH integration; never imports a fault adapter."""

import hashlib
import json
import shlex
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


ROOT = '/root/ops-competition-2026/final-case-unified-execution-v1-20261019'
SOCKETS = {'master': '/tmp/sf-unified-master.sock',
           'k8s-worker2': '/tmp/sf-unified-w2.sock',
           'k8s-worker3': '/tmp/sf-unified-w3.sock'}
IPS = {'master': '192.168.53.52',
       'k8s-worker2': '192.168.53.54',
       'k8s-worker3': '192.168.53.55'}


def remote(host, *args, check=True):
    command = ' '.join(shlex.quote(str(arg)) for arg in args)
    return subprocess.run(['ssh', '-S', SOCKETS[host], '-o', 'BatchMode=yes',
                           'root@' + IPS[host], command], text=True,
                          capture_output=True, check=check, timeout=30)


def upload(host, source, destination):
    subprocess.run(['rsync', '-a', '-e',
                    'ssh -S ' + SOCKETS[host] + ' -o BatchMode=yes',
                    str(source), 'root@' + IPS[host] + ':' + destination],
                   check=True, capture_output=True, timeout=30)


def put_json(host, path, value, scratch):
    local = scratch / (uuid.uuid4().hex + '.json')
    local.write_text(json.dumps(value, sort_keys=True) + '\n')
    upload(host, local, path)


def sha(value):
    return hashlib.sha256((json.dumps(value, sort_keys=True) + '\n').encode()).hexdigest()


def protocol_digest(value):
    canonical = json.dumps(value, sort_keys=True, separators=(',', ':'),
                           ensure_ascii=True).encode('ascii')
    return hashlib.sha256(canonical).hexdigest()


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def test_worker(worker, case, kind, mechanism, duration, scratch,
                revoke_before=False):
    token = uuid.uuid4().hex[:8].upper()
    run = case + '-ATTEMPT-MARKERTEST-' + token
    action_id = run + '-' + kind
    unit = 'ops-' + case.lower().replace('-', '') + '-' + token.lower()
    master_dir = ROOT + '/runtime/' + run + '/' + action_id
    worker_dir = ROOT + '/marker-runtime/' + run + '/' + action_id
    remote('master', 'mkdir', '-p', '-m', '700', master_dir)
    remote(worker, 'mkdir', '-p', '-m', '700', worker_dir)
    master_boot = remote('master', 'cat', '/proc/sys/kernel/random/boot_id').stdout.strip()
    worker_boot = remote(worker, 'cat', '/proc/sys/kernel/random/boot_id').stdout.strip()
    planned = time.time() + 24
    manifest = {'status': 'HARMLESS_MARKERTEST_ONLY', 'run_id': run,
                'action_id': action_id, 'worker': worker, 'planned_utc': iso(planned)}
    manifest_sha = sha(manifest)
    manifest_path = master_dir + '/manifest.json'
    put_json('master', manifest_path, manifest, scratch)
    action = {'run_id': run, 'action_id': action_id, 'kind': kind,
              'worker': worker, 'mechanism': mechanism,
              'duration_seconds': duration, 'manifest_sha256': manifest_sha,
              'capability': 'MARKER_ONLY', 'effect': 'MARKER',
              'entrypoint': ROOT + '/scripts/marker_cli.py'}
    action_path = worker_dir + '/registration.json'
    put_json(worker, action_path, action, scratch)
    put_json(worker, worker_dir + '/auth-binding.json', {
        'run_id': run, 'action_id': action_id, 'manifest_sha256': manifest_sha,
        'capability': 'MARKER_ONLY', 'worker_host': worker,
        'worker_boot_id': worker_boot, 'master_boot_id': master_boot}, scratch)
    controller = unit + '-control'
    guard = unit + '-guard'
    remote('master', 'systemd-run', '--unit=' + controller,
           '--property=RuntimeMaxSec=70', '/usr/bin/sleep', '70')
    remote('master', 'systemd-run', '--unit=' + guard,
           '--property=RuntimeMaxSec=70', '/usr/bin/sleep', '70')
    try:
        pid = remote('master', 'systemctl', 'show', controller + '.service',
                     '-p', 'MainPID', '--value').stdout.strip()
        if not pid.isdigit() or int(pid) <= 0:
            raise RuntimeError('MARKER_CONTROLLER_UNIT_NOT_ACTIVE')
        state = {'status': 'REGISTERED', 'run_id': run, 'action_id': action_id,
                 'manifest_sha256': manifest_sha, 'manifest_path': manifest_path,
                 'capability': 'MARKER_ONLY', 'worker_host': worker,
                 'worker_boot_id': worker_boot, 'master_boot_id': master_boot,
                 'planned_utc': iso(planned), 'controller_unit': controller,
                 'controller_pid': int(pid), 'guard_unit': guard}
        put_json('master', master_dir + '/state.json', state, scratch)
        permit = {'status': 'PERMITTED', 'run_id': run,
                  'action_id': action_id, 'manifest_sha256': manifest_sha,
                  'state_sha256': protocol_digest(state),
                  'issued_utc': iso(time.time() - 1),
                  'expires_utc': iso(planned + 2)}
        put_json('master', master_dir + '/permit.json', permit, scratch)
        calendar = datetime.fromtimestamp(planned, timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f UTC')
        remote(worker, 'systemd-run', '--unit=' + unit,
               '--on-calendar=' + calendar,
               '--timer-property=AccuracySec=10ms',
               '--timer-property=RandomizedDelaySec=0',
               '--timer-property=Persistent=false',
               '--property=RuntimeMaxSec=12',
               '/usr/bin/python3', '-B', ROOT + '/scripts/marker_cli.py',
               '--registration', action_path, '--sha256', sha(action))
        if revoke_before:
            identity = {'run_id': run, 'action_id': action_id,
                        'manifest_sha256': manifest_sha}
            code = ("import sys;sys.path.insert(0," + repr(ROOT + '/scripts') +
                    ");from action_decision import revoke;revoke(" +
                    repr(master_dir) + ',' + repr(identity) +
                    ",'HARMLESS_MARKER_TEST_CANCEL')")
            remote('master', '/usr/bin/python3', '-B', '-c', code)
        time.sleep(max(0, planned - time.time() + 4))
        fired = remote(worker, 'test', '-f', worker_dir + '/fired.json', check=False)
        consumed = remote('master', 'test', '-f', master_dir + '/consumed.json', check=False)
        result = {'run_id': run, 'worker': worker, 'case': case,
                  'planned_utc': iso(planned), 'marker_fired': fired.returncode == 0,
                  'master_grant_consumed': consumed.returncode == 0,
                  'revoke_before': revoke_before,
                  'marker': None}
        if fired.returncode == 0:
            result['marker'] = json.loads(remote(worker, 'cat', worker_dir + '/fired.json').stdout)
        return result
    finally:
        remote(worker, 'systemctl', 'stop', unit + '.timer', check=False)
        remote(worker, 'systemctl', 'stop', unit + '.service', check=False)
        remote('master', 'systemctl', 'stop', controller + '.service', check=False)
        remote('master', 'systemctl', 'stop', guard + '.service', check=False)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp)
        results = [test_worker('k8s-worker2', 'SF-013', 'POD',
                               'PodMemoryStress', 300, scratch),
                   test_worker('k8s-worker2', 'SF-016', 'POD',
                               'PodMemoryStress', 300, scratch),
                   test_worker('k8s-worker3', 'SF-014', 'SERVICE',
                               'ServicePause', 30, scratch),
                   test_worker('k8s-worker3', 'SF-017', 'SERVICE',
                               'ServicePause', 30, scratch, revoke_before=True)]
    evidence = {'status': 'HARMLESS_MARKER_ONLY', 'results': results}
    output = Path(__file__).resolve().parents[1] / 'results' / 'live-marker-integration.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2) + '\n')
    print(json.dumps(evidence, indent=2))
    if not all((not row['marker_fired'] and not row['master_grant_consumed'])
               if row['revoke_before'] else
               (row['marker_fired'] and row['master_grant_consumed']) for row in results):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
