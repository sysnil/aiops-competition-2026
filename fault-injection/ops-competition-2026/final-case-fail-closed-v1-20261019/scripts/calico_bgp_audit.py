#!/usr/bin/env python3
"""Read-only current Calico full-mesh BGP status on all four Nodes."""

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def command(argv):
    return subprocess.check_output(argv, text=True, timeout=15)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError('Immutable BGP output exists')
    pods = json.loads(command(['kubectl', '-n', 'calico-system', 'get', 'pods',
                               '-l', 'k8s-app=calico-node', '-o', 'json']))
    rows = []
    for pod in pods['items']:
        name = pod['metadata']['name']
        raw = command(['kubectl', '-n', 'calico-system', 'exec', name,
                       '-c', 'calico-node', '--', 'birdcl', 'show', 'protocols'])
        peers = [line for line in raw.splitlines() if ' BGP ' in line]
        rows.append({'pod': name, 'node': pod['spec']['nodeName'],
                     'peer_count': len(peers),
                     'established_count': sum('Established' in line for line in peers),
                     'raw': raw})
    result = {'status': 'CALICO_BGP_FOUR_NODES_THREE_OF_THREE_PASS'
              if len(rows) == 4 and all(row['peer_count'] == 3 and
                                        row['established_count'] == 3
                                        for row in rows) else 'CALICO_BGP_BLOCKED',
              'at_utc': datetime.now(timezone.utc).isoformat(),
              'rows': rows, 'read_only': True}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': result['status'],
                      'nodes': len(rows),
                      'established_peers': sum(row['established_count']
                                               for row in rows)}))
    if result['status'] != 'CALICO_BGP_FOUR_NODES_THREE_OF_THREE_PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
