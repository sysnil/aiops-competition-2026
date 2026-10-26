#!/usr/bin/env python3
"""Verify a full no-fault 10+5+10 qualification window."""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time

import yaml

from host_io import ROOT, LOCAL, ssh_cmd, remote, scp_to, scp_from, wait_until, write, finish_processes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--id', required=True)
    parser.add_argument('--seed', type=int, required=True)
    parser.add_argument('--health', type=Path, required=True)
    args = parser.parse_args()
    registered = {'S5V3AP-CLEAN-C3-S1': 851221,
                  'S5V3AP-CLEAN-C3-S2': 851222}
    if registered.get(args.id) != args.seed:
        raise ValueError('Clean C3 attempt ID and seed must match registration')
    remote('master', 'python3', ROOT / 'scripts/freeze_clean_candidate.py', '--verify')
    candidate = json.loads(remote('master', 'cat',
        ROOT / 'design/clean-c3-candidate-manifest.json'))
    for source, expected in candidate['sha256'].items():
        if source.startswith(str(ROOT) + '/'):
            relative = Path(source).relative_to(ROOT)
            if relative.parts[0] in ('design', 'scripts'):
                local_source = LOCAL / relative
                if not local_source.is_file() or hashlib.sha256(
                        local_source.read_bytes()).hexdigest() != expected:
                    raise ValueError('Local clean C3 source differs from pinned Master')
    pinned_proxy = json.loads(remote('master', 'cat', ROOT /
        'runtime/proxy-c2b-worker2-pin/deployment-after-pin.json'))
    live_proxy = json.loads(remote('master', 'kubectl', '-n', 'otel-demo',
                                  'get', 'deployment', 'frontend-proxy', '-o', 'json'))
    if pinned_proxy['spec']['template'] != live_proxy['spec']['template']:
        raise ValueError('Frontend-proxy config changed before C3 clean window')
    plan = yaml.safe_load((LOCAL / 'design/load-plan-c3.yaml').read_text())
    health = json.loads(remote('master', 'cat', args.health))
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(health['at_utc'])).total_seconds()
    if (health['status'] != 'STAGE5_BASE_HEALTH_PASS' or not 0 <= age <= 180 or
            plan['workload']['duration_seconds'] != 1500):
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
    unit = 's5v3ap-clean-iperf-' + args.id.lower()
    try:
        scp_to('k8s-worker3', LOCAL / 'scripts/stop_attempt_processes.py',
               ROOT / 'scripts/stop_attempt_processes.py')
        if ':5201' in remote('k8s-worker2', 'ss', '-ltn', 'sport', '=', ':5201'):
            raise RuntimeError('Probe receiver port is occupied')
        remote('k8s-worker2', 'systemd-run', '--quiet', '--unit=' + unit,
               '/usr/bin/timeout', '1700', '/usr/bin/iperf3', '-s', '-B',
               '192.168.53.54', '-p', '5201')
        receiver = True
        processes['load'] = subprocess.Popen(ssh_cmd('master', 'python3',
            ROOT / 'scripts/run_load_pair_c3.py', '--plan', ROOT / 'design/load-plan-c3.yaml',
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
            ROOT / 'scripts/capture_pipeline_c2.py', '--start', start.isoformat(),
            '--output', remote_root / 'capture-pipeline'),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for phase, offset, count in (('before', 1, 120), ('during', 601, 60),
                                     ('after', 901, 120)):
            processes['probe-' + phase] = subprocess.Popen(ssh_cmd('k8s-worker3',
                'python3', ROOT / 'scripts/probe_network.py', '--path',
                'NETWORK_PATH/k8s-worker3/k8s-worker2', '--source-ip', '192.168.53.55',
                '--destination-ip', '192.168.53.54', '--start-utc',
                (start + timedelta(seconds=offset)).isoformat(), '--samples', count,
                '--interval', 5, '--output', remote_root / ('probe-' + phase)),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        wait_until(start + timedelta(seconds=10))
        subprocess.run(['python3', str(LOCAL / 'scripts/verify_business_routes.py'),
            '--mode', 'qualification_clean', '--output', str(local / 'route-proof.json')],
            check=True, timeout=90)
        scp_to('master', local / 'route-proof.json', remote_root / 'route-proof.json')
        wait_until(start + timedelta(seconds=1500))
        status = finish_processes(processes, 240)
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
