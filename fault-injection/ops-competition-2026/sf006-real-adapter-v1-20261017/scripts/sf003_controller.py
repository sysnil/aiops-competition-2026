#!/usr/bin/env python3
"""Dedicated SF-006 Frontend PodMemoryStress60 controller."""

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
from sf003_preflight import check_five_host_heartbeat


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
        raise RuntimeError('Master-only controller')
    if any(sha(path) != expected for path, expected in plan['source_sha256'].items()):
        raise RuntimeError('Formal source version changed')
    control = Path(plan['control_root'])
    atomic(control / 'fault-controller-started.json',
           {'run_id': manifest['run_id'], 'at_utc': now(),
            'manifest_sha256': args.manifest_sha}, True)
    action = manifest['actions'][0]
    runtime = Path(action['runtime'])
    result = {'status': 'SF006_FORMAL_SEQUENCE_FAILED', 'faults': {},
              'public_created': False, 'private_gt_created': False,
              'actual_onsets': {}, 'stages': {}}
    armed = observer_started = granted_pod = False
    observer_pid = gate = None
    last_clock = 0
    clock_proof = None

    def event(phase, **details):
        with (control / 'fault-workflow.jsonl').open('a') as stream:
            stream.write(json.dumps({'at_utc': now(), 'phase': phase,
                                     **details}) + '\n')

    def backend(verb):
        return remote('w2', ['python3', '-B', ROOT / 'scripts/fault_worker.py',
            verb, '--manifest', args.manifest, '--manifest-sha', args.manifest_sha,
            '--action-id', action['action_id']])

    def refresh(tag, pending=False):
        call = ['python3', '-B', ROOT / 'scripts/sf003_preflight.py',
                '--manifest', args.manifest, '--manifest-sha', args.manifest_sha,
                '--tag', tag]
        if pending:
            call += ['--require-pending', action['action_id']]
        event('PREFLIGHT_BEGIN', tag=tag)
        command(call)
        path = control / ('preflight-' + tag + '.json')
        if read(path)['status'] != 'SF006_FORMAL_PREFLIGHT_PASS':
            raise RuntimeError('Private preflight did not pass')
        event('PREFLIGHT_PASS', tag=tag)
        return path

    def observer_alive():
        actual = command(['systemctl', 'show', plan['observer_unit']+'.service',
                          '-p', 'MainPID', '--value']).strip()
        return int(actual or '0') == observer_pid

    def check():
        nonlocal last_clock, clock_proof
        heartbeat = read(control / 'heartbeat.json') if (control/'heartbeat.json').is_file() else None
        if not gate.accept(heartbeat, time.time(), observer_pid, observer_alive()):
            raise RuntimeError('Observer READY/heartbeat not established')
        check_five_host_heartbeat(read(plan['five_host_heartbeat_path']),
                                  manifest['plan_sha256'], time.time(),
                                  plan['five_host_monitor_sha256'])
        if time.monotonic() - last_clock >= 15:
            clock_proof = clock('w2', 3)
            last_clock = time.monotonic()
            if (abs(clock_proof['worker_minus_master_seconds']) +
                    clock_proof['uncertainty_seconds'] > .5):
                raise RuntimeError('Master/Worker clock uncertainty too large')
        if armed:
            actual = optional('w2', runtime/'actual-start.json')
            terminal = optional('w2', runtime/'terminal.json')
            planned = stamp(action['planned_utc'])
            if actual and not result['actual_onsets']:
                feedback = (time.time() - stamp(actual['actual_worker_utc']) +
                            clock_proof['worker_minus_master_seconds'] +
                            clock_proof['uncertainty_seconds'])
                actual['feedback_upper_seconds'] = feedback
                actual['seen_master_utc'] = now()
                atomic(control/'pod-onset.json', actual, True)
                result['actual_onsets'][action['action_id']] = actual
                if not actual['pass'] or feedback > 2:
                    raise RuntimeError('Pod onset or feedback exceeded two seconds')
            if time.time() > planned + 2.5 and not actual and not terminal:
                raise RuntimeError('Worker did not report Pod onset immediately')
            if terminal and not result['faults']:
                injection = terminal.get('injection', {})
                if (terminal['status'] != 'FORMAL_FAULT_COMPLETE' or
                        abs(injection.get('duration_seconds', 0) - 60) > 2 or
                        injection.get('cleanup', {}).get('status') !=
                        'COMPETITION_CLEANUP_ACTIONS_VERIFIED'):
                    raise RuntimeError('Pod fault or owned-holder cleanup failed')
                result['faults'][action['action_id']] = injection
            if granted_pod and not terminal:
                issued = time.time()
                lease = {'run_id': manifest['run_id'],
                         'action_id': action['action_id'],
                         'manifest_sha256': args.manifest_sha,
                         'permission': 'BOUND_QUALIFIED_FAULT',
                         'authorization_sha256': sha(control/'pod-authorization.json'),
                         'issued_utc': now(),
                         'expires_utc': datetime.fromtimestamp(issued+4,timezone.utc).isoformat(),
                         'clock': clock_proof, 'safety_pass': True,
                         'heartbeat_sequence': heartbeat['sequence'],
                         'heartbeat_sha256': digest(heartbeat)}
                put('w2', runtime/'lease.json', lease, True)

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
        remote('w2', ['mkdir', '-p', ROOT/'scripts', args.manifest.parent])
        for name in ('run_registry.py','handshake.py','fault_worker.py'):
            copy('w2', ROOT/'scripts'/name, ROOT/'scripts'/name)
        for path in (manifest['plan_path'], manifest['schedule_lock_path'],
                     args.manifest, action['qualified_registration']):
            copy('w2', path, path)
        began = time.time()
        command(['systemd-run', '--unit='+plan['observer_unit'],
            '--setenv=SF006_RUN='+str(plan['raw_root']),
            '--property=RuntimeMaxSec=1600', 'python3', '-B',
            ROOT/'scripts/sf003_observer.py', '--manifest', args.manifest,
            '--manifest-sha', args.manifest_sha])
        observer_started = True
        while not (control/'observer-pid.json').is_file():
            if time.time() - began > 15:
                raise RuntimeError('Observer startup timed out')
            time.sleep(.05)
        observer_pid = read(control/'observer-pid.json')['pid']
        gate = Gate(manifest['run_id'], plan['run_token'],
                    manifest['plan_sha256'],
                    plan['source_sha256'][str(ROOT/'scripts/sf003_observer.py')],
                    began, stamp(plan['start_utc']))
        while not gate.accept(read(control/'heartbeat.json')
                if (control/'heartbeat.json').is_file() else None,
                time.time(), observer_pid, observer_alive()):
            time.sleep(.05)
        atomic(control/'fault-ready.json',
               {'at_utc': now(), 'manifest_sha256': args.manifest_sha,
                'heartbeat_sha256': sha(control/'heartbeat.json')}, True)
        event('READY')
        backend('arm');armed = True;event('POD_TIMER_ARMED')
        refresh('ARMED', pending=True)
        if time.time() >= stamp(plan['start_utc']) - 5:
            raise RuntimeError('READY and Timer checks missed T0')
        pod_start = stamp(action['planned_utc'])
        wait(pod_start-105)
        evidence = refresh('POD-PRE', pending=True)
        check()
        if time.time() >= pod_start - 5:
            raise RuntimeError('Pod preflight finished too late')
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
        auth_path = control/'pod-authorization.json'
        atomic(auth_path, authorization, True)
        copy('w2', auth_path, runtime/'authorization.json')
        granted_pod = True
        event('POD_AUTHORIZED')
        wait(pod_start+action['duration_seconds']+13)
        if action['action_id'] not in result['faults']:
            raise RuntimeError('Pod execution terminal missing')
        granted_pod = False
        backend('cancel')
        copy('w2', runtime, control/'pod-worker-runtime', True, True)
        wait(stamp(plan['start_utc'])+840)
        refresh('POD-RECOVERY')
        result['stages']['pod_recovered'] = True
        wait(stamp(plan['start_utc'])+900)
        refresh('POST')
        result['status'] = 'SF006_FAULT_SEQUENCE_COMPLETE_PENDING_CAPTURE'
    except BaseException as exc:
        result['error'] = type(exc).__name__ + ': ' + str(exc)
        event('ABORTING', error=result['error'])
    finally:
        granted_pod = False
        if armed:
            try:
                backend('cancel')
                result['worker_cleanup_requested'] = True
            except Exception as exc:
                result['cleanup_error'] = str(exc)
                result['status'] = 'SF006_FORMAL_SEQUENCE_FAILED'
        if observer_started:
            subprocess.run(['systemctl','stop',plan['observer_unit']+'.service'],
                           capture_output=True, timeout=25)
        if result['status'] != 'SF006_FAULT_SEQUENCE_COMPLETE_PENDING_CAPTURE':
            subprocess.run(['systemctl','stop',plan['capture_controller_unit']+'.service'],
                           capture_output=True, timeout=25)
        result['completed_utc'] = now()
        atomic(control/'fault-controller-terminal.json', result, True)
        print(json.dumps({'status': result['status'], 'error': result.get('error')}), flush=True)
    if result['status'] != 'SF006_FAULT_SEQUENCE_COMPLETE_PENDING_CAPTURE':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
