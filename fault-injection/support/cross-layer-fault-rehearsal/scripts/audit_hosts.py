"""Read-only five-host residue gate for isolated fault rehearsals."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess


EXPECTED = {'k8s-master', 'k8s-worker1', 'k8s-worker2', 'k8s-worker3', 'router'}
FAULT_PROCESS = re.compile(
    r'(?:stage4e|stage4f|stage5)[^ ]*/(?:fault|inject|stress)|'
    r'/cross-layer-fault-rehearsal/scripts/(?:host_fault|network_fault)\.py', re.I)
FAULT_QDISC = re.compile(r'\bqdisc\s+(?:netem|tbf|htb|prio)\b')
BENIGN_UNITS = {'stage5-v2-explorer-quick-tunnel.service', 'rh-iperf-server.service'}


def remote(ip, socket, *command):
    return subprocess.check_output(
        ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
         '-o', 'ControlPath=' + socket, 'root@' + ip, *command],
        text=True, timeout=25)


def audit(name, ip, socket):
    processes = remote(ip, socket, 'ps', '-eo', 'pid=,comm=,args=')
    qdiscs = remote(ip, socket, 'tc', 'qdisc', 'show')
    units = remote(ip, socket, 'systemctl', 'list-units', '--state=running',
                   '--type=service', '--plain', '--no-legend')
    timers = remote(ip, socket, 'systemctl', 'list-units', '--state=active',
                    '--type=timer', '--plain', '--no-legend')
    bad_processes = []
    for line in processes.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) == 3 and (parts[1] in {'stress', 'stress-ng', 'yes', 'fio'} or
                                FAULT_PROCESS.search(parts[2])):
            bad_processes.append(line.strip()[:220])
    bad_qdiscs = [line for line in qdiscs.splitlines() if FAULT_QDISC.search(line)]
    bad_units, benign_units = [], []
    for line in units.splitlines():
        unit = line.strip().split(None, 1)[0] if line.strip() else ''
        if unit in BENIGN_UNITS:
            benign_units.append(unit)
        elif re.search(r'stage4|stage5|fault-|^rh-', unit, re.I):
            bad_units.append(unit)
    bad_timers = [line.strip().split(None, 1)[0] for line in timers.splitlines()
                  if re.search(r'stage4|stage5|fault-|\brh-', line, re.I)]
    result = {'host': name, 'status': 'PASS' if not any((bad_processes, bad_qdiscs,
             bad_units, bad_timers)) else 'FAIL',
             'suspicious_processes': bad_processes, 'injected_qdiscs': bad_qdiscs,
             'fault_units': bad_units, 'fault_timers': bad_timers,
             'benign_units': benign_units,
             'qdisc_sha256': hashlib.sha256(qdiscs.encode()).hexdigest()}
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', action='append', required=True,
                        help='name,ip,authenticated-control-socket')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    hosts = {}
    for value in args.host:
        name, ip, socket = value.split(',', 2)
        if name in hosts:
            raise ValueError('Duplicate host')
        hosts[name] = (ip, socket)
    if set(hosts) != EXPECTED or args.output.exists():
        raise ValueError('Need five hosts and a new output path')
    results = {name: audit(name, *hosts[name]) for name in sorted(hosts)}
    result = {'status': 'HOST_RESIDUE_PASS' if all(row['status'] == 'PASS'
              for row in results.values()) else 'HOST_RESIDUE_FAIL',
              'at_utc': datetime.now(timezone.utc).isoformat(), 'hosts': results,
              'known_benign_unit_exception': sorted(BENIGN_UNITS)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'status': result['status'], 'hosts': {
        name: row['status'] for name, row in results.items()}}))
    if result['status'] != 'HOST_RESIDUE_PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
