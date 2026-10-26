#!/usr/bin/env python3
"""Verify one Marker Attempt's cancel cannot stop another real systemd timer."""

import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from marker_controller import ROOT, remote
from run_marker_suite import registration


CONTRACT = ROOT / 'design/marker-isolation-contract.json'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def invoke(value, operation, *args):
    return remote(value, operation, *args)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--batch-token', required=True)
    args = parser.parse_args()
    if args.output.exists() or not args.batch_token.startswith('20261019'):
        raise RuntimeError('Immutable isolation output and unique date token required')
    contract = json.loads(CONTRACT.read_text())
    if contract['status'] != 'PREREGISTERED_HARMLESS_TWO_ATTEMPT_ISOLATION_TEST':
        raise RuntimeError('Isolation contract changed')
    first, _ = registration('isolation_a', sha(CONTRACT), args.batch_token)
    second, _ = registration('isolation_b', sha(CONTRACT), args.batch_token)
    records = []
    try:
        for value in (first, second):
            invoke(value, 'owner', '--planned-utc', value['planned_utc'],
                   '--controller-pid', str(os.getpid()))
            invoke(value, 'arm', '--planned-utc', value['planned_utc'])
        before = {value['run_id']: invoke(value, 'inspect')
                  for value in (first, second)}
        if not all(before[value['run_id']]['units']['.timer']['ActiveState'] == 'active'
                   for value in (first, second)):
            raise RuntimeError('Both systemd timers were not pending')
        invoke(first, 'cancel')
        after_first = invoke(first, 'inspect')
        second_pending = invoke(second, 'inspect')
        if (after_first['units']['.timer']['ActiveState'] == 'active' or
                second_pending['units']['.timer']['ActiveState'] != 'active'):
            raise RuntimeError('Cancel crossed Attempt boundary')
        expiry = datetime.fromisoformat(second['planned_utc']) + timedelta(seconds=3)
        invoke(second, 'authorize', '--expires-utc', expiry.isoformat())
        until = max(datetime.fromisoformat(value['planned_utc']).timestamp()
                    for value in (first, second)) + 3
        while time.time() < until:
            time.sleep(0.1)
        records = [invoke(value, 'inspect') for value in (first, second)]
        if records[0]['fired'] or not records[1]['fired']:
            raise RuntimeError('Wrong Marker execution after targeted cancel')
        status = 'TWO_ATTEMPT_SYSTEMD_TIMER_ISOLATION_PASS'
    except BaseException as exc:
        status = 'TWO_ATTEMPT_SYSTEMD_TIMER_ISOLATION_FAIL'
        error = type(exc).__name__ + ': ' + str(exc)
    finally:
        cleanup = []
        for value in (first, second):
            try:
                cleanup.append(invoke(value, 'cancel'))
            except BaseException as exc:
                cleanup.append({'status': 'CLEANUP_UNVERIFIED', 'error': str(exc)})
        if any(item.get('status') != 'ATTEMPT_TIMER_REVOKED_AND_INACTIVE'
               for item in cleanup):
            status = 'TWO_ATTEMPT_SYSTEMD_TIMER_ISOLATION_FAIL'
    result = {'status': status, 'at_utc': datetime.now(timezone.utc).isoformat(),
              'contract_sha256': sha(CONTRACT),
              'attempts': [first['run_id'], second['run_id']],
              'before': before if 'before' in locals() else None,
              'after_first_cancel': after_first if 'after_first' in locals() else None,
              'other_attempt_still_pending': second_pending if 'second_pending' in locals() else None,
              'after_planned': records, 'cleanup': cleanup,
              'error': error if 'error' in locals() else None,
              'real_faults_injected': 0}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': status,
                      'first_fired': records[0]['fired'] if records else None,
                      'second_fired': records[1]['fired'] if records else None}))
    if status != 'TWO_ATTEMPT_SYSTEMD_TIMER_ISOLATION_PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
