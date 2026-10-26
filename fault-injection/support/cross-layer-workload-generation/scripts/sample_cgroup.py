"""Read-only one-second cgroup CPU/memory samples bound to a Pod UID."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import socket
import time

from host_fault import cgroup_path, task_pid


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cpu_stat(path):
    return {key: int(value) for key, value in
            (row.split() for row in (path / 'cpu.stat').read_text().splitlines())}


def run(preflight_path, pod_name, start_utc, seconds, output):
    preflight = json.loads(preflight_path.read_text())
    matches = [row for row in preflight['targets'] + preflight.get('controls', [])
               if row['name'] == pod_name and row['node'] == socket.gethostname()]
    if (preflight['status'] != 'RH_PREFLIGHT_READY' or len(matches) != 1 or
            seconds not in (60, 75, 120) or output.exists()):
        raise ValueError('Cgroup sampler needs one frozen local Pod identity')
    pod = matches[0]
    pid = task_pid(pod['container_id'])
    cgroup = cgroup_path(pid, pod['uid'], pod['container_id'])
    start = datetime.fromisoformat(start_utc.replace('Z', '+00:00')).timestamp()
    if not -2 <= start-time.time() <= 180:
        raise ValueError('Cgroup sample start is not fresh')
    output.mkdir(parents=True)
    with (output / 'observations.jsonl').open('x') as stream:
        for index in range(seconds):
            scheduled = start + index
            if scheduled > time.time():
                time.sleep(scheduled-time.time())
            if not cgroup.is_dir():
                raise ValueError('Target cgroup disappeared during sampling')
            observed = time.time()
            row = {'index': index, 'pod': pod_name, 'pod_uid': pod['uid'],
                   'node': pod['node'], 'container_id': pod['container_id'],
                   'scheduled_utc': datetime.fromtimestamp(
                       scheduled, timezone.utc).isoformat(),
                   'observed_utc': datetime.fromtimestamp(
                       observed, timezone.utc).isoformat(),
                   'cpu_stat': cpu_stat(cgroup),
                   'cpu_max': (cgroup / 'cpu.max').read_text().strip(),
                   'memory_current_bytes': int((cgroup / 'memory.current').read_text())}
            stream.write(json.dumps(row) + '\n')
            stream.flush()
    path = output / 'observations.jsonl'
    manifest = {'status': 'RH_CGROUP_SAMPLE_COMPLETE', 'pod': pod_name,
                'pod_uid': pod['uid'], 'node': pod['node'],
                'preflight_sha256': sha256(preflight_path),
                'samples': seconds, 'interval_seconds': 1,
                'observations_sha256': sha256(path)}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--preflight', required=True, type=Path)
    parser.add_argument('--pod-name', required=True)
    parser.add_argument('--start-utc', required=True)
    parser.add_argument('--seconds', required=True, type=int)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.preflight, args.pod_name,
                         args.start_utc, args.seconds, args.output)), flush=True)


if __name__ == '__main__':
    main()
