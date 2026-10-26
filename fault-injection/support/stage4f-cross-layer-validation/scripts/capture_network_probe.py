#!/usr/bin/env python3
"""Record timestamped, low-rate public node-path RTT/loss without a fault."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import time


RTT = re.compile(r'time=([0-9]+(?:\.[0-9]+)?)\s*ms')


def parse_utc(value):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('Probe start must have timezone')
    return stamp.astimezone(timezone.utc).timestamp()


def capture(destination_ip, source_node, destination_node, start_utc,
            intervals, seconds_per_interval, packets, output, index_offset=0):
    if output.exists():
        raise ValueError('Refusing to overwrite network probe')
    if packets < 5 or seconds_per_interval < 10 or intervals < 20 or index_offset < 0:
        raise ValueError('Insufficient normal path probe budget')
    start = parse_utc(start_utc)
    if start - time.time() > 120 or time.time() > start + 2:
        raise ValueError('Network probe must start at its predeclared live time')
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x') as stream:
        for index in range(intervals):
            scheduled = start + index * seconds_per_interval
            delay = scheduled - time.time()
            if delay > 0:
                time.sleep(delay)
            observed = time.time()
            if observed - scheduled > 2:
                raise ValueError('Network probe missed a 15-second sample boundary')
            command = ['ping', '-n', '-c', str(packets), '-i', '0.2', '-W', '1',
                       destination_ip]
            result = subprocess.run(command, capture_output=True, text=True,
                                    timeout=seconds_per_interval - 2)
            rtts = [float(value) for value in RTT.findall(result.stdout)]
            if len(rtts) > packets:
                raise ValueError('More RTTs than transmitted probe packets')
            row = {'schema_version': 1, 'source': 'live_node_path_probe',
                   'source_node': source_node, 'destination_node': destination_node,
                   'destination_ip': destination_ip,
                   'sample_index': index + index_offset,
                   'scheduled_unix_ns': round(scheduled * 1e9),
                   'observed_unix_ns': round(observed * 1e9),
                   'sent': packets, 'received': len(rtts), 'rtt_ms': rtts,
                   'command_exit_code': result.returncode}
            stream.write(json.dumps(row, separators=(',', ':')) + '\n')
            stream.flush()
    return {'samples': intervals, 'packets': intervals * packets,
            'source_node': source_node, 'destination_node': destination_node}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--destination-ip', required=True)
    parser.add_argument('--source-node', required=True)
    parser.add_argument('--destination-node', required=True)
    parser.add_argument('--start-utc', required=True)
    parser.add_argument('--intervals', type=int, default=60)
    parser.add_argument('--seconds-per-interval', type=int, default=15)
    parser.add_argument('--packets-per-interval', type=int, default=5)
    parser.add_argument('--index-offset', type=int, default=0)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(capture(args.destination_ip, args.source_node,
                             args.destination_node, args.start_utc,
                             args.intervals, args.seconds_per_interval,
                             args.packets_per_interval, args.output,
                             args.index_offset)))


if __name__ == '__main__':
    main()
