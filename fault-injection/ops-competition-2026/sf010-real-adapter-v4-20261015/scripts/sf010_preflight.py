#!/usr/bin/env python3
"""Private, fail-closed SF-010 preflight. This module cannot inject a fault."""

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


FLOOR = 5_000_000_000
MOUNTS = {name: ('root_free_bytes',) if name == 'vm1' else
          ('root_free_bytes', 'data_free_bytes')
          for name in ('master', 'worker1', 'worker2', 'worker3', 'vm1')}
VERIFY_TARGETS = '''import importlib.util,json,sys
p='/root/ops-competition-2026/scripts/competition_adapter.py'
s=importlib.util.spec_from_file_location('adapter',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
for target in json.loads(sys.argv[1]):m.verify_target(target)
print(json.dumps({'verified_targets':len(json.loads(sys.argv[1]))}))'''


def wait_for_quality(paths, deadline):
    while any(not path.is_file() for path in paths) and time.time() < deadline:
        time.sleep(.25)
    if time.time() >= deadline or any(not path.is_file() or
            json.loads(path.read_text())['status'] != 'CHUNK_QUALITY_PASS'
            for path in paths):
        raise RuntimeError('Mature capture quality missing or failed before fault')
    return {str(path): sha(path) for path in paths}


def check_five_host_heartbeat(value, plan_sha, checked_at, source_sha=None):
    if (value.get('run_id') != 'SF-010-ATTEMPT-04' or
            value.get('plan_sha256') != plan_sha or
            value.get('status') != 'DYNAMIC_STORAGE_PASS' or
            (source_sha is not None and value.get('source_sha256') != source_sha) or
            not 0 <= checked_at - stamp(value['at_utc']) <= 30):
        raise RuntimeError('Five-host disk heartbeat missing, stale or foreign')
    for host, disks in MOUNTS.items():
        for disk in disks:
            free, spend = value['snapshot'][host][disk], value['forecast'][host][disk]
            if (not isinstance(free, int) or not isinstance(spend, int) or
                    free < 0 or spend < 0 or free - spend < FLOOR):
                raise RuntimeError('Five-host disk forecast breaches the floor')


def recommendation_coverage(chunk, uids):
    traces = defaultdict(set)
    pages = chunk / 'trace-final/during/trace-storage-pages'
    for page in sorted(pages.glob('*.json')):
        for hit in json.loads(page.read_text())['hits']['hits']:
            span = hit['_source']
            if span['process']['serviceName'] != 'recommendation':
                continue
            tags = {item['key']: item.get('value') for item in span['process'].get('tags') or []}
            uid = tags.get('k8s.pod.uid')
            if uid in uids:
                traces[uid].add(span['traceID'])
    counts = {uid: len(traces[uid]) for uid in uids}
    if len(uids) != 2 or min(counts.values()) < 30:
        raise RuntimeError('Both Recommendation Pods need 30 distinct public traces')
    return counts


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
           (('home', 60), ('image_asset', 90), ('cart_view', 180))):
        raise RuntimeError('Normal business/control flow below registered minimum')
    return counts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--manifest-sha', required=True)
    parser.add_argument('--tag', choices=('ARMED', 'SERVICE-PRE', 'SERVICE-RECOVERY',
                                          'REQDELAY-PRE', 'POST'), required=True)
    parser.add_argument('--require-pending', action='append', default=[])
    args = parser.parse_args()
    manifest, plan = load_bundle(args.manifest, args.manifest_sha)
    sys.path.insert(0, '/root/ops-competition-2026/sf010-formal-20261015-v7/scripts')
    from sf010_safety import safety
    from pilot_common import telemetry_state
    run = Path(plan['raw_root'])
    if sha(run / 'schedule.json') != plan['capture_schedule_sha256']:
        raise RuntimeError('Formal capture schedule changed')
    quality = {}
    if args.tag in ('SERVICE-PRE', 'REQDELAY-PRE'):
        count = 1 if args.tag == 'SERVICE-PRE' else 2
        deadline = (stamp(manifest['actions'][0]['planned_utc']) if count == 1
                    else stamp(plan['start_utc']) + 960) - 8
        paths = [run / f'capture/chunk-{index:02d}/quality.json'
                 for index in range(count)]
        quality = wait_for_quality(paths, deadline)
    environment = safety(plan['targets_and_siblings'])
    collector = telemetry_state(plan['collector_uid'])
    health = json.loads(Path(plan['health_file']).read_text())
    if collector['refused'] != health['collector_refused']:
        raise RuntimeError('Collector refused count changed')
    heartbeat_path = Path(plan['five_host_heartbeat_path'])
    check_five_host_heartbeat(json.loads(heartbeat_path.read_text()),
                              manifest['plan_sha256'], time.time(),
                              plan['five_host_monitor_sha256'])
    timers = json.loads(remote('w3', ['python3', '-c', TIMER_CODE]))
    inventory = verify_timer_inventory(manifest, plan, args.manifest,
                                       args.manifest_sha, 'k8s-worker3',
                                       list(timers), timers)
    known = {action['action_id'] for action in manifest['actions']}
    if not set(args.require_pending) <= known:
        raise RuntimeError('Unknown pending fault action')
    pending = {action['unit'] + '.timer' for action in manifest['actions']
               if action['action_id'] in args.require_pending}
    if not pending <= set(timers):
        raise RuntimeError('Registered Worker Timer absent')
    action = manifest['actions'][0]
    targets = json.loads(remote('w3', ['python3', '-c', VERIFY_TARGETS,
                                       json.dumps(action['targets'])]))
    if targets['verified_targets'] != 2:
        raise RuntimeError('Wrong exact-PID Service target scope')
    coverage = (recommendation_coverage(run / 'capture/chunk-00',
                {target['uid'] for target in action['targets']})
                if args.tag == 'SERVICE-PRE' else None)
    business = (recent_business(run, time.time()) if args.tag in
                ('SERVICE-PRE', 'SERVICE-RECOVERY', 'REQDELAY-PRE') else None)
    if args.tag == 'SERVICE-PRE':
        proof = Path(action['watchdog_proof_file'])
        value = json.loads(proof.read_text())
        if (sha(proof) != action['watchdog_proof_sha256'] or
                value['status'] != 'COMPETITION_WATCHDOG_SELFTEST_PASS' or
                not 0 <= time.time() - stamp(value['completed_utc']) <= 3600):
            raise RuntimeError('Fresh independent Watchdog proof required')
    result = {'status': 'SF010_FORMAL_PREFLIGHT_PASS', 'tag': args.tag,
              'at_utc': datetime.now(timezone.utc).isoformat(),
              'manifest_sha256': args.manifest_sha, 'environment': environment,
              'collector_uid': collector['uid'],
              'five_host_heartbeat_sha256': sha(heartbeat_path),
              'timer_inventory': inventory, 'pending': sorted(pending),
              'quality_sha256': quality, 'recommendation_trace_coverage': coverage,
              'recent_business_requests': business, 'target_proof': targets,
              'no_prediction_or_private_gt_read': True}
    path = Path(plan['control_root']) / ('preflight-' + args.tag + '.json')
    atomic(path, result, True)
    print(json.dumps({'status': result['status'], 'path': str(path)}))


if __name__ == '__main__':
    main()
