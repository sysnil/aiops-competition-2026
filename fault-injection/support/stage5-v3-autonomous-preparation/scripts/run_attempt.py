"""One preregistered 10+5+10 rehearsal with independent watchdog cleanup."""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess

import yaml

from host_io import (ROOT, LOCAL, HOSTS, ssh_cmd, remote, scp_to, scp_from,
                     wait_until, write, finish_processes)


def fresh_health(attempt, stage):
    free_bytes = int(remote('master', 'python3', '-c',
                            'import shutil; print(shutil.disk_usage("/root").free)'))
    if free_bytes < 8 * 1024 ** 3:
        raise RuntimeError('Master free space is below the 8 GiB rehearsal safety floor')
    local_residue = LOCAL / 'runtime' / (attempt + '-' + stage + '-residue.json')
    write(LOCAL / 'runtime' / (attempt + '-' + stage + '-disk.json'),
          {'master_free_bytes': free_bytes,
           'minimum_required_bytes': 8 * 1024 ** 3,
           'at_utc': datetime.now(timezone.utc).isoformat()})
    command = ['python3', str(LOCAL / 'scripts/audit_hosts.py')]
    for name, (ip, socket) in HOSTS.items():
        audit_name = 'k8s-master' if name == 'master' else name
        command.extend(['--host', ','.join((audit_name, ip, socket))])
    if stage == 'prefault':
        command.extend(['--expected-probe-unit',
                        's5v3ap-iperf-' + attempt.lower() + '.service'])
    subprocess.run([*command, '--output', str(local_residue)],
                   check=True, timeout=90)
    remote_attempt = ROOT / 'attempts' / attempt
    remote('master', 'mkdir', '-p', remote_attempt)
    scp_to('master', LOCAL / 'runtime' / (attempt + '-' + stage + '-disk.json'),
           remote_attempt / (stage + '-disk.json'))
    scp_to('master', local_residue, remote_attempt / (stage + '-residue.json'))
    health_path = remote_attempt / (stage + '-health.json')
    remote('master', 'python3', '/root/stage5-v2-cross-layer-rca/scripts/health_gate.py',
           '--root', '/root/stage5-v2-cross-layer-rca',
           '--stage4f-root', '/root/stage4f-cross-layer-validation',
           '--residue', remote_attempt / (stage + '-residue.json'),
           '--output', health_path)
    health = json.loads(remote('master', 'cat', health_path))
    if health['status'] != 'STAGE5_BASE_HEALTH_PASS':
        raise ValueError('Fresh five-host health gate failed: ' + stage)
    registered_proxy = json.loads(remote('master', 'cat', ROOT /
        'runtime/proxy-c2b-worker2-pin/deployment-after-pin.json'))
    current_proxy = json.loads(remote('master', 'kubectl', '-n', 'otel-demo',
                                      'get', 'deployment', 'frontend-proxy', '-o', 'json'))
    if registered_proxy['spec']['template'] != current_proxy['spec']['template']:
        raise ValueError('Frontend-proxy transport repair changed after clean qualification')
    return health_path


def registration():
    path = ROOT / 'design/registration-manifest.json'
    remote('master', 'python3', ROOT / 'scripts/freeze_registration.py',
           '--verify', '--plan', ROOT / 'design/registration-v1.yaml',
           '--output', path)
    plan = yaml.safe_load(remote('master', 'cat', ROOT / 'design/registration-v1.yaml'))
    manifest = json.loads(remote('master', 'cat', path))
    for source, expected in manifest['source_sha256'].items():
        if source.startswith(str(ROOT) + '/'):
            relative = Path(source).relative_to(ROOT)
            if relative.parts[0] in ('scripts', 'design', 'tests'):
                local = LOCAL / relative
                if not local.is_file() or hashlib.sha256(local.read_bytes()).hexdigest() != expected:
                    raise ValueError('Local execution source differs from frozen Master: ' + source)
    return plan, manifest


def prepare_hosts(spec, preflight_path, local_attempt):
    hosts = sorted({row['node'] for row in spec.get('target_pods', [])})
    if spec['layer'] == 'NODE':
        hosts = [spec['target_entity'].split('/', 1)[1]]
    if spec['layer'] == 'NETWORK_PATH':
        hosts = ['k8s-worker3']
    if not hosts:
        raise ValueError('No registered fault host')
    script = 'network_fault.py' if spec['layer'] == 'NETWORK_PATH' else 'resource_fault.py'
    for host in hosts:
        remote(host, 'mkdir', '-p', ROOT / 'design', ROOT / 'scripts',
               ROOT / 'runtime', ROOT / 'attempts' / spec['id'])
        scp_to(host, LOCAL / 'scripts' / script, ROOT / 'scripts' / script)
        if spec['layer'] == 'POD':
            scp_to(host, LOCAL / 'scripts/resource_memory_holder.py',
                   ROOT / 'scripts/resource_memory_holder.py')
        scp_to(host, LOCAL / 'design/registration-v1.yaml',
               ROOT / 'design/registration-v1.yaml')
        if spec['layer'] != 'NETWORK_PATH':
            scp_to(host, LOCAL / 'design/registration-manifest.json',
                   ROOT / 'design/registration-manifest.json')
        scp_to(host, local_attempt / 'preflight.json',
               ROOT / 'attempts' / spec['id'] / 'preflight.json')
        if spec['layer'] == 'NETWORK_PATH':
            remote(host, 'python3', ROOT / 'scripts/network_fault.py', 'precheck',
                   '--plan', ROOT / 'design/registration-v1.yaml',
                   '--preflight', ROOT / 'attempts' / spec['id'] / 'preflight.json',
                   '--experiment-id', spec['id'])
        else:
            remote(host, 'python3', ROOT / 'scripts/resource_fault.py', 'precheck',
                   '--design-freeze', ROOT / 'design/registration-manifest.json',
                   '--preflight-freeze', ROOT / 'attempts' / spec['id'] / 'preflight.json')
    return hosts, script


def fault_command(host, script, spec, start):
    state = ROOT / 'runtime' / (spec['id'] + '-' + host + '.json')
    preflight = ROOT / 'attempts' / spec['id'] / 'preflight.json'
    if script == 'network_fault.py':
        args = [ROOT / 'scripts/network_fault.py', 'run',
                '--plan', ROOT / 'design/registration-v1.yaml',
                '--preflight', preflight, '--experiment-id', spec['id']]
    else:
        args = [ROOT / 'scripts/resource_fault.py', 'inject',
                '--design-freeze', ROOT / 'design/registration-manifest.json',
                '--preflight-freeze', preflight]
    return ssh_cmd(host, 'python3', *args, '--start-utc', start.isoformat(),
                   '--state-file', state), state


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--id', required=True)
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    plan, manifest = registration()
    rows = [row for row in plan['experiments'] if row['id'] == args.id]
    if len(rows) != 1 or args.id not in manifest['experiment_order']:
        raise ValueError('Fault attempt is not preregistered')
    spec = rows[0]
    if spec.get('precondition_status') != 'PASS':
        raise ValueError('Preregistered no-fault prerequisite is BLOCKED')
    if args.validate_only:
        print(json.dumps({'status': 'REGISTERED_ATTEMPT_SOURCE_VALIDATED',
                          'experiment_id': args.id, 'seed': spec['seed'],
                          'faults_injected': 0}))
        return
    local = LOCAL / 'attempts' / args.id
    if local.exists():
        raise ValueError('Attempt archive already exists; never overwrite a prior result')
    local.mkdir(parents=True)
    remote_attempt = ROOT / 'attempts' / args.id
    processes = {}
    receiver = False
    fault_states = {}
    completed = False
    errors = []
    receiver_unit = 's5v3ap-iperf-' + args.id.lower()
    try:
        health = fresh_health(args.id, 'initial')
        remote('k8s-worker3', 'mkdir', '-p', ROOT / 'scripts', remote_attempt)
        scp_to('k8s-worker3', LOCAL / 'scripts/probe_network.py',
               ROOT / 'scripts/probe_network.py')
        scp_to('k8s-worker3', LOCAL / 'scripts/stop_attempt_processes.py',
               ROOT / 'scripts/stop_attempt_processes.py')
        if ':5201' in remote('k8s-worker2', 'ss', '-ltn', 'sport', '=', ':5201'):
            raise RuntimeError('Probe receiver port is occupied before this attempt')
        remote('k8s-worker2', 'systemd-run', '--quiet', '--unit=' + receiver_unit,
               '/usr/bin/timeout', '1700', '/usr/bin/iperf3', '-s', '-B',
               '192.168.53.54', '-p', '5201')
        receiver = True
        processes['load'] = subprocess.Popen(ssh_cmd('master', 'python3',
            ROOT / 'scripts/run_load_pair_c3.py', '--plan', ROOT / 'design/load-plan-c3.yaml',
            '--health', health, '--run-id', args.id, '--seed', spec['seed'],
            '--output', remote_attempt / 'load_pair', '--allow-errors'),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for _ in range(30):
            try:
                schedule = json.loads(remote('master', 'cat', remote_attempt /
                                             'load_pair/schedule.json', timeout=5))
                break
            except Exception:
                if processes['load'].poll() is not None:
                    raise RuntimeError('Business load exited before schedule')
                subprocess.run(['sleep', '1'], check=True)
        else:
            raise RuntimeError('Business schedule was not created')
        write(local / 'schedule.json', schedule)
        start = datetime.fromisoformat(schedule['scheduled_start_utc'])
        processes['capture-pipeline'] = subprocess.Popen(ssh_cmd('master', 'python3',
            ROOT / 'scripts/capture_pipeline_c2.py', '--start', start.isoformat(),
            '--output', remote_attempt / 'capture-pipeline'),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for phase, offset, count in (('before', 1, 120), ('during', 601, 60),
                                     ('after', 901, 120)):
            processes['probe-' + phase] = subprocess.Popen(ssh_cmd('k8s-worker3',
                'python3', ROOT / 'scripts/probe_network.py', '--path',
                'NETWORK_PATH/k8s-worker3/k8s-worker2', '--source-ip', '192.168.53.55',
                '--destination-ip', '192.168.53.54', '--start-utc',
                (start + timedelta(seconds=offset)).isoformat(),
                '--samples', count, '--interval', 5,
                '--output', remote_attempt / ('probe-' + phase)),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        wait_until(start + timedelta(seconds=400))
        fresh = fresh_health(args.id, 'prefault')
        subprocess.run(['python3', str(LOCAL / 'scripts/verify_business_routes.py'),
                        '--mode', 'W3W2', '--output', str(local / 'route-proof.json')],
                       check=True, timeout=90)
        scp_to('master', local / 'route-proof.json', remote_attempt / 'route-proof.json')
        remote('master', 'python3', ROOT / 'scripts/build_preflight.py',
               '--plan', ROOT / 'design/registration-v1.yaml',
               '--registration-manifest', ROOT / 'design/registration-manifest.json',
               '--health', fresh,
               '--route-proof', remote_attempt / 'route-proof.json',
               '--experiment-id', args.id, '--output', remote_attempt / 'preflight.json')
        scp_from('master', remote_attempt / 'preflight.json', local / 'preflight.json')
        hosts, script = prepare_hosts(spec, remote_attempt / 'preflight.json', local)
        if datetime.now(timezone.utc) > start + timedelta(seconds=540):
            raise RuntimeError('Preflight finished too close to registered fault start')
        fault_start = start + timedelta(seconds=600)
        for host in hosts:
            if spec['layer'] == 'NETWORK_PATH':
                qdisc = remote(host, 'tc', '-j', 'qdisc', 'show', 'dev', 'ens160')
                write(local / 'network-qdisc-before.json', json.loads(qdisc))
                scp_to('master', local / 'network-qdisc-before.json',
                       remote_attempt / 'network-qdisc-before.json')
            command, fault_states[host] = fault_command(host, script, spec, fault_start)
            processes['fault-' + host] = subprocess.Popen(command,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        fault_processes = {name: processes.pop(name) for name in list(processes)
                           if name.startswith('fault-')}
        fault_results = finish_processes(fault_processes, 450)
        write(local / 'fault-processes.json', fault_results)
        errors.extend(name + '_failed' for name, row in fault_results.items()
                      if row['exit_code'] != 0)
        for host, state in fault_states.items():
            raw = json.loads(remote(host, 'cat', state, timeout=10))
            write(local / ('fault-state-' + host + '.json'), raw)
            if raw['status'] != 'CLEANED':
                errors.append('fault_cleanup_not_verified_on_' + host)
        if any(value.startswith('fault_cleanup_not_verified') for value in errors):
            raise RuntimeError('Fault cleanup failed; stop before proceeding')
        wait_until(start + timedelta(seconds=1500))
        process_results = finish_processes(processes, 240)
        processes.clear()
        write(local / 'process-results.json', process_results)
        errors.extend(name + '_failed' for name, row in process_results.items()
                      if row['exit_code'] != 0)
        for phase in ('before', 'during', 'after'):
            scp_from('k8s-worker3', remote_attempt / ('probe-' + phase),
                     local / ('probe-' + phase), recursive=True)
        remote('master', 'python3', ROOT / 'scripts/audit_public_boundary.py',
               '--run', remote_attempt,
               '--output', ROOT / 'results' / (args.id + '-public-boundary-audit.json'),
               timeout=180)
        subprocess.run(ssh_cmd('k8s-worker2', 'systemctl', 'stop',
                               receiver_unit + '.service'),
                       check=True, capture_output=True, timeout=15)
        subprocess.run(ssh_cmd('k8s-worker2', 'systemctl', 'reset-failed',
                               receiver_unit + '.service'),
                       capture_output=True, timeout=15)
        receiver = False
        post_health = fresh_health(args.id, 'post')
        result = {'experiment_id': args.id, 'seed': spec['seed'],
                  'status': 'REHEARSAL_CAPTURED' if not errors else 'REHEARSAL_INCOMPLETE',
                  'fault_start_utc': fault_start.isoformat(),
                  'fault_end_utc': (fault_start + timedelta(seconds=300)).isoformat(),
                  'fault_hosts': hosts, 'errors': errors,
                  'post_health_path': str(post_health),
                  'formal_case_created': False, 'algorithm_predictions_run': False}
        write(local / 'result.json', result)
        subprocess.run(['rsync', '-a', '-e',
                        'ssh -o BatchMode=yes -o ControlMaster=no -S /tmp/stage4f-formal-ssh',
                        str(local) + '/', 'root@192.168.53.52:' + str(remote_attempt) + '/'],
                       check=True, timeout=90)
        completed = True
        print(json.dumps(result), flush=True)
    except Exception as exc:
        write(local / 'failure.json', {'experiment_id': args.id,
              'failed_at_utc': datetime.now(timezone.utc).isoformat(),
              'error_type': type(exc).__name__, 'reason': str(exc),
              'formal_case_created': False})
        try:
            scp_to('master', local / 'failure.json', remote_attempt / 'failure.json')
        except Exception:
            pass
        raise
    finally:
        for process in processes.values():
            if process.poll() is None:
                process.terminate()
        for host, state in fault_states.items():
            try:
                raw = json.loads(remote(host, 'cat', state, timeout=10))
                if raw['status'] != 'CLEANED':
                    remote(host, 'python3', ROOT / 'scripts' / script, 'cleanup',
                           '--state-file', state, timeout=45)
            except Exception as exc:
                print(json.dumps({'cleanup_warning': host + ': ' + str(exc)}), flush=True)
        if receiver:
            subprocess.run(ssh_cmd('k8s-worker2', 'systemctl', 'stop',
                                   receiver_unit + '.service'),
                           capture_output=True, timeout=15)
            subprocess.run(ssh_cmd('k8s-worker2', 'systemctl', 'reset-failed',
                                   receiver_unit + '.service'),
                           capture_output=True, timeout=15)
        if not completed:
            for host in ('master', 'k8s-worker3'):
                try:
                    remote(host, 'python3', ROOT / 'scripts/stop_attempt_processes.py',
                           '--id', args.id, '--output', remote_attempt /
                           ('early-stop-' + host + '.json'), timeout=35)
                except Exception as exc:
                    print(json.dumps({'process_cleanup_warning': host + ': ' +
                                      str(exc)}), flush=True)


if __name__ == '__main__':
    main()
