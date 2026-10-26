#!/usr/bin/env python3
"""Private, fail-closed Pod-memory and HTTP preflight."""

import argparse
import json
import os
import subprocess
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
    if (value.get('run_id') != 'SF-008-ATTEMPT-05' or
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
            matching = [candidate for candidate in candidates if
                        labels.get('pod') == candidate['name'] and
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


def bind_run_environment(plan):
    expected = '/data/stage5-v3/ops-competition-2026/runs/SF-008-ATTEMPT-05'
    if plan.get('id') != 'SF-008-ATTEMPT-05' or plan.get('raw_root') != expected:
        raise RuntimeError('Preflight Run identity or data root mismatch')
    os.environ['SF008_RUN'] = expected


def check_master_competition_units(listing, plan):
    allowed = {plan[key] + '.service' for key in
               ('controller_unit', 'observer_unit', 'capture_controller_unit')}
    running = {line.split(None,1)[0] for line in listing.splitlines() if line.strip()
               and line.split(None,1)[0].startswith(('ops-sf','ops-p04','ops-a0'))}
    unknown = running - allowed
    if unknown:
        raise RuntimeError('Unregistered active competition unit: ' + ','.join(sorted(unknown)))
    return sorted(running)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--manifest-sha', required=True)
    parser.add_argument('--tag', choices=('ARMED', 'POD-PRE', 'POD-RECOVERY',
                                          'REQDELAY-PRE', 'POST'), required=True)
    parser.add_argument('--require-pending', action='append', default=[])
    args = parser.parse_args()
    manifest, plan = load_bundle(args.manifest, args.manifest_sha)
    bind_run_environment(plan)
    competition_file = Path(plan['competition_residue_file'])
    competition = json.loads(competition_file.read_text())
    if (sha(competition_file) != plan['competition_residue_sha256'] or
            competition['status'] != 'COMPETITION_UNIT_RESIDUE_PASS' or
            any(row['active_competition_units'] for row in competition['hosts'].values())):
        raise RuntimeError('Frozen pre-run competition unit residue proof invalid')
    listing = subprocess.check_output(['systemctl','list-units','--state=running',
        '--type=service','--plain','--no-legend'], text=True, timeout=15)
    master_units = check_master_competition_units(listing, plan)
    sys.path.insert(0, '/root/ops-competition-2026/sf008-formal-v5-20261017/scripts')
    from sf008_safety import safety
    from pilot_common import telemetry_state
    run = Path(plan['raw_root'])
    if sha(run / 'schedule.json') != plan['capture_schedule_sha256']:
        raise RuntimeError('Formal capture schedule changed')
    quality = {}
    if args.tag in ('POD-PRE', 'REQDELAY-PRE'):
        count = 1 if args.tag == 'POD-PRE' else 2
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
    frontends = [row for row in plan['targets_and_siblings'] if row['service']=='frontend']
    coverage = (frontend_coverage(run / 'capture/chunk-00',
                {row['uid'] for row in frontends})
                if args.tag == 'POD-PRE' else None)
    resources = (frontend_resource_coverage(run / 'capture/chunk-00', frontends)
                 if args.tag == 'POD-PRE' else None)
    business = (recent_business(run, time.time()) if args.tag in
                ('POD-PRE', 'POD-RECOVERY', 'REQDELAY-PRE') else None)
    if args.tag == 'POD-PRE':
        proof = Path(action['watchdog_proof_file'])
        value = json.loads(proof.read_text())
        if (sha(proof) != action['watchdog_proof_sha256'] or
                value['status'] != 'COMPETITION_WATCHDOG_SELFTEST_PASS' or
                not 0 <= time.time() - stamp(value['completed_utc']) <= 3600):
            raise RuntimeError('Fresh independent Watchdog proof required')
    result = {'status': 'SF008_FORMAL_PREFLIGHT_PASS', 'tag': args.tag,
              'at_utc': datetime.now(timezone.utc).isoformat(),
              'manifest_sha256': args.manifest_sha, 'environment': environment,
              'collector_uid': collector['uid'],
              'five_host_heartbeat_sha256': sha(heartbeat_path),
              'master_competition_units': master_units,
              'timer_inventory': inventory, 'pending': sorted(pending),
              'quality_sha256': quality, 'frontend_trace_coverage': coverage,
              'frontend_resource_coverage': resources,
              'recent_business_requests': business, 'target_proof': targets,
              'no_prediction_or_private_gt_read': True}
    path = Path(plan['control_root']) / ('preflight-' + args.tag + '.json')
    atomic(path, result, True)
    print(json.dumps({'status': result['status'], 'path': str(path)}))


if __name__ == '__main__':
    main()
