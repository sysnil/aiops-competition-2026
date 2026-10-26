"""Independent, bounded NetworkPath probes on a fixed five-second schedule."""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import socket
import subprocess
import threading
import time


RTT = re.compile(r'\btime=([0-9]+(?:\.[0-9]+)?)\s*ms')
PER_SAMPLE_TIMEOUT = 4


def utc(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def empty(status, **extra):
    return {'status': status, 'throughput_bps': None, 'loss_fraction': None,
            'jitter_ms': None, **extra}


def connections():
    result = subprocess.run(['ss', '-tn'], capture_output=True, text=True, timeout=2)
    if result.returncode:
        return []
    return [line.strip() for line in result.stdout.splitlines() if ':5201' in line][:12]


def iperf(source_ip, destination_ip):
    started = time.monotonic()
    command = ['iperf3', '-u', '-c', destination_ip, '-B', source_ip,
               '-p', '5201', '-b', '2000000', '-t', '1', '--json']
    child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True)
    time.sleep(.15)
    control = connections()
    try:
        stdout, stderr = child.communicate(timeout=max(.1, PER_SAMPLE_TIMEOUT -
                                                       (time.monotonic() - started)))
    except subprocess.TimeoutExpired:
        child.kill()
        _, stderr = child.communicate()
        return empty('TIMEOUT', elapsed_seconds=time.monotonic() - started,
                     control_connections_during=control, stderr=stderr[-500:])
    elapsed = time.monotonic() - started
    if not control:
        return empty('CONTROL_CONNECTION_FAILURE', elapsed_seconds=elapsed,
                     control_connections_during=[], stderr=stderr[-500:])
    if child.returncode:
        return empty('COMMAND_FAILURE', elapsed_seconds=elapsed,
                     control_connections_during=control, stderr=stderr[-500:])
    try:
        result = json.loads(stdout)
        received = result['end']['sum_received']
        packets = received['packets']
        lost = received['lost_packets']
        if result.get('error') or packets < 100 or not 0 <= lost <= packets:
            raise ValueError('Incomplete receiver evidence')
        return {'status': 'SUCCESS', 'throughput_bps': received['bits_per_second'],
                'loss_fraction': lost / packets, 'jitter_ms': received['jitter_ms'],
                'elapsed_seconds': elapsed, 'control_connections_during': control,
                'stderr': stderr[-500:]}
    except (KeyError, ValueError, TypeError) as exc:
        return empty('INVALID_RESULT', elapsed_seconds=elapsed,
                     control_connections_during=control, error=str(exc))


def icmp(destination_ip):
    started = time.monotonic()
    command = ['ping', '-n', '-c', '5', '-i', '0.2', '-W', '1', destination_ip]
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=PER_SAMPLE_TIMEOUT - .25)
    except subprocess.TimeoutExpired:
        return {'status': 'TIMEOUT', 'received': None, 'loss_fraction': None,
                'rtt_median_ms': None, 'elapsed_seconds': time.monotonic() - started}
    values = sorted(float(value) for value in RTT.findall(result.stdout))
    if not values:
        return {'status': 'TIMEOUT', 'received': 0, 'loss_fraction': None,
                'rtt_median_ms': None, 'elapsed_seconds': time.monotonic() - started,
                'exit_code': result.returncode}
    return {'status': 'SUCCESS' if len(values) == 5 else 'PARTIAL',
            'received': len(values), 'loss_fraction': (5 - len(values)) / 5,
            'rtt_median_ms': values[len(values) // 2],
            'elapsed_seconds': time.monotonic() - started,
            'exit_code': result.returncode}


def one_sample(index, scheduled, path, source_ip, destination_ip):
    observed = time.time()
    common = {'index': index, 'path': path, 'scheduled_utc': utc(scheduled),
              'observed_utc': utc(observed), 'start_lag_seconds': observed - scheduled}
    if observed - scheduled > 1:
        return {**common, 'status': 'SCHEDULE_MISS',
                'iperf3': empty('SCHEDULE_MISS', control_connections_during=[]),
                'icmp': {'status': 'SCHEDULE_MISS', 'received': None,
                         'loss_fraction': None, 'rtt_median_ms': None}}
    with ThreadPoolExecutor(max_workers=2) as pool:
        transfer = pool.submit(iperf, source_ip, destination_ip)
        ping = pool.submit(icmp, destination_ip)
        try:
            network = transfer.result(timeout=PER_SAMPLE_TIMEOUT + 1)
            latency = ping.result(timeout=PER_SAMPLE_TIMEOUT + 1)
        except Exception as exc:
            network = empty('COMMAND_FAILURE', control_connections_during=[],
                            error=type(exc).__name__)
            latency = {'status': 'COMMAND_FAILURE', 'received': None,
                       'loss_fraction': None, 'rtt_median_ms': None}
    return {**common, 'status': network['status'], 'iperf3': network, 'icmp': latency}


def schedule_samples(start, samples, interval, task, on_result):
    """The dispatcher never waits for a previous sample before scheduling the next."""
    futures = []
    lock = threading.Lock()
    def completed(future):
        row = future.result()
        with lock:
            on_result(row)
    with ThreadPoolExecutor(max_workers=8) as pool:
        for index in range(samples):
            scheduled = start + index * interval
            time.sleep(max(0, scheduled - time.time()))
            future = pool.submit(task, index, scheduled)
            future.add_done_callback(completed)
            futures.append(future)
        results = {future.result()['index']: future.result() for future in as_completed(futures)}
    return [results[index] for index in range(samples)]


def run(path, source_ip, destination_ip, start_utc, samples, interval, output):
    source_node, destination_node = path.split('/')[1:]
    if (socket.gethostname() != source_node or source_node == destination_node or
            samples not in (12, 24) or interval != 5 or output.exists()):
        raise ValueError('Probe parameters differ from registered Phase 4 policy')
    start = datetime.fromisoformat(start_utc.replace('Z', '+00:00')).timestamp()
    if not -2 <= start - time.time() <= 360:
        raise ValueError('Probe schedule is stale or too far ahead')
    route = json.loads(subprocess.check_output(
        ['ip', '-j', 'route', 'get', destination_ip], text=True, timeout=5))
    if len(route) != 1 or route[0].get('dev') != 'ens160' or route[0].get('prefsrc') != source_ip:
        raise ValueError('Node-pair route changed')
    output.mkdir(parents=True)
    live = output / 'observations-live.jsonl'
    with live.open('x') as stream:
        def persist(row):
            stream.write(json.dumps(row, separators=(',', ':')) + '\n')
            stream.flush()
        rows = schedule_samples(start, samples, interval,
            lambda index, at: one_sample(index, at, path, source_ip, destination_ip), persist)
    final = output / 'observations.jsonl'
    with final.open('x') as stream:
        for row in rows:
            stream.write(json.dumps(row, separators=(',', ':')) + '\n')
    counts = dict(Counter(row['status'] for row in rows))
    manifest = {'status': 'PHASE4_PROBES_COMPLETE', 'path': path,
                'source_ip': source_ip, 'destination_ip': destination_ip,
                'planned_samples': samples, 'interval_seconds': interval,
                'status_counts': counts,
                'observations_sha256': hashlib.sha256(final.read_bytes()).hexdigest(),
                'live_observations_sha256': hashlib.sha256(live.read_bytes()).hexdigest(),
                'timeout_imputation': None}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--path', required=True)
    parser.add_argument('--source-ip', required=True)
    parser.add_argument('--destination-ip', required=True)
    parser.add_argument('--start-utc', required=True)
    parser.add_argument('--samples', type=int, required=True)
    parser.add_argument('--interval', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.path, args.source_ip, args.destination_ip,
                         args.start_utc, args.samples, args.interval, args.output)), flush=True)


if __name__ == '__main__':
    main()
