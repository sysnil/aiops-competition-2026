#!/usr/bin/env python3
"""Bind the qualified Frontend Pod memory mechanism to fresh Pod/PID IDs."""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


SOURCE = Path('/root/ops-competition-2026/plans/COMP-SF01-POD-SHORT.json')
DECISION = Path('/root/ops-competition-2026/qualification/COMP-SF01-POD-SHORT/decision.json')
ADAPTER = Path('/root/ops-competition-2026/scripts/competition_adapter.py')
ADAPTER_SHA = '791481b3e12ce6f64bb77c75acaff7df8000d800081589194ccde5d5ad3f2a92'
ID = 'SF-008-ATTEMPT-04-POD'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build(historical, inspection):
    if historical['id'] != 'COMP-SF01-POD-SHORT' or (
            historical['layer'], historical['duration_seconds'], historical['host']) != (
            'POD', 60, 'k8s-worker2'):
        raise ValueError('Wrong historical Pod mechanism')
    siblings = [row for row in inspection['targets'] if row['service'] == 'frontend']
    rows = [row for row in siblings if row['node'] == 'k8s-worker2']
    if (len(rows) != 1 or len(siblings) != 2 or
            len({row['uid'] for row in siblings}) != 2):
        raise ValueError('Qualified Frontend Pod and sibling required')
    expected = {row['uid']: row for row in historical['targets']}
    if {row['uid'] for row in rows} != set(expected):
        raise ValueError('Pod candidate identity changed')
    targets = []
    for row in rows:
        old = expected[row['uid']]
        if (row['node'] != 'k8s-worker2' or len(row['processes']) != 1 or
            any(row[key] != old[key] for key in ('container_id', 'cgroup')) or
            any(str(row['processes'][0][key]) != str(old[key])
                for key in ('pid', 'start_ticks'))):
            raise ValueError('Pod/container/PID binding changed')
        if row['conservative_32MiB_plus_32MiB_overhead_fits_80pct'] is not True:
            raise ValueError('Pod memory safety headroom changed')
        target = {key: row[key] for key in ('service', 'name', 'uid', 'node',
                   'container_id', 'restart_count', 'cgroup')}
        target.update({key: row['processes'][0][key] for key in ('pid', 'start_ticks')})
        targets.append(target)
    result = dict(historical)
    result.update(id=ID, at_utc=datetime.now(timezone.utc).isoformat(),
                  targets=targets, formal_case=True,
                  fresh_identity_sha256=inspection['identity_sha256'],
                  targets_and_siblings=[{key: row[key] for key in
                      ('service', 'name', 'uid', 'node', 'container_id', 'restart_count')}
                      for row in inspection['targets']],
                  inspection_sha256=inspection['identity_sha256'])
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--inspection', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists() or sha(ADAPTER) != ADAPTER_SHA:
        raise RuntimeError('New output and unchanged qualified adapter required')
    decision = json.loads(DECISION.read_text())
    if decision['qualified'] is not True or decision['blockers']:
        raise RuntimeError('Historical 30-second qualification failed')
    inspection = json.loads(args.inspection.read_text())
    age = (datetime.now(timezone.utc) -
           datetime.fromisoformat(inspection['at_utc'])).total_seconds()
    if inspection['status'] != 'CANDIDATE_CGROUPS_INSPECTED_NOT_QUALIFIED' or not 0 <= age <= 300:
        raise RuntimeError('Fresh process inspection required')
    inspection['identity_sha256'] = sha(args.inspection)
    result = build(json.loads(SOURCE.read_text()), inspection)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    args.output.chmod(0o600)
    print(json.dumps({'status': 'SF008_POD_MEMORY60_REGISTRATION_PREPARED_NO_INJECTION',
                      'output': str(args.output), 'sha256': sha(args.output)}))


if __name__ == '__main__':
    main()
