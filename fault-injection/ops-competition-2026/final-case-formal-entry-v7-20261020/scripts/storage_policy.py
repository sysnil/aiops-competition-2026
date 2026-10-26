"""Single DRAFT archive allocation and decimal 5 GB dynamic floor."""

import shutil
from pathlib import Path


FLOOR = 5_000_000_000
ARCHIVE_RESERVE = 7_350_324_113
BACKGROUND_RESERVE = 2_000_000_000
ALLOCATION = {
    'SF-013': ('k8s-worker1', '/'),
    'SF-014': ('k8s-worker2', '/'),
    'SF-015': ('k8s-worker3', '/'),
    'SF-016': ('k8s-worker1', '/'),
    'SF-017': ('k8s-worker2', '/'),
}
ROOTS = {'k8s-master': ('/', '/data/stage5-v3'),
         'k8s-worker1': ('/', '/data'),
         'k8s-worker2': ('/', '/data'),
         'k8s-worker3': ('/', '/data'),
         'vm1': ('/',)}


def check(case_id, free_bytes, *, reserve=ARCHIVE_RESERVE):
    if case_id not in ALLOCATION:
        raise ValueError('STORAGE_UNKNOWN_CASE')
    expected = {(host, mount) for host, mounts in ROOTS.items() for mount in mounts}
    if set(free_bytes) != expected:
        raise ValueError('STORAGE_FIVE_HOST_SNAPSHOT_INCOMPLETE')
    worker, mount = ALLOCATION[case_id]
    needs = {key: FLOOR for key in expected}
    needs[('k8s-master', '/data/stage5-v3')] += reserve
    needs[(worker, mount)] += reserve
    for host in ('k8s-worker1', 'k8s-worker2', 'k8s-worker3'):
        needs[(host, '/data')] += BACKGROUND_RESERVE
    shortfalls = {f'{host}:{path}': needs[(host, path)] - free_bytes[(host, path)]
                  for host, path in expected
                  if free_bytes[(host, path)] < needs[(host, path)]}
    return {'status': 'STORAGE_PASS' if not shortfalls else 'STORAGE_BLOCKED',
            'case_id': case_id, 'archive_worker': worker,
            'archive_mount': mount, 'floor_bytes': FLOOR,
            'reserve_bytes': reserve, 'shortfalls': shortfalls}


def local_free(path):
    return shutil.disk_usage(Path(path)).free
