#!/usr/bin/env python3
"""GT-free, read-only gates for the SF-011 HTTP-only fault stage."""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from handshake import atomic
from run_registry import load_bundle, sha, stamp


FLOOR = 5_000_000_000
MOUNTS = {name: ('root_free_bytes',) if name == 'vm1' else
          ('root_free_bytes', 'data_free_bytes')
          for name in ('master', 'worker1', 'worker2', 'worker3', 'vm1')}
REAL_HTTP_PROOF = Path('/data/stage5-v3/ops-competition-2026/runs/SF-010-ATTEMPT-07/control/fault-controller-terminal.json')


def check_five_host_heartbeat(value, plan_sha, checked_at, source_sha=None):
    if (value.get('run_id') != 'SF-011-ATTEMPT-04' or
            value.get('plan_sha256') != plan_sha or
            value.get('status') != 'DYNAMIC_STORAGE_PASS' or
            (source_sha is not None and value.get('source_sha256') != source_sha) or
            not 0 <= checked_at - stamp(value['at_utc']) <= 30):
        raise RuntimeError('Five-host disk heartbeat missing, stale or foreign')
    for host, disks in MOUNTS.items():
        for disk in disks:
            free, spend = value['snapshot'][host][disk], value['forecast'][host][disk]
            if (not isinstance(free, int) or not isinstance(spend, int) or
                    free - spend < FLOOR):
                raise RuntimeError('Five-host disk forecast breaches the floor')
    timers = value.get('active_competition_timers')
    if (not isinstance(timers, dict) or set(timers) != set(MOUNTS) or
            any(timers[host] for host in MOUNTS)):
        raise RuntimeError('Five-host active competition Timer inventory incomplete')


def wait_for_quality(path, deadline):
    while not path.is_file() and time.time() < deadline:
        time.sleep(.25)
    if (time.time() >= deadline or not path.is_file() or
            json.loads(path.read_text())['status'] != 'CHUNK_QUALITY_PASS'):
        raise RuntimeError('Mature public capture quality missing before HTTP fault')
    return sha(path)


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
            started = row.get('request_started_utc')
            operation = row.get('operation')
            if (started and operation in counts and row.get('success') is True and
                    checked_at - 120 <= stamp(started) < checked_at):
                counts[operation] += 1
    if any(counts[name] < minimum for name, minimum in
           (('home', 60), ('image_asset', 90), ('cart_view', 90))):
        raise RuntimeError('Registered business and controls below minimum')
    return counts


def marked_home_events(path, start, end):
    paths = path if isinstance(path, (tuple, list)) else [path]
    if any(not item.is_file() for item in paths):
        raise RuntimeError('Home dispatch evidence missing')
    rows = []
    for item in paths:
        data = item.read_bytes()
        lines = data.splitlines()
        if data and not data.endswith(b'\n'):
            lines = lines[:-1]
        rows.extend(json.loads(line) for line in lines)
    marked = [row for row in rows if row['delay_header_applied']]
    if any(not start <= stamp(row['at_utc']) < end or
           row['requested_delay_ms'] != 250 for row in marked):
        raise RuntimeError('HTTP delay marker escaped frozen window or strength')
    if any(start <= stamp(row['at_utc']) < end and
           not row['delay_header_applied'] for row in rows):
        raise RuntimeError('In-window ordinary home request lacked fault effect')
    return {'home_requests': len(rows), 'delayed_requests': len(marked),
            'source_paths': [str(item) for item in paths]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--manifest-sha', required=True)
    parser.add_argument('--tag', choices=('ARMED', 'HTTP-PRE', 'HTTP-RECOVERY', 'POST'),
                        required=True)
    args = parser.parse_args()
    manifest, plan = load_bundle(args.manifest, args.manifest_sha)
    sys.path.insert(0, '/root/ops-competition-2026/sf011-http-formal-v4-20261018/scripts')
    from sf004_safety import safety
    from pilot_common import telemetry_state
    run = Path(plan['raw_root'])
    if sha(run / 'schedule.json') != plan['capture_schedule_sha256']:
        raise RuntimeError('Formal capture schedule changed')
    if sha(REAL_HTTP_PROOF) != plan['real_http_qualification_sha256']:
        raise RuntimeError('Validated ordinary home route proof changed')
    quality = None
    if args.tag == 'HTTP-PRE':
        deadline = stamp(plan['start_utc']) + 900 - 8
        quality = wait_for_quality(run / 'capture/chunk-00/quality.json', deadline)
    environment = safety(plan['targets_and_siblings'])
    collector = telemetry_state(plan['collector_uid'])
    health = json.loads(Path(plan['health_file']).read_text())
    if collector['refused'] != health['collector_refused']:
        raise RuntimeError('Collector refusal increased')
    heartbeat_path = Path(plan['five_host_heartbeat_path'])
    check_five_host_heartbeat(json.loads(heartbeat_path.read_text()),
                              manifest['plan_sha256'], time.time(),
                              plan['five_host_monitor_sha256'])
    business = (recent_business(run, time.time()) if args.tag in
                ('HTTP-PRE', 'HTTP-RECOVERY') else None)
    http = plan['request_delay']
    effect = (marked_home_events([run / 'state-machine/home-header-events.jsonl',
              run / 'controls/home-header-events.jsonl'],
              stamp(plan['start_utc']) + http['offset_seconds'],
              stamp(plan['start_utc']) + http['offset_seconds'] + 60)
              if args.tag in ('HTTP-RECOVERY', 'POST') else None)
    if effect and effect['delayed_requests'] < 60:
        raise RuntimeError('Too few real delayed home business requests')
    result = {'status': 'SF011_FORMAL_PREFLIGHT_PASS', 'tag': args.tag,
              'at_utc': datetime.now(timezone.utc).isoformat(),
              'manifest_sha256': args.manifest_sha, 'environment': environment,
              'collector_uid': collector['uid'],
              'five_host_heartbeat_sha256': sha(heartbeat_path),
              'quality_sha256': quality, 'recent_business_requests': business,
              'http_effect': effect,
              'active_competition_timers': json.loads(heartbeat_path.read_text())['active_competition_timers'],
              'no_prediction_or_private_gt_read': True}
    path = Path(plan['control_root']) / ('preflight-' + args.tag + '.json')
    atomic(path, result, True)
    print(json.dumps({'status': result['status'], 'path': str(path)}))


if __name__ == '__main__':
    main()
