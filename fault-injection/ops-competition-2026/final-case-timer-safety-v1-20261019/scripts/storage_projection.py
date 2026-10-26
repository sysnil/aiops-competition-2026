#!/usr/bin/env python3
"""Project sequential five-case storage without relaxing the five-GB floor."""

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError('Projection output already exists')
    snapshot = json.loads(args.snapshot.read_text())
    plan = json.loads(args.plan.read_text())
    if (snapshot['status'] != 'READ_ONLY_PREFLIGHT_PASS' or
            plan['status'] != 'DRAFT_STORAGE_PLAN_NOT_PRODUCTION_AUTHORIZATION' or
            len(plan['cases']) != 5 or
            plan['absolute_free_floor_bytes'] != 5_000_000_000):
        raise RuntimeError('Storage inputs invalid')
    mounts = snapshot['hosts']
    archive_counts = Counter()
    rows = []
    for index, case in enumerate(plan['cases'], start=1):
        archive_counts[case['archive_worker']] += 1
        projected = {}
        sensitivity = {}
        for host, values in mounts.items():
            projected[host] = {}
            sensitivity[host] = {}
            for path, item in values.items():
                root = path == '/'
                spend = (index * plan['root_runtime_reservation_per_host_per_case_bytes']
                         if root else 0)
                if host == 'master' and path == '/data/stage5-v3':
                    spend += index * plan['master_data_commitment_per_case_bytes']
                if root and host in archive_counts:
                    spend += (archive_counts[host] *
                              plan['worker_root_archive_commitment_per_case_bytes'])
                if host != 'master':
                    spend += plan['one_time_background_reservation_per_host_bytes']
                remaining = item['free_bytes'] - spend
                projected[host][path] = remaining
                if not root and host not in ('master', 'vm1'):
                    sensitivity[host][path] = (
                        remaining - (index - 1) *
                        plan['sensitivity_background_data_per_host_per_case_bytes'])
                else:
                    sensitivity[host][path] = remaining
        floor = plan['absolute_free_floor_bytes']
        rows.append({'case_id': case['case_id'],
                     'archive_worker': case['archive_worker'],
                     'projected_free_bytes': projected,
                     'repeated_background_sensitivity_free_bytes': sensitivity,
                     'base_floor_pass': all(value >= floor for host in projected.values()
                                            for value in host.values()),
                     'sensitivity_floor_pass': all(value >= floor
                         for host in sensitivity.values() for value in host.values())})
    result = {'status': 'SEQUENTIAL_STORAGE_PROJECTION_CONDITIONAL',
              'at_utc': datetime.now(timezone.utc).isoformat(),
              'source_snapshot_sha256': sha(args.snapshot),
              'allocation_plan_sha256': sha(args.plan),
              'rows': rows,
              'all_base_cases_pass': all(row['base_floor_pass'] for row in rows),
              'all_sensitivity_cases_pass': all(row['sensitivity_floor_pass'] for row in rows),
              'not_a_production_permit': True}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': result['status'],
                      'base_pass': result['all_base_cases_pass'],
                      'sensitivity_pass': result['all_sensitivity_cases_pass']}))


if __name__ == '__main__':
    main()
