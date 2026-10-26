"""Bounded, direction-specific NetworkPath rehearsal with qdisc watchdog."""

import argparse
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time

import yaml


INTERFACE = 'ens160'
STATE_ROOT = Path('/root/cross-layer-observability-control-rehearsal/runtime')
SCRIPT = Path(__file__).resolve()


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command(*args):
    return subprocess.check_output(args, text=True, timeout=15)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2) + '\n')
    os.replace(temp, path)


def qdisc_root():
    rows = json.loads(command('tc', '-j', 'qdisc', 'show', 'dev', INTERFACE))
    roots = [row for row in rows if row.get('root') is True]
    if len(roots) != 1:
        raise ValueError('NIC has no unique qdisc root')
    return roots[0], rows


def clean_qdisc():
    root, rows = qdisc_root()
    if root.get('kind') != 'mq' or any(
            row.get('kind') in {'prio', 'netem', 'tbf', 'htb'} for row in rows):
        raise ValueError('Network source qdisc is not clean default mq')


def precheck(plan_path, preflight_path, experiment_id):
    plan = yaml.safe_load(plan_path.read_text())
    preflight = json.loads(preflight_path.read_text())
    experiments = [row for row in plan['network_experiments']
                   if row['id'] == experiment_id]
    age = (datetime.now(timezone.utc) -
           datetime.fromisoformat(preflight['created_at_utc'])).total_seconds()
    if (len(experiments) != 1 or preflight.get('status') != 'RH_NETWORK_PREFLIGHT_READY' or
            preflight.get('experiment_id') != experiment_id or
            preflight.get('plan_sha256') != sha256(plan_path) or
            preflight.get('faults_injected') != 0 or not 0 <= age <= 300):
        raise ValueError('Network rehearsal preflight is stale or mismatched')
    spec = experiments[0]
    path = spec['path']
    source, destination = path.split('/')[1:]
    if (socket.gethostname() != source or preflight.get('path') != path or
            preflight.get('source_node') != source or
            preflight.get('destination_node') != destination or
            len(preflight.get('verified_traversing_edges', [])) < 2):
        raise ValueError('NetworkPath source or verified business edges changed')
    prefix = ipaddress.ip_network(preflight['business_route_prefix'], strict=True)
    node_ip = ipaddress.ip_address(preflight['destination_node_ip'])
    pod_ip = ipaddress.ip_address(preflight['verified_traversing_edges'][0]['destination_pod_ip'])
    if prefix.version != 4 or node_ip.version != 4 or pod_ip not in prefix:
        raise ValueError('Route prefix or destination IP is invalid')
    for ip, expected_gateway in ((str(node_ip), None), (str(pod_ip), str(node_ip))):
        rows = json.loads(command('ip', '-j', 'route', 'get', ip))
        if len(rows) != 1 or rows[0].get('dev') != INTERFACE or (
                rows[0].get('prefsrc') != preflight['source_node_ip'] or
                rows[0].get('gateway') != expected_gateway):
            raise ValueError('Current Node/probe/business route differs from pre-fault proof')
    clean_qdisc()
    return {'status': 'RH_NETWORK_PRECHECK_PASS', 'experiment_id': experiment_id,
            'host': source, 'path': path, 'duration_seconds': spec['fault_seconds'],
            'kind': spec['mechanism'], 'loss_percent': spec.get('loss_percent'),
            'rate_kbit': spec.get('rate_kbit'),
            'business_route_prefix': str(prefix),
            'destination_node_ip': str(node_ip),
            'preflight_sha256': sha256(preflight_path),
            'plan_sha256': sha256(plan_path)}


def cleanup(state_file):
    if not state_file.is_relative_to(STATE_ROOT) or not state_file.is_file():
        raise ValueError('Network cleanup state is outside independent root')
    state = json.loads(state_file.read_text())
    if state['host'] != socket.gethostname():
        raise ValueError('Network cleanup belongs to another source host')
    root, _ = qdisc_root()
    if root.get('kind') == 'prio' and root.get('handle') == '1:':
        subprocess.run(['tc', 'qdisc', 'del', 'dev', INTERFACE, 'root'],
                       check=True, timeout=15)
    elif root.get('kind') != 'mq':
        raise ValueError('Refusing to delete an unrelated qdisc')
    clean_qdisc()
    state['status'] = 'CLEANED'
    state['cleanup_at_utc'] = datetime.now(timezone.utc).isoformat()
    write_json(state_file, state)
    return {'status': 'RH_NETWORK_CLEANUP_PASS', 'experiment_id': state['experiment_id']}


def run(plan_path, preflight_path, experiment_id, start_utc, state_file):
    facts = precheck(plan_path, preflight_path, experiment_id)
    if state_file != STATE_ROOT / (experiment_id + '-' + facts['host'] + '.json') or state_file.exists():
        raise ValueError('Network state is not new and source-host-specific')
    start = datetime.fromisoformat(start_utc.replace('Z', '+00:00')).timestamp()
    if not -2 <= start - time.time() <= 120:
        raise ValueError('Network start must be within two minutes')
    if start > time.time():
        time.sleep(start-time.time())
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(SystemExit(143)))
    signal.signal(signal.SIGHUP, lambda *_: (_ for _ in ()).throw(SystemExit(129)))
    state = {**facts, 'status': 'ARMED', 'requested_start_utc': start_utc}
    write_json(state_file, state)
    unit = 'oc-' + experiment_id.lower() + '-' + facts['host'] + '-cleanup'
    subprocess.run(['systemd-run', '--quiet', '--unit=' + unit,
                    '--on-active=' + str(facts['duration_seconds'] + 20) + 's',
                    '/usr/bin/python3', str(SCRIPT), 'cleanup',
                    '--state-file', str(state_file)], check=True, timeout=15)
    state['watchdog_unit'] = unit
    write_json(state_file, state)
    try:
        clean_qdisc()
        subprocess.run(['tc', 'qdisc', 'replace', 'dev', INTERFACE, 'root',
                        'handle', '1:', 'prio', 'bands', '3', 'priomap',
                        *(['0'] * 16)], check=True, timeout=15)
        if facts['kind'] == 'egress_filtered_netem_loss':
            effect = ['netem', 'loss', str(facts['loss_percent']) + '%']
        elif facts['kind'] == 'egress_filtered_tbf_shaping':
            effect = ['tbf', 'rate', str(facts['rate_kbit']) + 'kbit',
                      'burst', '32kb', 'latency', '400ms']
        else:
            raise ValueError('Unsupported independent network mechanism')
        subprocess.run(['tc', 'qdisc', 'add', 'dev', INTERFACE,
                        'parent', '1:3', 'handle', '30:', *effect], check=True, timeout=15)
        for priority, target in enumerate((facts['business_route_prefix'],
                                           facts['destination_node_ip'] + '/32'), 1):
            subprocess.run(['tc', 'filter', 'add', 'dev', INTERFACE,
                            'protocol', 'ip', 'parent', '1:0', 'prio', str(priority),
                            'u32', 'match', 'ip', 'dst', target, 'flowid', '1:3'],
                           check=True, timeout=15)
        state['status'] = 'ACTIVE'
        state['start_at_utc'] = datetime.now(timezone.utc).isoformat()
        write_json(state_file, state)
        print(json.dumps({'status': 'RH_NETWORK_ACTIVE', 'experiment_id': experiment_id,
                          'start_at_utc': state['start_at_utc']}), flush=True)
        time.sleep(facts['duration_seconds'])
    finally:
        result = cleanup(state_file)
        subprocess.run(['systemctl', 'stop', unit + '.timer'], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=('precheck', 'run', 'cleanup'))
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--preflight', type=Path)
    parser.add_argument('--experiment-id')
    parser.add_argument('--start-utc')
    parser.add_argument('--state-file', type=Path)
    args = parser.parse_args()
    if args.operation == 'precheck':
        result = precheck(args.plan, args.preflight, args.experiment_id)
    elif args.operation == 'run':
        result = run(args.plan, args.preflight, args.experiment_id,
                     args.start_utc, args.state_file)
    else:
        result = cleanup(args.state_file)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
