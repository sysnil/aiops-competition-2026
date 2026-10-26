#!/usr/bin/env python3
"""Join clean-before and fault-during node-path probes without filling gaps."""

import argparse
import json
from pathlib import Path


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def merge(before, during):
    clean = read_rows(before)[:40]
    fault = read_rows(during)
    if len(clean) != 40 or len(fault) != 20:
        raise ValueError('Need 40 clean and 20 during 15-second network probes')
    rows = clean + fault
    if [row['sample_index'] for row in rows] != list(range(60)):
        raise ValueError('Missing or duplicate network probe indices')
    identities = {(row['source_node'], row['destination_node'], row['destination_ip'],
                   row['sent']) for row in rows}
    if len(identities) != 1 or next(iter(identities))[3] < 5:
        raise ValueError('Node path or probe packet budget changed')
    stamps = [row['scheduled_unix_ns'] for row in rows]
    if stamps != sorted(set(stamps)):
        raise ValueError('Network probe time windows overlap or go backward')
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--before', type=Path, required=True)
    parser.add_argument('--during', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refusing to overwrite network probe merge')
    rows = merge(args.before, args.during)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        for row in rows:
            stream.write(json.dumps(row, separators=(',', ':')) + '\n')
    print(json.dumps({'status': 'NETWORK_PROBE_PHASES_MERGED', 'samples': len(rows)}))


if __name__ == '__main__':
    main()
