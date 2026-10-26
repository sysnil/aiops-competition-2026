#!/usr/bin/env python3
"""SF-004 HTTP-only fault controller; no Worker fault capability."""

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time
from datetime import datetime, timezone

from handshake import Gate, atomic, digest
from run_registry import ROOT, load_bundle, sha, stamp
from sf004_preflight import check_five_host_heartbeat, marked_home_events


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text())


def command(argv, timeout=150):
    result = subprocess.run(list(map(str, argv)), capture_output=True,
                            text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stdout[-800:] + result.stderr[-1500:])
    return result.stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--manifest-sha', required=True)
    args = parser.parse_args()
    manifest, plan = load_bundle(args.manifest, args.manifest_sha)
    if os.uname().nodename != 'k8s-master':
        raise RuntimeError('Master-only HTTP controller')
    if any(sha(path) != value for path, value in plan['source_sha256'].items()):
        raise RuntimeError('Frozen HTTP source version changed')
    control = Path(plan['control_root'])
    atomic(control / 'fault-controller-started.json',
           {'run_id': manifest['run_id'], 'at_utc': now(),
            'manifest_sha256': args.manifest_sha}, True)
    result = {'status': 'SF004_FORMAL_SEQUENCE_FAILED', 'faults': {},
              'public_created': False, 'private_gt_created': False,
              'stages': {}, 'no_worker_timer': True}
    observer_started = granted = False
    observer_pid = gate = None

    def event(phase, **fields):
        with (control / 'fault-workflow.jsonl').open('a') as stream:
            stream.write(json.dumps({'at_utc': now(), 'phase': phase, **fields}) + '\n')

    def observer_alive():
        pid = command(['systemctl', 'show', plan['observer_unit'] + '.service',
                       '-p', 'MainPID', '--value']).strip()
        return int(pid or '0') == observer_pid

    def refresh(tag):
        event('PREFLIGHT_BEGIN', tag=tag)
        command(['python3', '-B', ROOT / 'scripts/sf004_preflight.py',
                 '--manifest', args.manifest, '--manifest-sha', args.manifest_sha,
                 '--tag', tag])
        path = control / ('preflight-' + tag + '.json')
        if read(path)['status'] != 'SF004_FORMAL_PREFLIGHT_PASS':
            raise RuntimeError('Private HTTP preflight did not pass')
        event('PREFLIGHT_PASS', tag=tag)
        return path

    def check():
        if (Path(plan['raw_root']) / 'state-machine/lease-violations.jsonl').exists():
            raise RuntimeError('Unleased ordinary business HTTP fault request')
        heartbeat = (read(control / 'heartbeat.json')
                     if (control / 'heartbeat.json').is_file() else None)
        if not gate.accept(heartbeat, time.time(), observer_pid, observer_alive()):
            raise RuntimeError('Observer heartbeat missing or stale')
        check_five_host_heartbeat(read(plan['five_host_heartbeat_path']),
                                  manifest['plan_sha256'], time.time(),
                                  plan['five_host_monitor_sha256'])
        if granted:
            issued = time.time()
            lease = {'run_id': manifest['run_id'],
                     'plan_sha256': manifest['plan_sha256'],
                     'permission': 'BOUND_HTTP_REQDELAY',
                     'safety_pass': True, 'issued_utc': now(),
                     'expires_utc': datetime.fromtimestamp(issued + 4,
                         timezone.utc).isoformat(),
                     'heartbeat_sha256': digest(heartbeat)}
            atomic(control / 'http-reqdelay-lease.json', lease)

    def wait(until):
        while time.time() < until:
            check()
            time.sleep(min(.25, max(0, until - time.time())))

    for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(signum, lambda value, frame:
                      (_ for _ in ()).throw(SystemExit(128 + value)))
    try:
        health = read(plan['health_file'])
        if (sha(plan['health_file']) != plan['health_sha256'] or
                health['current_environment_blockers'] or
                not 0 <= time.time() - stamp(health['at_utc']) <= 180):
            raise RuntimeError('Fresh formal health gate required')
        began = time.time()
        command(['systemd-run', '--unit=' + plan['observer_unit'],
                 '--setenv=SF004_RUN=' + str(plan['raw_root']),
                 '--property=RuntimeMaxSec=1600', 'python3', '-B',
                 ROOT / 'scripts/sf004_observer.py', '--manifest', args.manifest,
                 '--manifest-sha', args.manifest_sha])
        observer_started = True
        while not (control / 'observer-pid.json').is_file():
            if time.time() - began > 15:
                raise RuntimeError('Observer startup timed out')
            time.sleep(.05)
        observer_pid = read(control / 'observer-pid.json')['pid']
        gate = Gate(manifest['run_id'], plan['run_token'],
                    manifest['plan_sha256'],
                    plan['source_sha256'][str(ROOT / 'scripts/sf004_observer.py')],
                    began, stamp(plan['start_utc']))
        while not gate.accept(read(control / 'heartbeat.json')
                if (control / 'heartbeat.json').is_file() else None,
                time.time(), observer_pid, observer_alive()):
            time.sleep(.05)
        atomic(control / 'fault-ready.json',
               {'at_utc': now(), 'manifest_sha256': args.manifest_sha,
                'heartbeat_sha256': sha(control / 'heartbeat.json')}, True)
        event('READY_NO_WORKER_TIMER')
        refresh('ARMED')
        if time.time() >= stamp(plan['start_utc']) - 5:
            raise RuntimeError('READY missed T0')
        start = stamp(plan['start_utc']) + 900
        end = start + 60
        wait(start - 35)
        refresh('HTTP-PRE')
        wait(start - 5)
        check()
        granted = True
        event('HTTP_DELAY_AUTHORIZED')
        wait(end + 13)
        granted = False
        effect = marked_home_events([
            Path(plan['raw_root']) / 'state-machine/home-header-events.jsonl',
            Path(plan['raw_root']) / 'controls/home-header-events.jsonl'], start, end)
        if effect['delayed_requests'] < 60:
            raise RuntimeError('Real HTTP delay samples below frozen minimum')
        result['stages']['http_delay'] = effect
        wait(stamp(plan['start_utc']) + 1140)
        refresh('HTTP-RECOVERY')
        result['stages']['http_recovered'] = True
        result['status'] = 'SF004_FAULT_SEQUENCE_COMPLETE_PENDING_CAPTURE'
    except BaseException as error:
        result['error'] = type(error).__name__ + ': ' + str(error)
        event('ABORTING', error=result['error'])
    finally:
        granted = False
        if observer_started:
            subprocess.run(['systemctl', 'stop', plan['observer_unit'] + '.service'],
                           capture_output=True, timeout=25)
        if result['status'] != 'SF004_FAULT_SEQUENCE_COMPLETE_PENDING_CAPTURE':
            subprocess.run(['systemctl', 'stop',
                            plan['capture_controller_unit'] + '.service'],
                           capture_output=True, timeout=25)
        result['completed_utc'] = now()
        atomic(control / 'fault-controller-terminal.json', result, True)
        print(json.dumps({'status': result['status'],
                          'error': result.get('error')}), flush=True)
    if result['status'] != 'SF004_FAULT_SEQUENCE_COMPLETE_PENDING_CAPTURE':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
