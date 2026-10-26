#!/usr/bin/env python3
"""One bounded no-fault business stream to verify current route flow evidence."""

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess

import yaml

from clean_protocol import DATA_ROOT, assert_disk, save_new, sha
from run_clean import (BASE_URL, CONTROLS, MACHINE, PEOPLE, PROFILES, STATE,
                       current_snapshot, validate_workload)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--prereg', type=Path, required=True)
    parser.add_argument('--health', type=Path, required=True)
    parser.add_argument('--residue', type=Path, required=True)
    args = parser.parse_args()
    plan = yaml.safe_load(args.prereg.read_text())
    if (plan['status'] != 'PREREGISTERED_SHORT_NO_FAULT_ROUTE_PREFLIGHT' or
            plan['id'] != 'S5V3FFR-ROUTE-PREFLIGHT-20261013' or
            plan['seed'] != 864090 or plan['duration_seconds'] != 180 or
            plan['fault_injected'] or plan['formal_case'] or
            plan['data_root'] != str(DATA_ROOT)):
        raise RuntimeError('Short route preflight registration invalid')
    validate_workload(plan)
    health, residue = json.loads(args.health.read_text()), json.loads(args.residue.read_text())
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(
        health['captured_at_utc'])).total_seconds()
    if health['status'] != 'GLOBAL_SAFETY_PASS' or residue['status'] != \
            'HOST_RESIDUE_PASS' or not 0 <= age <= 180:
        raise RuntimeError('Fresh Global Safety and residue required')
    snapshot = current_snapshot('short-preflight')
    if snapshot['collector_config_sha256'] != \
            'b14e2ce22e701e627d792bafc559036e59ec0f054fe020ddb229fbfb9f197f97':
        raise RuntimeError('Collector original configuration not restored')
    run = DATA_ROOT / 'runs' / plan['id']
    if run.exists(): raise RuntimeError('Preflight run already exists')
    assert_disk(run, plan['planned_raw_bytes'])
    run.mkdir(parents=True)
    start = datetime.now(timezone.utc) + timedelta(seconds=35)
    schedule = {'id': plan['id'], 'seed': plan['seed'],
        'scheduled_start_utc': start.isoformat(),
        'scheduled_end_utc': (start + timedelta(seconds=180)).isoformat(),
        'pod_uids': snapshot['pod_uids'],
        'route_sha256': snapshot['live_route_sha256'],
        'source_sha256': {str(path): sha(path) for path in
            (args.prereg, args.health, args.residue, Path(__file__), STATE,
             CONTROLS, MACHINE, PROFILES, PEOPLE)},
        'fault_injected': False, 'formal_case': False}
    save_new(run / 'schedule.json', schedule)
    commands = {
        'state-machine': ['/usr/bin/python3', '-B', str(STATE), '--machine', str(MACHINE),
            '--profiles', str(PROFILES), '--people', str(PEOPLE), '--profile', 'MEDIUM',
            '--seed', str(plan['seed']), '--duration', '180', '--start-utc',
            start.isoformat(), '--base-url', BASE_URL, '--output', str(run / 'state-machine')],
        'controls': ['/usr/bin/python3', '-B', str(CONTROLS), '--plan', str(args.prereg),
            '--seed', str(plan['seed']), '--duration', '180', '--start-utc',
            start.isoformat(), '--base-url', BASE_URL, '--output', str(run / 'controls')]}
    processes, handles = {}, []
    result = {'status': 'SHORT_ROUTE_PREFLIGHT_FAILED', 'run_id': plan['id'],
              'fault_injected': False}
    try:
        for name, command in commands.items():
            out, err = (run / (name + '.stdout')).open('x'), (run / (name + '.stderr')).open('x')
            handles.extend((out, err))
            processes[name] = subprocess.Popen(command, stdout=out, stderr=err)
        codes = {name: child.wait(timeout=260) for name, child in processes.items()}
        result['process_exit_codes'] = codes
        manifests = {name: json.loads((run / name / 'result-manifest.json').read_text())
                     for name in commands}
        result['workload_manifests'] = manifests
        if any(codes.values()) or manifests['state-machine']['status'] != 'LOAD_PASS' or \
                manifests['controls']['status'] != 'CONTROL_STREAMS_COMPLETE':
            raise RuntimeError('Short business stream failed')
        after = current_snapshot('short-preflight')
        if after['pod_uids'] != snapshot['pod_uids'] or after[
                'live_route_sha256'] != snapshot['live_route_sha256']:
            raise RuntimeError('Short preflight route or Pod drift')
        result['status'] = 'SHORT_ROUTE_PREFLIGHT_LOAD_PASS'
    except Exception as error:
        result['error'] = str(error)
    finally:
        for child in processes.values():
            if child.poll() is None:
                child.terminate()
                try: child.wait(timeout=15)
                except subprocess.TimeoutExpired: child.kill()
        for handle in handles: handle.close()
        result['completed_utc'] = datetime.now(timezone.utc).isoformat()
        save_new(run / 'result.json', result)
        print(json.dumps({'status': result['status'], 'error': result.get('error')}), flush=True)
    if result['status'] != 'SHORT_ROUTE_PREFLIGHT_LOAD_PASS':
        raise SystemExit(1)


if __name__ == '__main__': main()
