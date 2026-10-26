"""Compare bounded iperf3 and ICMP probes without imputing failed samples."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import socket
import subprocess
import time


RTT = re.compile(r'\btime=([0-9]+(?:\.[0-9]+)?)\s*ms')


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def control_connections():
    text = subprocess.check_output(['ss', '-tn'], text=True, timeout=3)
    return [line.strip() for line in text.splitlines() if ':5201' in line][:12]


def iperf(source_ip, destination_ip):
    command = ['iperf3', '-u', '-c', destination_ip, '-B', source_ip,
               '-p', '5201', '-b', '2000000', '-t', '1', '--json']
    started = time.monotonic()
    child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True)
    time.sleep(.2)
    during_control = control_connections()
    try:
        stdout, stderr = child.communicate(timeout=8)
    except subprocess.TimeoutExpired:
        child.kill()
        stdout, stderr = child.communicate()
        return {'status': 'TIMEOUT', 'throughput_bps': None,
                'loss_fraction': None, 'jitter_ms': None,
                'elapsed_seconds': time.monotonic() - started,
                'control_connections_during': during_control,
                'control_connections_after': control_connections(),
                'stderr': stderr[:1000]}
    if child.returncode != 0:
        return {'status': 'COMMAND_FAILURE', 'throughput_bps': None,
                'loss_fraction': None, 'jitter_ms': None,
                'elapsed_seconds': time.monotonic() - started,
                'control_connections_during': during_control,
                'control_connections_after': control_connections(),
                'stderr': stderr[:1000]}
    try:
        payload = json.loads(stdout)
        received = payload['end']['sum_received']
        packets = received['packets']
        lost = received['lost_packets']
        if payload.get('error') or packets < 100 or not 0 <= lost <= packets:
            raise ValueError('Receiver result is incomplete')
        return {'status': 'PASS', 'throughput_bps': received['bits_per_second'],
                'loss_fraction': lost / packets, 'jitter_ms': received['jitter_ms'],
                'elapsed_seconds': time.monotonic() - started,
                'control_connections_during': during_control,
                'control_connections_after': control_connections(),
                'stderr': stderr[:1000]}
    except (KeyError, ValueError, TypeError) as exc:
        return {'status': 'INVALID_RESULT', 'throughput_bps': None,
                'loss_fraction': None, 'jitter_ms': None,
                'elapsed_seconds': time.monotonic() - started,
                'control_connections_during': during_control,
                'control_connections_after': control_connections(),
                'error': str(exc)}


def ping(destination_ip):
    command = ['ping', '-n', '-c', '5', '-i', '0.2', '-W', '1', destination_ip]
    started = time.monotonic()
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=4)
    except subprocess.TimeoutExpired:
        return {'status': 'TIMEOUT', 'received': None, 'loss_fraction': None,
                'rtt_median_ms': None, 'elapsed_seconds': time.monotonic() - started}
    values = sorted(float(value) for value in RTT.findall(result.stdout))
    return {'status': 'PASS' if len(values) == 5 else 'PARTIAL',
            'received': len(values), 'loss_fraction': (5-len(values))/5,
            'rtt_median_ms': values[len(values)//2] if values else None,
            'elapsed_seconds': time.monotonic() - started,
            'exit_code': result.returncode}


def run(path, source_ip, destination_ip, start_utc, samples, interval, output):
    source_node, destination_node = path.split('/')[1:]
    if (socket.gethostname() != source_node or source_node == destination_node or
            samples not in (6, 8, 11, 12) or interval not in (10, 15) or output.exists()):
        raise ValueError('Network probe is not bound to one isolated source path')
    start = datetime.fromisoformat(start_utc.replace('Z', '+00:00')).timestamp()
    if not -2 <= start - time.time() <= 180:
        raise ValueError('Network probe start is not fresh')
    route = json.loads(subprocess.check_output(
        ['ip', '-j', 'route', 'get', destination_ip], text=True, timeout=5))
    if len(route) != 1 or route[0].get('dev') != 'ens160' or (
            route[0].get('prefsrc') != source_ip):
        raise ValueError('Node-pair probe route changed')
    output.mkdir(parents=True)
    with (output / 'observations.jsonl').open('x') as stream:
        for index in range(samples):
            scheduled = start + interval*index
            if scheduled > time.time():
                time.sleep(scheduled-time.time())
            observed = time.time()
            if observed - scheduled > 2:
                row = {'index': index, 'path': path,
                       'scheduled_utc': datetime.fromtimestamp(
                           scheduled, timezone.utc).isoformat(),
                       'observed_utc': datetime.fromtimestamp(
                           observed, timezone.utc).isoformat(),
                       'iperf3': {'status': 'SCHEDULE_MISS', 'throughput_bps': None,
                                  'loss_fraction': None, 'jitter_ms': None,
                                  'control_connections_during': []},
                       'icmp': {'status': 'SCHEDULE_MISS', 'received': None,
                                'loss_fraction': None, 'rtt_median_ms': None}}
                stream.write(json.dumps(row) + '\n')
                stream.flush()
                continue
            result = {'index': index, 'path': path,
                      'scheduled_utc': datetime.fromtimestamp(
                          scheduled, timezone.utc).isoformat(),
                      'observed_utc': datetime.fromtimestamp(
                          observed, timezone.utc).isoformat(),
                      'iperf3': iperf(source_ip, destination_ip),
                      'icmp': ping(destination_ip)}
            stream.write(json.dumps(result) + '\n')
            stream.flush()
    samples_path = output / 'observations.jsonl'
    manifest = {'status': 'RH_NETWORK_PROBES_COMPLETE', 'path': path,
                'source_ip': source_ip, 'destination_ip': destination_ip,
                'samples': samples, 'step_seconds': interval,
                'observations_sha256': sha256(samples_path)}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--path', required=True)
    parser.add_argument('--source-ip', required=True)
    parser.add_argument('--destination-ip', required=True)
    parser.add_argument('--start-utc', required=True)
    parser.add_argument('--samples', required=True, type=int)
    parser.add_argument('--interval', type=int, default=15)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.path, args.source_ip, args.destination_ip,
                         args.start_utc, args.samples, args.interval, args.output)), flush=True)


if __name__ == '__main__':
    main()
