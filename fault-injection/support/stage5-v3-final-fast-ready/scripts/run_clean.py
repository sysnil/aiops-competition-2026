#!/usr/bin/env python3
"""One detached, no-fault 10+5+10 run; independent archive/health finalization follows."""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import yaml

from clean_protocol import (DATA_ROOT, PHASES, assert_disk, save_new, sha,
                            validate_plan, validate_snapshot)
from live_safety import tick as safety_tick


SOURCE = Path('/root/stage5-v3-final-fast-ready')
STATE = Path('/root/cross-layer-observability-control-rehearsal/scripts/workload_generator_rehearsal.py')
CONTROLS = Path('/root/stage5-v3-autonomous-preparation/scripts/fixed_control_load_c3.py')
MACHINE = Path('/root/stage5-v3-observability-diagnostics/snapshots/reference/workload-state-machine-c3.yaml')
PROFILES = Path('/root/stage5-v3-autonomous-preparation/design/load-profiles-c3.yaml')
PEOPLE = Path('/root/cross-layer-workload-generation/design/people.json')
PIPELINE = Path('/root/stage5-v3-clean-gate-v2/scripts/capture_pipeline_v3.py')
CPU_SAMPLER = Path('/root/stage5-v3-final-fast-ready/scripts/cpu_safety_master.py')
BASE_URL = 'http://10.104.177.128:8080'
KUBECTL = ['/usr/bin/kubectl', '--kubeconfig=/etc/kubernetes/admin.conf',
           '--context=kubernetes-admin@kubernetes']


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def kube(*args, namespace=True):
    prefix = KUBECTL + (['-n', 'otel-demo'] if namespace else [])
    return json.loads(subprocess.check_output(prefix + list(args) + ['-o', 'json'],
                      text=True, timeout=45))


def current_snapshot(route_proof_sha):
    pods = kube('get', 'pods')['items']
    endpoints = kube('get', 'endpointslices')
    collector = kube('get', 'configmap', 'otel-collector')
    names = ('checkout', 'cart', 'recommendation', 'frontend', 'frontend-proxy',
             'image-provider', 'payment', 'shipping', 'quote')
    identities, placements = {}, {}
    for name in names:
        rows = [row for row in pods if row['metadata']['labels'].get(
            'opentelemetry.io/name') == name and row['status'].get('phase') == 'Running'
            and row['status'].get('containerStatuses') and all(c.get('ready')
            for c in row['status']['containerStatuses'])]
        identities[name] = sorted(row['metadata']['uid'] for row in rows)
        placements[name] = sorted((row['metadata']['uid'], row['spec']['nodeName'],
            row['status']['podIP']) for row in rows)
        if not rows:
            raise RuntimeError('Business Pod not Ready: ' + name)
    target = {name: sorted((tuple(sorted((row.get('targetRef') or {}).items())),
                       tuple(row.get('addresses', [])), tuple(sorted((row.get('conditions') or {}).items())))
                       for item in endpoints['items'] if item['metadata']['labels'].get(
                           'kubernetes.io/service-name') == name
                       for row in item.get('endpoints', []))
              for name in names}
    raw_endpoint = json.dumps(target, sort_keys=True).encode()
    checkout = placements['checkout']
    cart = placements['cart']
    if (len(checkout) != 1 or len(cart) != 1 or
            checkout[0][1] != 'k8s-worker2' or cart[0][1] != 'k8s-worker3' or
            not any(any(row[0] and ('uid', checkout[0][0]) in row[0] for row in value)
                    for name, value in target.items() if name == 'checkout') or
            not any(any(row[0] and ('uid', cart[0][0]) in row[0] for row in value)
                    for name, value in target.items() if name == 'cart')):
        raise RuntimeError('Target Pod/EndpointSlice linkage changed')
    calico_pods = json.loads(subprocess.check_output(KUBECTL + ['-n', 'calico-system',
        'get', 'pods', '-o', 'json'], text=True, timeout=30))['items']
    calico_names = [row['metadata']['name'] for row in calico_pods
                    if row['metadata']['name'].startswith('calico-node-') and
                    row['spec']['nodeName'] == 'k8s-worker2' and
                    row['status']['phase'] == 'Running']
    if len(calico_names) != 1:
        raise RuntimeError('Worker2 Calico route source ambiguous')
    routes = json.loads(subprocess.check_output(KUBECTL + ['-n', 'calico-system',
        'exec', calico_names[0], '-c', 'calico-node', '--', 'ip', '-j', 'route', 'get',
        cart[0][2]], text=True, timeout=30))
    if len(routes) != 1 or routes[0].get('gateway') != '192.168.53.55' or \
            routes[0].get('dev') != 'ens160' or \
            routes[0].get('prefsrc') != '192.168.53.54':
        raise RuntimeError('Live Worker2 to Worker3 route drift')
    route_key = {key: routes[0].get(key) for key in ('dst', 'gateway', 'dev', 'prefsrc')}
    return {'observed_utc': utc_now(), 'pod_uids': identities,
        'node_placement': placements,
        'endpoint_slices_sha256': hashlib.sha256(raw_endpoint).hexdigest(),
        'route_proof_sha256': route_proof_sha,
        'live_route_sha256': hashlib.sha256(json.dumps(route_key,
            sort_keys=True).encode()).hexdigest(), 'live_route': route_key,
        'collector_config_sha256': hashlib.sha256(collector['data']['relay'].encode()).hexdigest(),
        'network_path': 'NETWORK_PATH/k8s-worker2/k8s-worker3'}


def check_health(health, residue):
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(
        health['captured_at_utc'])).total_seconds()
    residue_age = (datetime.now(timezone.utc) - datetime.fromisoformat(
        residue['at_utc'])).total_seconds()
    if (health['status'] != 'GLOBAL_SAFETY_PASS' or health.get('blockers') or
        residue['status'] != 'HOST_RESIDUE_PASS' or not 0 <= age <= 180 or
        not 0 <= residue_age <= 180):
        raise RuntimeError('Fresh Global Safety and five-host residue required')


def validate_workload(plan):
    profile = yaml.safe_load(PROFILES.read_text())['profiles']['MEDIUM']
    machine = yaml.safe_load(MACHINE.read_text())
    if (profile['arrival_rate_rps'] != 1.4 or
        {name: row['weight'] for name, row in machine['workflows'].items()} !=
            plan['workload']['weights']):
        raise RuntimeError('State-machine generation changed after preregistration')


def append_event(run, stage, status, planned_start=None, planned_end=None, **extra):
    record = {'stage': stage, 'status': status, 'at_utc': utc_now(),
              'planned_start_utc': planned_start, 'planned_end_utc': planned_end,
              'fault_injected': False, **extra}
    with (run / 'events.jsonl').open('a') as stream:
        stream.write(json.dumps(record, sort_keys=True) + '\n')
        stream.flush(); os.fsync(stream.fileno())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--prereg', type=Path, required=True)
    parser.add_argument('--health', type=Path, required=True)
    parser.add_argument('--residue', type=Path, required=True)
    parser.add_argument('--route-proof', type=Path, required=True)
    args = parser.parse_args()
    plan = json.loads(args.prereg.read_text())
    run_id = validate_plan(plan)
    run = DATA_ROOT / 'runs' / run_id
    if run.exists() or args.route_proof.stat().st_mtime < time.time() - 180:
        raise RuntimeError('Run ID exists or route proof is stale')
    sys.path.insert(0, '/root/stage5-v3-fast-ready/scripts')
    disk = assert_disk(run, plan['planned_raw_bytes'])
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    if os.stat(DATA_ROOT).st_dev != os.stat('/data/stage5-v3').st_dev:
        raise RuntimeError('Data root is not mounted disk')
    check_health(json.loads(args.health.read_text()), json.loads(args.residue.read_text()))
    validate_workload(plan)
    route = json.loads(args.route_proof.read_text())
    route_age = (datetime.now(timezone.utc) - datetime.fromisoformat(
        route['at_utc'])).total_seconds()
    if (not 0 <= route_age <= 180 or
        route.get('status') not in ('ROUTE_FLOW_PROOF_PASS',
                                  'W2_W3_ADAPTER_DRY_RUN_SCOPE_PASS') or
        route.get('target_path', route.get('source_node', '') + '->' +
                  route.get('destination_node', '')) not in
             ('NETWORK_PATH/k8s-worker2/k8s-worker3', 'k8s-worker2->k8s-worker3')):
        raise RuntimeError('Fresh W2->W3 route and packet proof missing')
    sources = [Path(__file__), Path(__file__).with_name('clean_protocol.py'),
               Path(__file__).with_name('live_safety.py'),
               Path(__file__).with_name('assess_clean_run.py'),
               Path(__file__).with_name('analyze_network_controls.py'),
               Path(__file__).with_name('archive_clean.py'),
               Path(__file__).with_name('coordinate_probes.py'),
               STATE, CONTROLS, MACHINE, PROFILES, PEOPLE, PIPELINE, CPU_SAMPLER,
               args.prereg, args.health, args.residue, args.route_proof]
    locked = {str(path): sha(path) for path in sources}
    run.mkdir(parents=True)
    status = {'status': 'RUNNING', 'id': run_id, 'seed': plan['seed'],
        'started_utc': utc_now(), 'data_disk_guard': disk, 'source_sha256': locked,
        'fault_injected': False, 'formal_case': False}
    processes = {}
    handles = []
    try:
        append_event(run, 'PREFLIGHT', 'PASS', data_disk_uuid=disk['mount']['uuid'])
        snapshot = current_snapshot(locked[str(args.route_proof)])
        if route.get('pod_identities'):
            for key, service in (('checkout_w2', 'checkout'), ('cart_w3', 'cart')):
                if route['pod_identities'][key]['uid'] not in snapshot['pod_uids'][service]:
                    raise RuntimeError('Fresh route proof Pod UID drift')
        if (snapshot['node_placement']['checkout'][0][1] != 'k8s-worker2' or
            snapshot['node_placement']['cart'][0][1] != 'k8s-worker3' or
            len(snapshot['pod_uids']['recommendation']) != 2):
            raise RuntimeError('Target topology changed before source lock')
        save_new(run / 'source-lock.json', snapshot)
        append_event(run, 'SOURCE_LOCK', 'PASS', snapshot_sha256=sha(run / 'source-lock.json'))
        start = datetime.now(timezone.utc) + timedelta(seconds=60)
        end = start + timedelta(seconds=1500)
        schedule = {'run_id': run_id, 'seed': plan['seed'],
            'scheduled_start_utc': start.isoformat(timespec='microseconds'),
            'scheduled_end_utc': end.isoformat(timespec='microseconds'),
            'phase_windows': {phase.lower(): {'start_utc': (start + timedelta(seconds=lo)).isoformat(),
                'end_utc': (start + timedelta(seconds=hi)).isoformat()}
                for phase, (lo, hi) in PHASES.items()},
            'source_sha256': locked, 'source_lock_sha256': sha(run / 'source-lock.json'),
            'collector_config_sha256': snapshot['collector_config_sha256'],
            'pod_uids': snapshot['pod_uids'], 'node_placement': snapshot['node_placement'],
            'trace_source': 'JAEGER_OPENSEARCH_STORAGE', 'fault_injected': False}
        save_new(run / 'schedule.json', schedule)
        (run / 'load_pair').mkdir()
        save_new(run / 'load_pair/schedule.json', schedule)
        commands = {
            'state-machine': ['/usr/bin/python3', '-B', str(STATE), '--machine', str(MACHINE),
                '--profiles', str(PROFILES), '--people', str(PEOPLE), '--profile', 'MEDIUM',
                '--seed', str(plan['seed']), '--duration', '1500', '--start-utc',
                start.isoformat(), '--base-url', BASE_URL, '--output', str(run / 'load_pair/state-machine')],
            'controls': ['/usr/bin/python3', '-B', str(CONTROLS), '--plan', str(args.prereg),
                '--seed', str(plan['seed']), '--duration', '1500', '--start-utc',
                start.isoformat(), '--base-url', BASE_URL, '--output', str(run / 'load_pair/controls')],
            'capture-pipeline': ['/usr/bin/python3', '-B', str(PIPELINE), '--start',
                start.isoformat(), '--output', str(run / 'capture-pipeline')],
            'cpu-safety': ['/usr/bin/python3', '-B', str(CPU_SAMPLER), '--start',
                start.isoformat(), '--duration', '1500', '--output',
                str(run / 'cpu-safety')]}
        for name, command in commands.items():
            out, err = (run / (name + '.stdout')).open('x'), (run / (name + '.stderr')).open('x')
            handles.extend((out, err))
            processes[name] = subprocess.Popen(command, stdout=out, stderr=err)
        next_health_tick = 0
        for phase, (lo, hi) in PHASES.items():
            begun, finished = start + timedelta(seconds=lo), start + timedelta(seconds=hi)
            time.sleep(max(0, begun.timestamp() - time.time()))
            validate_snapshot(snapshot, current_snapshot(locked[str(args.route_proof)]))
            if any(sha(path) != expected for path, expected in locked.items()):
                raise RuntimeError('Registered source hash changed before phase')
            if any(processes[name].poll() is not None for name in
                   ('state-machine', 'controls', 'capture-pipeline')):
                raise RuntimeError('Workload or capture process exited before phase end')
            assert_disk(run, plan['planned_raw_bytes'])
            append_event(run, phase, 'STARTED', begun.isoformat(), finished.isoformat(),
                         current_pod_uids=snapshot['pod_uids'])
            while time.time() < finished.timestamp():
                time.sleep(min(30, max(0, finished.timestamp() - time.time())))
                if any(processes[name].poll() is not None and
                       time.time() < finished.timestamp() - 5 for name in
                       ('state-machine', 'controls', 'capture-pipeline')):
                    raise RuntimeError('Workload or capture process exited during ' + phase)
                assert_disk(run, plan['planned_raw_bytes'])
                if time.time() >= next_health_tick:
                    health_tick = safety_tick()
                    append_event(run, 'SAFETY_TICK', health_tick['status'],
                                 health_source='Master_live_read_only')
                    if any(sha(path) != expected for path, expected in locked.items()):
                        raise RuntimeError('Registered source hash changed during run')
                    validate_snapshot(snapshot, current_snapshot(locked[str(args.route_proof)]))
                    next_health_tick = time.time() + 60
            append_event(run, phase, 'COMPLETED', begun.isoformat(), finished.isoformat())
        codes = {name: child.wait(timeout=370) for name, child in processes.items()}
        status['process_exit_codes'] = codes
        if any(codes[name] for name in ('state-machine', 'controls', 'capture-pipeline')):
            raise RuntimeError('Workload or live capture process failed')
        status['cpu_safety_v2_shadow_status'] = ('PASS' if codes['cpu-safety'] == 0
            else 'FAIL_OR_INCOMPLETE_NOT_OLD_RULE_RESULT')
        manifests = {name: json.loads((run / 'load_pair' / name /
            'result-manifest.json').read_text()) for name in ('state-machine', 'controls')}
        load_status = ('LOAD_PAIR_COMPLETE' if manifests['state-machine']['status'] ==
            'LOAD_PASS' and manifests['controls']['status'] == 'CONTROL_STREAMS_COMPLETE'
            else 'LOAD_PAIR_INCOMPLETE')
        save_new(run / 'load_pair/result.json', {'status': load_status,
            'run_id': run_id, 'manifests': manifests, 'fault_injected': False})
        if load_status != 'LOAD_PAIR_COMPLETE':
            raise RuntimeError('Workload completed with invalid manifest')
        append_event(run, 'TRACE_COMPLETION', 'COMPLETE', trace_source='JAEGER_OPENSEARCH_STORAGE')
        status['status'] = 'RAW_CLEAN_CAPTURE_COMPLETE_AWAITING_QUALITY_AND_ARCHIVE'
        append_event(run, 'QUALITY_EVALUATION', 'PENDING_INDEPENDENT_CHECK')
    except Exception as error:
        status['status'] = 'CLEAN_RUN_FAILED_CLOSED'
        status['error'] = str(error)
        append_event(run, 'FAILED_TERMINAL', 'FAIL', reason=str(error))
    finally:
        for child in processes.values():
            if child.poll() is None:
                child.terminate()
                try: child.wait(timeout=15)
                except subprocess.TimeoutExpired: child.kill()
        for handle in handles:
            handle.close()
        status['completed_utc'] = utc_now()
        save_new(run / 'run-result.json', status)
        print(json.dumps({'status': status['status'], 'id': run_id,
                          'error': status.get('error')}), flush=True)
    if status['status'] != 'RAW_CLEAN_CAPTURE_COMPLETE_AWAITING_QUALITY_AND_ARCHIVE':
        raise SystemExit(1)


if __name__ == '__main__': main()
