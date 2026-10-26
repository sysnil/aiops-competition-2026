#!/usr/bin/env python3
"""Five-host fail-closed audit for active competition units before a case."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess


HOSTS = {
    'k8s-master': ('192.168.53.52', '/tmp/sf-timer-master.sock'),
    'k8s-worker1': ('192.168.53.53', '/tmp/sf-timer-w1.sock'),
    'k8s-worker2': ('192.168.53.54', '/tmp/sf-timer-w2.sock'),
    'k8s-worker3': ('192.168.53.55', '/tmp/sf-timer-w3.sock'),
    'router': ('192.168.53.50', '/tmp/sf-timer-vm1.sock'),
}
COMPETITION = re.compile(r'^ops-(?:sf|p04|a0[1-4])', re.IGNORECASE)


def active_competition_units(listing):
    return sorted({line.split(None, 1)[0] for line in listing.splitlines()
                   if line.strip() and COMPETITION.match(line.split(None, 1)[0])})


def remote(host, *argv):
    address, socket = HOSTS[host]
    result = subprocess.run(['ssh', '-S', socket, '-o', 'BatchMode=yes',
                             '-o', 'ConnectTimeout=5', 'root@' + address, *argv],
                            capture_output=True, text=True, timeout=25)
    if result.returncode:
        raise RuntimeError(host + ': ' + result.stderr[-500:])
    return result.stdout


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--generic-residue', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError('No overwrite of safety audit')
    generic = json.loads(args.generic_residue.read_text())
    age = (datetime.now(timezone.utc)-datetime.fromisoformat(generic['at_utc'])).total_seconds()
    if generic['status'] != 'HOST_RESIDUE_PASS' or not 0 <= age <= 120:
        raise RuntimeError('Fresh five-host general residue audit required')
    rows = {}
    for host in HOSTS:
        services = remote(host, 'systemctl', 'list-units', '--state=running',
                          '--type=service', '--plain', '--no-legend')
        timers = remote(host, 'systemctl', 'list-units', '--state=active',
                        '--type=timer', '--plain', '--no-legend')
        active = active_competition_units(services) + active_competition_units(timers)
        rows[host] = {'active_competition_units': sorted(active),
                      'service_inventory_sha256': hashlib.sha256(services.encode()).hexdigest(),
                      'timer_inventory_sha256': hashlib.sha256(timers.encode()).hexdigest()}
    result = {'status': 'COMPETITION_UNIT_RESIDUE_PASS' if all(
        not row['active_competition_units'] for row in rows.values()) else
        'COMPETITION_UNIT_RESIDUE_FAIL',
        'at_utc': datetime.now(timezone.utc).isoformat(),
        'generic_residue_sha256': hashlib.sha256(args.generic_residue.read_bytes()).hexdigest(),
        'hosts': rows}
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    args.output.chmod(0o600)
    print(json.dumps({'status': result['status'],
                      'active': {host: row['active_competition_units'] for host,row in rows.items()}}))
    if result['status'] != 'COMPETITION_UNIT_RESIDUE_PASS':
        raise SystemExit(1)
