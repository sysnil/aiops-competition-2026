#!/usr/bin/env python3
"""Dedicated SF-010 ServicePause30 controller with a separately leased HTTP delay."""

import argparse
import json
import os
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from handshake import Gate, atomic, digest
from master_transport import clock, copy, optional, put, remote
from run_registry import ROOT, load_bundle, sha, stamp
from sf010_preflight import check_five_host_heartbeat


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


def check_http_headers(path, plan, at):
    rows = [json.loads(line) for line in Path(path).read_text().splitlines()]
    start = stamp(plan['start_utc']) + 960
    end = start + 60
    delayed = 0
    for row in rows:
        timestamp = stamp(row['at_utc'])
        marked = row['delay_header_applied'] is True
        if marked and not start <= timestamp < end:
            raise RuntimeError('HTTP delay was applied outside registered window')
        delayed += marked
    if delayed < 60 or at < end:
        raise RuntimeError('Insufficient real business HTTP delay samples')
    return {'home_requests': len(rows), 'delayed_home_requests': delayed}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--manifest-sha', required=True)
    args = parser.parse_args()
    manifest, plan = load_bundle(args.manifest, args.manifest_sha)
    if os.uname().nodename != 'k8s-master':
        raise RuntimeError('Master-only controller')
    if any(sha(path) != expected for path, expected in plan['source_sha256'].items()):
        raise RuntimeError('Formal source version changed')
    control = Path(plan['control_root'])
    atomic(control / 'fault-controller-started.json',
           {'run_id': manifest['run_id'], 'at_utc': now(),
            'manifest_sha256': args.manifest_sha}, True)
    action = manifest['actions'][0]
    runtime = Path(action['runtime'])
    result = {'status': 'SF010_FORMAL_SEQUENCE_FAILED', 'faults': {},
              'public_created': False, 'private_gt_created': False,
              'actual_onsets': {}, 'stages': {}}
    armed = observer_started = granted_service = granted_http = False
    observer_pid = gate = None
    last_clock = 0
    clock_proof = None

    def event(phase, **details):
        with (control / 'fault-workflow.jsonl').open('a') as stream:
            stream.write(json.dumps({'at_utc': now(), 'phase': phase,
                                     **details}) + '\n')

    def backend(verb):
        return remote('w3', ['python3', '-B', ROOT / 'scripts/fault_worker.py',
            verb, '--manifest', args.manifest, '--manifest-sha', args.manifest_sha,
            '--action-id', action['action_id']])

    def refresh(tag, pending=False):
        call = ['python3', '-B', ROOT / 'scripts/sf010_preflight.py',
                '--manifest', args.manifest, '--manifest-sha', args.manifest_sha,
                '--tag', tag]
        if pending:
            call += ['--require-pending', action['action_id']]
        event('PREFLIGHT_BEGIN', tag=tag)
        command(call)
        path = control / ('preflight-' + tag + '.json')
        if read(path)['status'] != 'SF010_FORMAL_PREFLIGHT_PASS':
            raise RuntimeError('Private preflight did not pass')
        event('PREFLIGHT_PASS', tag=tag)
        return path

    def observer_alive():
        actual = command(['systemctl', 'show', plan['observer_unit']+'.service',
                          '-p', 'MainPID', '--value']).strip()
        return int(actual or '0') == observer_pid

    def check():
        nonlocal last_clock, clock_proof
        if (Path(plan['raw_root'])/'state-machine/lease-violations.jsonl').exists():
            raise RuntimeError('HTTP delay lease was missing or invalid for a business request')
        heartbeat = read(control / 'heartbeat.json') if (control/'heartbeat.json').is_file() else None
        if not gate.accept(heartbeat, time.time(), observer_pid, observer_alive()):
            raise RuntimeError('Observer READY/heartbeat not established')
        check_five_host_heartbeat(read(plan['five_host_heartbeat_path']),
                                  manifest['plan_sha256'], time.time(),
                                  plan['five_host_monitor_sha256'])
        if time.monotonic() - last_clock >= 15:
            clock_proof = clock('w3', 3)
            last_clock = time.monotonic()
            if (abs(clock_proof['worker_minus_master_seconds']) +
                    clock_proof['uncertainty_seconds'] > .5):
                raise RuntimeError('Master/Worker clock uncertainty too large')
        if armed:
            actual = optional('w3', runtime/'actual-start.json')
            terminal = optional('w3', runtime/'terminal.json')
            planned = stamp(action['planned_utc'])
            if actual and not result['actual_onsets']:
                feedback = (time.time() - stamp(actual['actual_worker_utc']) +
                            clock_proof['worker_minus_master_seconds'] +
                            clock_proof['uncertainty_seconds'])
                actual['feedback_upper_seconds'] = feedback
                actual['seen_master_utc'] = now()
                atomic(control/'service-onset.json', actual, True)
                result['actual_onsets'][action['action_id']] = actual
                if not actual['pass'] or feedback > 2:
                    raise RuntimeError('Service onset or feedback exceeded two seconds')
            if time.time() > planned + 2.5 and not actual and not terminal:
                raise RuntimeError('Worker did not report Service onset immediately')
            if terminal and not result['faults']:
                injection = terminal.get('injection', {})
                if (terminal['status'] != 'FORMAL_FAULT_COMPLETE' or
                        abs(injection.get('duration_seconds', 0) - 30) > 2 or
                        injection.get('cleanup', {}).get('status') !=
                        'COMPETITION_CLEANUP_ACTIONS_VERIFIED'):
                    raise RuntimeError('Service fault or exact-PID cleanup failed')
                result['faults'][action['action_id']] = injection
            if granted_service and not terminal:
                issued = time.time()
                lease = {'run_id': manifest['run_id'],
                         'action_id': action['action_id'],
                         'manifest_sha256': args.manifest_sha,
                         'permission': 'BOUND_QUALIFIED_FAULT',
                         'authorization_sha256': sha(control/'service-authorization.json'),
                         'issued_utc': now(),
                         'expires_utc': datetime.fromtimestamp(issued+4,timezone.utc).isoformat(),
                         'clock': clock_proof, 'safety_pass': True,
                         'heartbeat_sequence': heartbeat['sequence'],
                         'heartbeat_sha256': digest(heartbeat)}
                put('w3', runtime/'lease.json', lease, True)
        if granted_http:
            issued = time.time()
            lease = {'run_id': manifest['run_id'],
                     'plan_sha256': manifest['plan_sha256'],
                     'permission': 'BOUND_HTTP_REQDELAY', 'safety_pass': True,
                     'issued_utc': now(),
                     'expires_utc': datetime.fromtimestamp(issued+4,timezone.utc).isoformat(),
                     'heartbeat_sha256': digest(heartbeat)}
            atomic(control/'http-reqdelay-lease.json', lease)

    def wait(until):
        while time.time() < until:
            check()
            time.sleep(min(.25, max(0, until-time.time())))

    for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(signum, lambda value, frame: (_ for _ in ()).throw(SystemExit(128+value)))
    try:
        health = read(plan['health_file'])
        if (sha(plan['health_file']) != plan['health_sha256'] or
                health['current_environment_blockers'] or
                not 0 <= time.time()-stamp(health['at_utc']) <= 180):
            raise RuntimeError('Fresh formal health gate required')
        remote('w3', ['mkdir', '-p', ROOT/'scripts', args.manifest.parent])
        for name in ('run_registry.py','handshake.py','fault_worker.py'):
            copy('w3', ROOT/'scripts'/name, ROOT/'scripts'/name)
        for path in (manifest['plan_path'], manifest['schedule_lock_path'],
                     args.manifest, action['qualified_registration']):
            copy('w3', path, path)
        began = time.time()
        command(['systemd-run', '--unit='+plan['observer_unit'],
            '--setenv=SF010_RUN='+str(plan['raw_root']),
            '--property=RuntimeMaxSec=1600', 'python3', '-B',
            ROOT/'scripts/sf010_observer.py', '--manifest', args.manifest,
            '--manifest-sha', args.manifest_sha])
        observer_started = True
        while not (control/'observer-pid.json').is_file():
            if time.time() - began > 15:
                raise RuntimeError('Observer startup timed out')
            time.sleep(.05)
        observer_pid = read(control/'observer-pid.json')['pid']
        gate = Gate(manifest['run_id'], plan['run_token'],
                    manifest['plan_sha256'],
                    plan['source_sha256'][str(ROOT/'scripts/sf010_observer.py')],
                    began, stamp(plan['start_utc']))
        while not gate.accept(read(control/'heartbeat.json')
                if (control/'heartbeat.json').is_file() else None,
                time.time(), observer_pid, observer_alive()):
            time.sleep(.05)
        atomic(control/'fault-ready.json',
               {'at_utc': now(), 'manifest_sha256': args.manifest_sha,
                'heartbeat_sha256': sha(control/'heartbeat.json')}, True)
        event('READY')
        backend('arm');armed = True;event('SERVICE_TIMER_ARMED')
        refresh('ARMED', pending=True)
        if time.time() >= stamp(plan['start_utc']) - 5:
            raise RuntimeError('READY and Timer checks missed T0')
        service_start = stamp(action['planned_utc'])
        wait(service_start-105)
        evidence = refresh('SERVICE-PRE', pending=True)
        check()
        if time.time() >= service_start - 5:
            raise RuntimeError('Service preflight finished too late')
        authorization = {'run_id': manifest['run_id'],
            'action_id': action['action_id'],
            'manifest_sha256': args.manifest_sha,
            'permission': 'BOUND_QUALIFIED_FAULT',
            'status': 'COMPETITION_PREFLIGHT_AUTHORIZED',
            'at_utc': now(), 'faults_allowed': True,
            'actual_environment_pass': True,
            'independent_watchdog_pass': True,
            'registration_sha256': action['qualified_registration_sha256'],
            'watchdog_proof_file': action['watchdog_proof_file'],
            'watchdog_proof_sha256': action['watchdog_proof_sha256'],
            'health_sha256': sha(evidence)}
        auth_path = control/'service-authorization.json'
        atomic(auth_path, authorization, True)
        copy('w3', auth_path, runtime/'authorization.json')
        granted_service = True
        event('SERVICE_AUTHORIZED')
        wait(service_start+action['duration_seconds']+13)
        if action['action_id'] not in result['faults']:
            raise RuntimeError('Service execution terminal missing')
        granted_service = False
        backend('cancel')
        copy('w3', runtime, control/'service-worker-runtime', True, True)
        wait(stamp(plan['start_utc'])+810)
        refresh('SERVICE-RECOVERY')
        result['stages']['service_recovered'] = True
        http_start = stamp(plan['start_utc'])+960
        wait(http_start-35)
        refresh('REQDELAY-PRE')
        wait(http_start-5)
        check()
        granted_http = True
        event('HTTP_DELAY_AUTHORIZED')
        wait(http_start+60+13)
        granted_http = False
        result['stages']['http_delay'] = check_http_headers(
            Path(plan['raw_root'])/'state-machine/home-header-events.jsonl',
            plan, time.time())
        wait(stamp(plan['start_utc'])+1200)
        refresh('POST')
        result['status'] = 'SF010_FAULT_SEQUENCE_COMPLETE_PENDING_CAPTURE'
    except BaseException as exc:
        result['error'] = type(exc).__name__ + ': ' + str(exc)
        event('ABORTING', error=result['error'])
    finally:
        granted_service = granted_http = False
        if armed:
            try:
                backend('cancel')
                result['worker_cleanup_requested'] = True
            except Exception as exc:
                result['cleanup_error'] = str(exc)
                result['status'] = 'SF010_FORMAL_SEQUENCE_FAILED'
        if observer_started:
            subprocess.run(['systemctl','stop',plan['observer_unit']+'.service'],
                           capture_output=True, timeout=25)
        if result['status'] != 'SF010_FAULT_SEQUENCE_COMPLETE_PENDING_CAPTURE':
            subprocess.run(['systemctl','stop',plan['capture_controller_unit']+'.service'],
                           capture_output=True, timeout=25)
        result['completed_utc'] = now()
        atomic(control/'fault-controller-terminal.json', result, True)
        print(json.dumps({'status': result['status'], 'error': result.get('error')}), flush=True)
    if result['status'] != 'SF010_FAULT_SEQUENCE_COMPLETE_PENDING_CAPTURE':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
