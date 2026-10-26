#!/usr/bin/env python3
"""Run one registered bounded Phase 4 network rehearsal."""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time

import yaml


ROOT = Path('/root/cross-layer-rehearsal-phase4')
LOCAL = Path('cross-layer-rehearsal-phase4')
HOSTS = {'master': ('192.168.53.52', '/tmp/stage4f-formal-ssh'),
         'k8s-worker1': ('192.168.53.53', '/tmp/p4-worker1-ssh'),
         'k8s-worker2': ('192.168.53.54', '/tmp/p4-worker2-ssh'),
         'k8s-worker3': ('192.168.53.55', '/tmp/p4-worker3-ssh'),
         'router': ('192.168.53.50', '/tmp/p4-router-ssh')}


def ssh_cmd(host, *args):
    ip, socket = HOSTS[host]
    return ['ssh', '-o', 'BatchMode=yes', '-o', 'ControlPath=' + socket,
            'root@' + ip, *map(str, args)]


def remote(host, *args, timeout=90):
    result = subprocess.run(ssh_cmd(host, *args), capture_output=True, text=True,
                            timeout=timeout)
    if result.returncode:
        raise RuntimeError(host + ' command failed: ' + result.stderr[-500:])
    return result.stdout


def scp_to(host, local, remote_path):
    ip, socket = HOSTS[host]
    subprocess.run(['scp', '-q', '-o', 'ControlPath=' + socket, str(local),
                    'root@' + ip + ':' + str(remote_path)], check=True, timeout=60)


def scp_from(host, remote_path, local, recursive=False):
    ip, socket = HOSTS[host]
    command = ['scp', '-q', '-o', 'ControlPath=' + socket]
    if recursive:
        command.append('-r')
    subprocess.run([*command, 'root@' + ip + ':' + str(remote_path), str(local)],
                   check=True, timeout=90)


def now():
    return datetime.now(timezone.utc)


def wait_until(value):
    delay = value.timestamp() - time.time()
    if delay > 0:
        time.sleep(delay)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ValueError('Refusing overwrite: ' + str(path))
    path.write_text(json.dumps(value, indent=2) + '\n')


def fresh_health(attempt):
    local_residue = LOCAL / 'runtime' / (attempt + '-preflight-residue.json')
    command = ['python3', str(LOCAL / 'scripts/audit_hosts.py')]
    for name, (ip, socket) in HOSTS.items():
        audit_name = 'k8s-master' if name == 'master' else name
        command.extend(['--host', ','.join((audit_name, ip, socket))])
    subprocess.run([*command, '--output', str(local_residue)], check=True, timeout=90)
    remote_dir = ROOT / 'attempts' / attempt
    remote('master', 'mkdir', '-p', str(remote_dir))
    scp_to('master', local_residue, remote_dir / 'preflight-residue.json')
    health = remote_dir / 'preflight-health.json'
    remote('master', 'python3', '/root/stage5-v2-cross-layer-rca/scripts/health_gate.py',
           '--root', '/root/stage5-v2-cross-layer-rca',
           '--stage4f-root', '/root/stage4f-cross-layer-validation',
           '--residue', str(remote_dir / 'preflight-residue.json'),
           '--output', str(health))
    value = json.loads(remote('master', 'cat', str(health)))
    if value['status'] != 'STAGE5_BASE_HEALTH_PASS':
        raise ValueError('Fresh five-host health gate failed: ' + str(value))
    return health


def start_capture(attempt, schedule):
    start = datetime.fromisoformat(schedule['scheduled_start_utc'])
    command = ssh_cmd('master', 'python3', ROOT / 'scripts/capture_pipeline.py',
                      '--start', start.isoformat(), '--output',
                      ROOT / 'attempts' / attempt / 'capture-pipeline')
    return {'capture-pipeline': subprocess.Popen(command, stdout=subprocess.PIPE,
                                                stderr=subprocess.PIPE, text=True)}


def finish_processes(processes, timeout):
    result = {}
    for name, process in processes.items():
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            result[name] = {'exit_code': process.returncode, 'stdout': stdout[-1000:],
                            'stderr': stderr[-2000:]}
        except subprocess.TimeoutExpired:
            process.terminate()
            stdout, stderr = process.communicate(timeout=15)
            result[name] = {'exit_code': 'TIMEOUT', 'stdout': stdout[-1000:],
                            'stderr': stderr[-2000:]}
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--id', required=True)
    args = parser.parse_args()
    plan = yaml.safe_load((LOCAL / 'design/plan.yaml').read_text())
    entries = [row for row in plan['experiments'] if row['id'] == args.id]
    if len(entries) != 1:
        raise ValueError('Attempt not pre-registered')
    registered = json.loads((LOCAL / 'design/registered-rehearsal-manifest.json').read_text())
    for source, digest in registered['sha256'].items():
        if source.startswith(str(ROOT)):
            local_source = Path(source.replace(str(ROOT), str(LOCAL), 1))
            actual = hashlib.sha256(local_source.read_bytes()).hexdigest()
        else:
            actual = remote('master', 'sha256sum', source).split()[0]
        if actual != digest:
            raise ValueError('Registered source version changed: ' + source)
    spec = entries[0]
    attempt = args.id
    local_attempt = LOCAL / 'attempts' / attempt
    if local_attempt.exists():
        raise ValueError('Original attempt already exists')
    local_attempt.mkdir(parents=True)
    remote_attempt = ROOT / 'attempts' / attempt
    if not attempt.startswith('P4-W3W2'):
        raise ValueError('Only the registered W3-W2 network mechanism is executable')
    source = 'k8s-worker3'
    destination = 'k8s-worker2'
    adapter = 'network_fault.py'
    processes = {}
    receiver = False
    completed = False
    state = ROOT / 'runtime' / (attempt + '-' + source + '.json')
    errors = []
    try:
        health = fresh_health(attempt)
        remote(source, 'mkdir', '-p', str(ROOT / 'scripts'), str(ROOT / 'design'),
               str(ROOT / 'attempts' / attempt), str(ROOT / 'runtime'))
        scp_to(source, LOCAL / 'scripts/stop_attempt_processes.py',
               ROOT / 'scripts/stop_attempt_processes.py')
        load = ROOT / 'attempts' / attempt / 'load_pair'
        processes['load'] = subprocess.Popen(ssh_cmd('master', 'python3',
            ROOT / 'scripts/run_load_pair.py', '--plan', ROOT / 'design/plan.yaml',
            '--health', health, '--run-id', attempt, '--seed', spec['seed'],
            '--output', load, '--allow-errors'), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        for _ in range(30):
            try:
                schedule = json.loads(remote('master', 'cat', load / 'schedule.json', timeout=5))
                break
            except Exception:
                if processes['load'].poll() is not None:
                    raise RuntimeError('Workload exited before schedule')
                time.sleep(1)
        else:
            raise RuntimeError('No workload schedule')
        write(local_attempt / 'schedule.json', schedule)
        start = datetime.fromisoformat(schedule['scheduled_start_utc'])
        processes.update(start_capture(attempt, schedule))
        remote(destination, 'mkdir', '-p', str(ROOT / 'attempts' / attempt))
        scp_to(source, LOCAL / 'scripts/probe_network.py', ROOT / 'scripts/probe_network.py')
        unit = 'p4-iperf-' + attempt.lower()
        remote(destination, 'systemd-run', '--quiet', '--unit=' + unit,
               '/usr/bin/timeout', '370', '/usr/bin/iperf3', '-s', '-B',
               '192.168.53.54', '-p', '5201')
        receiver = True
        for phase, offset, count in (('before', 1, 24), ('during', 121, 12),
                                     ('after', 181, 24)):
            processes['probe-' + phase] = subprocess.Popen(ssh_cmd(source,
                'python3', ROOT / 'scripts/probe_network.py', '--path',
                'NETWORK_PATH/k8s-worker3/k8s-worker2', '--source-ip',
                '192.168.53.55', '--destination-ip', '192.168.53.54',
                '--start-utc', (start + timedelta(seconds=offset)).isoformat(),
                '--samples', count, '--interval', 5, '--output',
                ROOT / 'attempts' / attempt / ('probe-' + phase)),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        wait_until(start + timedelta(seconds=10))
        proof_path = remote_attempt / 'route-proof.json'
        local_proof = local_attempt / 'route-proof.json'
        subprocess.run(['python3', str(LOCAL / 'scripts/verify_business_routes.py'),
            '--mode', 'W3W2', '--output', str(local_proof)], check=True, timeout=90)
        scp_to('master', local_proof, proof_path)
        preflight = remote_attempt / 'preflight.json'
        cmd = ['python3', ROOT / 'scripts/build_preflight.py', '--plan',
               ROOT / 'design/plan.yaml', '--adapters', ROOT / 'design/fault-adapters.yaml',
               '--health', health, '--id', attempt, '--output', preflight,
               '--route-proof', proof_path,
               '--readiness', ROOT / 'observations/no-fault-readiness.json']
        remote('master', *cmd)
        scp_from('master', preflight, local_attempt / 'preflight.json')
        for filename in (adapter,):
            scp_to(source, LOCAL / 'scripts' / filename, ROOT / 'scripts' / filename)
        scp_to(source, LOCAL / 'design/fault-adapters.yaml',
               ROOT / 'design/fault-adapters.yaml')
        scp_to(source, local_attempt / 'preflight.json', preflight)
        remote(source, 'python3', ROOT / 'scripts' / adapter, 'precheck',
               '--plan', ROOT / 'design/fault-adapters.yaml', '--preflight', preflight,
               '--experiment-id', attempt)
        if ':5201' not in remote(destination, 'ss', '-ltn', 'sport', '=', ':5201'):
            raise RuntimeError('Network probe receiver not listening')
        if now() > start + timedelta(seconds=100):
            raise RuntimeError('Preflight finished too close to fault start')
        processes['fault'] = subprocess.Popen(ssh_cmd(source, 'python3',
            ROOT / 'scripts' / adapter, 'run', '--plan',
            ROOT / 'design/fault-adapters.yaml', '--preflight', preflight,
            '--experiment-id', attempt, '--start-utc',
            (start + timedelta(seconds=120)).isoformat(), '--state-file', state),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        fault_result = finish_processes({'fault': processes.pop('fault')}, 200)
        write(local_attempt / 'fault-process.json', fault_result)
        if fault_result['fault']['exit_code'] != 0:
            errors.append('fault_execution_failed')
        wait_until(start + timedelta(seconds=300))
        process_results = finish_processes(processes, 150)
        write(local_attempt / 'process-results.json', process_results)
        errors.extend(name + '_failed' for name, row in process_results.items()
                      if row['exit_code'] != 0)
        state_data = json.loads(remote(source, 'cat', state))
        write(local_attempt / 'fault-state.json', state_data)
        if state_data['status'] != 'CLEANED':
            errors.append('fault_not_cleaned')
        for phase in ('before', 'during', 'after'):
            scp_from(source, ROOT / 'attempts' / attempt / ('probe-' + phase),
                     local_attempt / ('probe-' + phase), recursive=True)
        result = {'experiment_id': attempt, 'seed': spec['seed'],
                  'fault_start_utc': (start + timedelta(seconds=120)).isoformat(),
                  'fault_end_utc': (start + timedelta(seconds=180)).isoformat(),
                  'status': 'REHEARSAL_COMPLETE' if not errors else 'REHEARSAL_INCOMPLETE',
                  'errors': errors, 'formal_case_created': False}
        write(local_attempt / 'result.json', result)
        completed = True
        print(json.dumps(result), flush=True)
    finally:
        if processes:
            for process in processes.values():
                if process.poll() is None:
                    process.terminate()
        try:
            state_data = json.loads(remote(source, 'cat', state, timeout=10))
            if state_data.get('status') != 'CLEANED':
                remote(source, 'python3', ROOT / 'scripts' / adapter, 'cleanup',
                       '--state-file', state, timeout=30)
        except Exception as exc:
            print(json.dumps({'cleanup_warning': str(exc)}), flush=True)
        if receiver:
            subprocess.run(ssh_cmd(destination, 'systemctl', 'stop', unit + '.service'),
                           capture_output=True, timeout=15)
            subprocess.run(ssh_cmd(destination, 'systemctl', 'reset-failed', unit + '.service'),
                           capture_output=True, timeout=15)
        if not completed:
            for host in ('master', source):
                try:
                    remote(host, 'python3', ROOT / 'scripts/stop_attempt_processes.py',
                           '--id', attempt, '--output', ROOT / 'attempts' / attempt /
                           ('early-stop-' + host + '.json'), timeout=30)
                except Exception as exc:
                    print(json.dumps({'early_stop_warning': host + ': ' + str(exc)}), flush=True)


if __name__ == '__main__':
    main()
