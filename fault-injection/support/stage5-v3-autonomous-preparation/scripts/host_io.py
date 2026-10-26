"""Bounded SSH helpers for independent qualification runs on the Mac."""

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time


ROOT = Path('/root/stage5-v3-autonomous-preparation')
LOCAL = Path('stage5-v3-autonomous-preparation')
HOSTS = {
    'master': ('192.168.53.52', '/tmp/stage4f-formal-ssh'),
    'k8s-worker1': ('192.168.53.53', '/tmp/s5v3q-worker1-ssh'),
    'k8s-worker2': ('192.168.53.54', '/tmp/s5v3q-worker2-ssh'),
    'k8s-worker3': ('192.168.53.55', '/tmp/s5v3q-worker3-ssh'),
    'router': ('192.168.53.50', '/tmp/s5v3q-router-ssh'),
}


def ssh_cmd(host, *args):
    ip, socket = HOSTS[host]
    return ['ssh', '-o', 'BatchMode=yes', '-o', 'ControlMaster=no',
            '-o', 'ConnectTimeout=5', '-o', 'ControlPath=' + socket,
            'root@' + ip, *map(str, args)]


def remote(host, *args, timeout=90):
    result = subprocess.run(ssh_cmd(host, *args), capture_output=True,
                            text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(host + ' command failed: ' + result.stderr[-500:])
    return result.stdout


def scp_to(host, local, remote_path):
    ip, socket = HOSTS[host]
    subprocess.run(['scp', '-q', '-o', 'BatchMode=yes',
                    '-o', 'ControlPath=' + socket, str(local),
                    'root@' + ip + ':' + str(remote_path)],
                   check=True, timeout=60)


def scp_from(host, remote_path, local, recursive=False):
    ip, socket = HOSTS[host]
    command = ['scp', '-q', '-o', 'BatchMode=yes', '-o', 'ControlPath=' + socket]
    if recursive:
        command.append('-r')
    subprocess.run([*command, 'root@' + ip + ':' + str(remote_path), str(local)],
                   check=True, timeout=90)


def wait_until(value):
    delay = value.timestamp() - time.time()
    if delay > 0:
        time.sleep(delay)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ValueError('Refusing overwrite: ' + str(path))
    path.write_text(json.dumps(value, indent=2) + '\n')


def finish_processes(processes, timeout):
    result = {}
    for name, process in processes.items():
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            result[name] = {'exit_code': process.returncode,
                            'stdout': stdout[-1000:], 'stderr': stderr[-2000:]}
        except subprocess.TimeoutExpired:
            process.terminate()
            stdout, stderr = process.communicate(timeout=15)
            result[name] = {'exit_code': 'TIMEOUT',
                            'stdout': stdout[-1000:], 'stderr': stderr[-2000:]}
    return result
