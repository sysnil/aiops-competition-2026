#!/usr/bin/env python3
"""Verify the full no-fault workload, route, capture, and probe lifecycle."""

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import time

import yaml

from run_attempt import ROOT, LOCAL, ssh_cmd, remote, scp_to, scp_from, wait_until, write, finish_processes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--id', required=True)
    parser.add_argument('--seed', type=int, required=True)
    parser.add_argument('--health', type=Path, required=True)
    args = parser.parse_args()
    if not args.id.startswith('P4-CLEAN-'):
        raise ValueError('Only an independent clean rehearsal ID is allowed')
    plan = yaml.safe_load((LOCAL / 'design/plan.yaml').read_text())
    health = json.loads(remote('master', 'cat', args.health))
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(health['at_utc'])).total_seconds()
    if (health['status'] != 'STAGE5_BASE_HEALTH_PASS' or not 0 <= age <= 180 or
            plan['workload']['duration_seconds'] != 300):
        raise ValueError('No fresh clean health gate or changed workload duration')
    local = LOCAL / 'runs' / args.id
    if local.exists():
        raise ValueError('Run already exists')
    local.mkdir(parents=True)
    remote_root = ROOT / 'runs' / args.id
    remote('master', 'mkdir', '-p', remote_root)
    remote('k8s-worker3', 'mkdir', '-p', ROOT / 'scripts', remote_root)
    scp_to('k8s-worker3', LOCAL / 'scripts/probe_network.py', ROOT / 'scripts/probe_network.py')
    processes = {}
    receiver = False
    completed = False
    unit = 'p4-clean-iperf-' + args.id.lower()
    try:
        scp_to('k8s-worker3', LOCAL / 'scripts/stop_attempt_processes.py',
               ROOT / 'scripts/stop_attempt_processes.py')
        if ':5201' in remote('k8s-worker2', 'ss', '-ltn', 'sport', '=', ':5201'):
            raise RuntimeError('Probe receiver port is occupied')
        remote('k8s-worker2', 'systemd-run', '--quiet', '--unit=' + unit,
               '/usr/bin/timeout', '370', '/usr/bin/iperf3', '-s', '-B',
               '192.168.53.54', '-p', '5201')
        receiver = True
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
                    raise RuntimeError('Workload exited before schedule')
                time.sleep(1)
        else:
            raise RuntimeError('Workload schedule absent')
        write(local / 'schedule.json', schedule)
        start = datetime.fromisoformat(schedule['scheduled_start_utc'])
        processes['capture-pipeline'] = subprocess.Popen(ssh_cmd('master', 'python3',
            ROOT / 'scripts/capture_pipeline.py', '--start', start.isoformat(),
            '--output', remote_root / 'capture-pipeline'),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for phase, offset, count in (('before', 1, 24), ('during', 121, 12),
                                     ('after', 181, 24)):
            processes['probe-' + phase] = subprocess.Popen(ssh_cmd('k8s-worker3',
                'python3', ROOT / 'scripts/probe_network.py', '--path',
                'NETWORK_PATH/k8s-worker3/k8s-worker2', '--source-ip', '192.168.53.55',
                '--destination-ip', '192.168.53.54', '--start-utc',
                (start + timedelta(seconds=offset)).isoformat(), '--samples', count,
                '--interval', 5, '--output', remote_root / ('probe-' + phase)),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        wait_until(start + timedelta(seconds=10))
        subprocess.run(['python3', str(LOCAL / 'scripts/verify_business_routes.py'),
            '--mode', 'W3W2', '--output', str(local / 'route-proof.json')],
            check=True, timeout=90)
        scp_to('master', local / 'route-proof.json', remote_root / 'route-proof.json')
        wait_until(start + timedelta(seconds=300))
        status = finish_processes(processes, 150)
        write(local / 'process-results.json', status)
        for phase in ('before', 'during', 'after'):
            scp_from('k8s-worker3', remote_root / ('probe-' + phase),
                     local / ('probe-' + phase), recursive=True)
        success = all(row['exit_code'] == 0 for row in status.values())
        result = {'id': args.id, 'seed': args.seed,
                  'status': 'CLEAN_LIFECYCLE_PASS' if success else 'CLEAN_LIFECYCLE_FAILED',
                  'fault_injected': False, 'formal_case_created': False,
                  'workload_plan_sha256': schedule['plan_sha256'],
                  'process_exit_codes': {name: row['exit_code'] for name, row in status.items()}}
        write(local / 'result.json', result)
        completed = True
        print(json.dumps(result), flush=True)
        if not success:
            raise RuntimeError('Clean lifecycle failed')
    finally:
        for process in processes.values():
            if process.poll() is None:
                process.terminate()
        if receiver:
            subprocess.run(ssh_cmd('k8s-worker2', 'systemctl', 'stop', unit + '.service'),
                           capture_output=True, timeout=15)
            subprocess.run(ssh_cmd('k8s-worker2', 'systemctl', 'reset-failed', unit + '.service'),
                           capture_output=True, timeout=15)
        if not completed:
            for host in ('master', 'k8s-worker3'):
                try:
                    remote(host, 'python3', ROOT / 'scripts/stop_attempt_processes.py',
                           '--id', args.id, '--output', remote_root /
                           ('early-stop-' + host + '.json'), timeout=30)
                except Exception as exc:
                    print(json.dumps({'cleanup_warning': host + ': ' + str(exc)}), flush=True)


if __name__ == '__main__':
    main()
