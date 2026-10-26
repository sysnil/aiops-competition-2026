#!/usr/bin/env python3
"""Private, fail-closed SF-012 preflight. This module cannot inject a fault."""

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from handshake import atomic
from master_transport import remote
from run_registry import load_bundle, sha, stamp, verify_timer_inventory
from timer_inventory import CODE as TIMER_CODE

STORAGE_V2 = Path(__file__).resolve().parents[2] / 'low-stable-v4-20261017/storage-v2'
sys.path.insert(0, str(STORAGE_V2))
from validate_heartbeat import validate as validate_storage_heartbeat


FLOOR = 5_000_000_000
GATE_SOURCE = STORAGE_V2 / 'storage_forecast_gate.py'
GATE_PLAN_PATH = '/root/ops-competition-2026/low-stable-v4-20261017/storage-v2/storage_forecast_gate.py'
BUDGET_SOURCE = STORAGE_V2 / 'case-budgets/sf012-att02.json'
MOUNTS = {name: ('root_free_bytes',) if name == 'vm1' else
          ('root_free_bytes', 'data_free_bytes')
          for name in ('master', 'worker1', 'worker2', 'worker3', 'vm1')}
VERIFY_TARGETS = '''import importlib.util,json,sys
p='/root/ops-competition-2026/scripts/competition_adapter.py'
s=importlib.util.spec_from_file_location('adapter',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
for target in json.loads(sys.argv[1]):m.verify_target(target)
print(json.dumps({'verified_targets':len(json.loads(sys.argv[1]))}))'''
VERIFY_MEMORY = '''import json,pathlib,sys
t=json.loads(sys.argv[1]);p=pathlib.Path(t['cgroup']);current=int((p/'memory.current').read_text());limit=int((p/'memory.max').read_text())
assert t['uid'].replace('-','_') in str(p) and current+64*1024**2<=limit*.8
events=dict(line.split() for line in (p/'memory.events').read_text().splitlines())
assert int(events['oom'])==0 and int(events['oom_kill'])==0
print(json.dumps({'current_bytes':current,'limit_bytes':limit,'events':events,'conservative_headroom_pass':True}))'''


def wait_for_quality(paths, deadline):
    while any(not path.is_file() for path in paths) and time.time() < deadline:
        time.sleep(.25)
    if time.time() >= deadline or any(not path.is_file() or
            json.loads(path.read_text())['status'] != 'CHUNK_QUALITY_PASS'
            for path in paths):
        raise RuntimeError('Mature capture quality missing or failed before fault')
    return {str(path): sha(path) for path in paths}


def check_five_host_heartbeat(value, plan_sha, checked_at, source_sha=None,
                              gate_sha=None, budget_sha=None):
    expected_budget = budget_sha or sha(BUDGET_SOURCE)
    budget = json.loads(BUDGET_SOURCE.read_text())
    if (expected_budget != sha(BUDGET_SOURCE) or
            value.get('case_budget_sha256') != expected_budget or
            value.get('archive_target') != 'worker1' or
            any(value.get('forecast', {}).get(host, {}).get('data_free_bytes', -1) < amount
                for host, amount in budget['data_reservation_bytes'].items())):
        raise RuntimeError('Five-host case-specific reserve or archive target changed')
    return validate_storage_heartbeat(
        value, 'SF-012-ATTEMPT-02', plan_sha, checked_at,
        source_sha or sha(Path(__file__).with_name('five_host_heartbeat.py')),
        gate_sha or sha(GATE_SOURCE))


def frontend_coverage(chunk, uids):
    traces = defaultdict(set)
    pages = chunk / 'trace-final/during/trace-storage-pages'
    for page in sorted(pages.glob('*.json')):
        for hit in json.loads(page.read_text())['hits']['hits']:
            span = hit['_source']
            attributes = {item['key']: item.get('value') for item in span.get('tags') or []}
            if (span['process']['serviceName'] != 'frontend' or
                    span.get('tag', {}).get('span@kind') != 'server' or
                    attributes.get('http.user_agent') not in
                    ('business-workload/1.0', 'business-control/1.0')):
                continue
            tags = {item['key']: item.get('value') for item in span['process'].get('tags') or []}
            uid = tags.get('k8s.pod.uid')
            if uid in uids:
                traces[uid].add(span['traceID'])
    counts = {uid: len(traces[uid]) for uid in uids}
    if len(uids) != 2 or min(counts.values()) < 30:
        raise RuntimeError('Both Frontend Pods need 30 distinct public traces')
    return counts


def frontend_resource_coverage(chunk, candidates):
    result = {}
    for metric in ('memory', 'cpu'):
        rows = json.loads((chunk / 'captures/during/metrics' / (metric + '.json')).read_text())['data']['result']
        counts = {}
        for row in rows:
            labels = row['metric']
            if labels.get('container') != 'frontend':
                continue
            matching = [candidate for candidate in candidates if labels.get('pod') == candidate['name'] and
                        candidate['uid'].replace('-', '_') in labels.get('id', '')]
            if len(matching) == 1:
                counts[matching[0]['uid']] = len(row['values'])
        if set(counts) != {row['uid'] for row in candidates} or min(counts.values()) < 10:
            raise RuntimeError('Both Frontend Pods need ten independent ' + metric + ' samples')
        result[metric] = counts
    return result


def recent_business(run, checked_at):
    counts = {'home': 0, 'image_asset': 0, 'cart_view': 0}
    for relative in ('state-machine/request-events-live.jsonl',
                     'controls/control-events-live.jsonl'):
        path = run / relative
        if not path.is_file():
            raise RuntimeError('Ordinary business journal missing')
        data = path.read_bytes()
        lines = data.splitlines()
        if data and not data.endswith(b'\n'):
            lines = lines[:-1]
        for line in lines:
            row = json.loads(line)
            began = row.get('request_started_utc') or row.get('time_utc')
            operation = row.get('operation')
            if (began and operation in counts and row.get('success') is True and
                    checked_at - 120 <= stamp(began) < checked_at):
                counts[operation] += 1
    if any(counts[key] < minimum for key, minimum in
           (('home', 60), ('image_asset', 90), ('cart_view', 90))):
        raise RuntimeError('Normal business/control flow below registered minimum')
    return counts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--manifest-sha', required=True)
    parser.add_argument('--tag', choices=('ARMED', 'POD-PRE', 'POD-RECOVERY', 'POST'), required=True)
    parser.add_argument('--require-pending', action='append', default=[])
    args = parser.parse_args()
    manifest, plan = load_bundle(args.manifest, args.manifest_sha)
    sys.path.insert(0, '/root/ops-competition-2026/sf012-pod300-formal-v2-20261018/scripts')
    from sf003_safety import safety
    from pilot_common import telemetry_state
    run = Path(plan['raw_root'])
    if sha(run / 'schedule.json') != plan['capture_schedule_sha256']:
        raise RuntimeError('Formal capture schedule changed')
    quality = {}
    if args.tag == 'POD-PRE':
        deadline = stamp(manifest['actions'][0]['planned_utc']) - 8
        paths = [run / 'capture/chunk-00/quality.json']
        quality = wait_for_quality(paths, deadline)
    environment = safety(plan['targets_and_siblings'])
    collector = telemetry_state(plan['collector_uid'])
    health = json.loads(Path(plan['health_file']).read_text())
    if collector['refused'] != health['collector_refused']:
        raise RuntimeError('Collector refused count changed')
    heartbeat_path = Path(plan['five_host_heartbeat_path'])
    check_five_host_heartbeat(json.loads(heartbeat_path.read_text()),
                              manifest['plan_sha256'], time.time(),
                              plan['five_host_monitor_sha256'],
                              plan['source_sha256'][GATE_PLAN_PATH],
                              plan['case_budget_contract_sha256'])
    timers = json.loads(remote('w2', ['python3', '-c', TIMER_CODE]))
    inventory = verify_timer_inventory(manifest, plan, args.manifest,
                                       args.manifest_sha, 'k8s-worker2',
                                       list(timers), timers)
    known = {action['action_id'] for action in manifest['actions']}
    if not set(args.require_pending) <= known:
        raise RuntimeError('Unknown pending fault action')
    pending = {action['unit'] + '.timer' for action in manifest['actions']
               if action['action_id'] in args.require_pending}
    if not pending <= set(timers):
        raise RuntimeError('Registered Worker Timer absent')
    action = manifest['actions'][0]
    targets = json.loads(remote('w2', ['python3', '-c', VERIFY_TARGETS,
                                       json.dumps(action['targets'])]))
    if targets['verified_targets'] != 1:
        raise RuntimeError('Wrong exact-PID Pod target scope')
    candidates = [row for row in plan['targets_and_siblings'] if row['service'] == 'frontend']
    coverage = (frontend_coverage(run / 'capture/chunk-00',
                {candidate['uid'] for candidate in candidates})
                if args.tag == 'POD-PRE' else None)
    resources = (frontend_resource_coverage(run / 'capture/chunk-00', candidates)
                 if args.tag == 'POD-PRE' else None)
    memory = (json.loads(remote('w2', ['python3', '-c', VERIFY_MEMORY,
              json.dumps(action['targets'][0])])) if args.tag == 'POD-PRE' else None)
    business = (recent_business(run, time.time()) if args.tag in
                ('POD-PRE', 'POD-RECOVERY') else None)
    if args.tag == 'POD-PRE':
        proof = Path(action['watchdog_proof_file'])
        value = json.loads(proof.read_text())
        if (sha(proof) != action['watchdog_proof_sha256'] or
                value['status'] != 'COMPETITION_WATCHDOG_SELFTEST_PASS' or
                not 0 <= time.time() - stamp(value['completed_utc']) <= 3600):
            raise RuntimeError('Fresh independent Watchdog proof required')
    result = {'status': 'SF012_FORMAL_PREFLIGHT_PASS', 'tag': args.tag,
              'at_utc': datetime.now(timezone.utc).isoformat(),
              'manifest_sha256': args.manifest_sha, 'environment': environment,
              'collector_uid': collector['uid'],
              'five_host_heartbeat_sha256': sha(heartbeat_path),
              'timer_inventory': inventory, 'pending': sorted(pending),
              'quality_sha256': quality, 'frontend_trace_coverage': coverage,
              'frontend_resource_coverage': resources, 'target_memory_headroom': memory,
              'recent_business_requests': business, 'target_proof': targets,
              'no_prediction_or_private_gt_read': True}
    path = Path(plan['control_root']) / ('preflight-' + args.tag + '.json')
    atomic(path, result, True)
    print(json.dumps({'status': result['status'], 'path': str(path)}))


if __name__ == '__main__':
    main()
