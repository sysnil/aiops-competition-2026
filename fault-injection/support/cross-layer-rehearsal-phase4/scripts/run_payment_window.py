#!/usr/bin/env python3
"""Collect one anonymous no-fault Payment business window with fixed policy."""

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import time

import yaml

from run_attempt import ROOT, LOCAL, ssh_cmd, remote, wait_until, write, finish_processes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--id', required=True)
    parser.add_argument('--seed', required=True, type=int)
    parser.add_argument('--health', required=True, type=Path)
    args = parser.parse_args()
    plan = yaml.safe_load((LOCAL / 'design/plan.yaml').read_text())
    registered = [row for row in plan['payment_experiments'] if row['id'] == args.id]
    if len(registered) != 1 or registered[0]['seed'] != args.seed:
        raise ValueError('Payment window/seed was not registered')
    health = json.loads(remote('master', 'cat', args.health))
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(health['at_utc'])).total_seconds()
    if health['status'] != 'STAGE5_BASE_HEALTH_PASS' or not 0 <= age <= 180:
        raise ValueError('Fresh clean health gate missing')
    local = LOCAL / 'runs' / args.id
    if local.exists():
        raise ValueError('Payment run already exists')
    local.mkdir(parents=True)
    remote_root = ROOT / 'runs' / args.id
    remote('master', 'mkdir', '-p', remote_root)
    processes = {}
    completed = False
    try:
        processes['load'] = subprocess.Popen(ssh_cmd('master', 'python3',
            ROOT / 'scripts/run_load_pair.py', '--plan', ROOT / 'design/plan.yaml',
            '--health', args.health, '--run-id', args.id, '--seed', args.seed,
            '--output', remote_root / 'load_pair', '--allow-errors'),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for _ in range(30):
            try:
                schedule = json.loads(remote('master', 'cat', remote_root / 'load_pair/schedule.json', timeout=5))
                break
            except Exception:
                if processes['load'].poll() is not None:
                    raise RuntimeError('Load exited before schedule')
                time.sleep(1)
        else:
            raise RuntimeError('No Payment workload schedule')
        write(local / 'schedule.json', schedule)
        start = datetime.fromisoformat(schedule['scheduled_start_utc'])
        processes['capture-pipeline'] = subprocess.Popen(ssh_cmd('master', 'python3',
            ROOT / 'scripts/capture_pipeline.py', '--start', start.isoformat(),
            '--output', remote_root / 'capture-pipeline'),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        wait_until(start + timedelta(seconds=300))
        statuses = finish_processes(processes, 150)
        write(local / 'process-results.json', statuses)
        result = {'run_id': args.id, 'seed': args.seed,
                  'status': 'PAYMENT_WINDOW_COMPLETE' if all(
                      row['exit_code'] == 0 for row in statuses.values()) else 'PAYMENT_WINDOW_FAILED',
                  'fault_injected': False, 'formal_case_created': False,
                  'process_exit_codes': {name: row['exit_code'] for name, row in statuses.items()}}
        write(local / 'result.json', result)
        completed = True
        print(json.dumps(result), flush=True)
        if result['status'] != 'PAYMENT_WINDOW_COMPLETE':
            raise RuntimeError('Payment window incomplete')
    finally:
        for process in processes.values():
            if process.poll() is None:
                process.terminate()
        if not completed:
            try:
                remote('master', 'python3', ROOT / 'scripts/stop_attempt_processes.py',
                       '--id', args.id, '--output', remote_root / 'early-stop.json', timeout=30)
            except Exception as exc:
                print(json.dumps({'cleanup_warning': str(exc)}), flush=True)


if __name__ == '__main__':
    main()
