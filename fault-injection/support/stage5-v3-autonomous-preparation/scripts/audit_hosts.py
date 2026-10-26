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
    r'/(?:cross-layer-fault-rehearsal|cross-layer-workload-generation|cross-layer-observability-control-rehearsal|cross-layer-rehearsal-phase3|cross-layer-rehearsal-phase4|stage5-v3-mechanism-qualification|stage5-v3-autonomous-preparation)/scripts/(?:host_fault|resource_fault|network_fault)\.py', re.I)
FAULT_QDISC = re.compile(r'\bqdisc\s+(?:netem|tbf|htb|prio)\b')
BENIGN_UNITS = {'stage5-v2-explorer-quick-tunnel.service', 'rh-iperf-server.service'}
PROXY_ROLLBACK_TIMERS = {'s5v3ap-proxy-rollback.timer',
                         's5v3ap-proxy-rollback-extended.timer'}


def remote(ip, socket, *command):
    return subprocess.check_output(
        ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
         '-o', 'ControlPath=' + socket, 'root@' + ip, *command],
        text=True, timeout=25)


def audit(name, ip, socket, allow_payment_watchdog=False,
          expected_proxy_watchdog=False,
          expected_probe_unit=None):
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
    probe_seen = False
    for line in units.splitlines():
        unit = line.strip().split(None, 1)[0] if line.strip() else ''
        if unit == expected_probe_unit and name == 'k8s-worker2':
            probe_seen = True
        elif unit in BENIGN_UNITS:
            benign_units.append(unit)
        elif re.search(r'stage4|stage5|fault-|^rh-|^wg-|^oc-|^p3-|^p4-|^mq-p1-|^s5v3ap-', unit, re.I):
            bad_units.append(unit)
    active_timers = [line.strip().split(None, 1)[0] for line in timers.splitlines()]
    expected_watchdog = (allow_payment_watchdog and name == 'k8s-master')
    expected_proxy = expected_proxy_watchdog and name == 'k8s-master'
    bad_timers = [unit for unit in active_timers
                  if re.search(r'stage4|stage5|fault-|\brh-|\bwg-|\boc-|\bp3-|\bp4-|^mq-p1-|^s5v3ap-', unit, re.I)
                  and not (expected_watchdog and unit == 'p4-checkout-rollback.timer')
                  and not (expected_proxy and unit in PROXY_ROLLBACK_TIMERS)]
    missing_watchdog = expected_watchdog and 'p4-checkout-rollback.timer' not in active_timers
    missing_proxy_watchdog = (expected_proxy and
                              not PROXY_ROLLBACK_TIMERS.intersection(active_timers))
    result = {'host': name, 'status': 'PASS' if not any((bad_processes, bad_qdiscs,
             bad_units, bad_timers, missing_watchdog, missing_proxy_watchdog,
             expected_probe_unit is not None and name == 'k8s-worker2' and
             not probe_seen)) else 'FAIL',
             'suspicious_processes': bad_processes, 'injected_qdiscs': bad_qdiscs,
             'fault_units': bad_units, 'fault_timers': bad_timers,
             'expected_payment_watchdog_present': not missing_watchdog if expected_watchdog else None,
             'expected_proxy_watchdog_present': not missing_proxy_watchdog if expected_proxy else None,
             'registered_probe_unit': expected_probe_unit if probe_seen else None,
             'benign_units': benign_units,
             'qdisc_sha256': hashlib.sha256(qdiscs.encode()).hexdigest()}
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', action='append', required=True,
                        help='name,ip,authenticated-control-socket')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--payment-setup', type=Path,
                        help='Allow only the registered Checkout rollback timer during Payment study')
    parser.add_argument('--expected-probe-unit',
                        help='Allow one named active Worker2 receiver during this attempt only')
    parser.add_argument('--expected-proxy-watchdog', action='store_true',
                        help='Require the registered proxy repair rollback timer on Master')
    args = parser.parse_args()
    hosts = {}
    for value in args.host:
        name, ip, socket = value.split(',', 2)
        if name in hosts:
            raise ValueError('Duplicate host')
        hosts[name] = (ip, socket)
    if set(hosts) != EXPECTED or args.output.exists():
        raise ValueError('Need five hosts and a new output path')
    if args.expected_probe_unit and not re.fullmatch(
            r's5v3ap-iperf-s5v3ap-(?:service|pod|node|network)-s[12]\.service',
            args.expected_probe_unit):
        raise ValueError('Probe exception is not a preregistered Phase 1 unit')
    if args.payment_setup:
        setup = json.loads(args.payment_setup.read_text())
        age = (datetime.now(timezone.utc) -
               datetime.fromisoformat(setup['started_at_utc'])).total_seconds()
        if setup.get('status') != 'PAYMENT_CLIENT_SETUP_PASS' or not 0 <= age <= 1800:
            raise ValueError('Registered Payment setup is absent or stale')
    results = {name: audit(name, *hosts[name],
                           allow_payment_watchdog=bool(args.payment_setup),
                           expected_proxy_watchdog=args.expected_proxy_watchdog,
                           expected_probe_unit=args.expected_probe_unit)
               for name in sorted(hosts)}
    result = {'status': 'HOST_RESIDUE_PASS' if all(row['status'] == 'PASS'
              for row in results.values()) else 'HOST_RESIDUE_FAIL',
              'at_utc': datetime.now(timezone.utc).isoformat(), 'hosts': results,
              'known_benign_unit_exception': sorted(BENIGN_UNITS),
              'registered_active_probe_exception': args.expected_probe_unit}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'status': result['status'], 'hosts': {
        name: row['status'] for name, row in results.items()}}))
    if result['status'] != 'HOST_RESIDUE_PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
