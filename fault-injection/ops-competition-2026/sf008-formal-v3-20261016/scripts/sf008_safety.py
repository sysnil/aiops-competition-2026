#!/usr/bin/env python3
"""Reuse the established live safety checks with the authorized 5 GB disk floor."""

import importlib.util
import os
import shutil
import subprocess
from pathlib import Path


SOURCE = Path('/root/ops-competition-2026/scripts/competition_observation.py')
MOUNT = Path('/data/stage5-v3')
EXPECTED_DEVICE = '/dev/sdb1'
EXPECTED_UUID = 'a35051a2-3d09-4b83-bbc0-3bb055e8a4f3'
FLOOR = 5_000_000_000


def storage():
    fields = {key: subprocess.check_output(
        ['findmnt', '-n', '-o', key, '-T', str(MOUNT)], text=True, timeout=10).strip()
        for key in ('SOURCE', 'UUID', 'OPTIONS')}
    if (fields['SOURCE'] != EXPECTED_DEVICE or fields['UUID'] != EXPECTED_UUID or
            'rw' not in fields['OPTIONS'].split(',') or
            os.stat(MOUNT).st_dev == os.stat('/').st_dev):
        raise RuntimeError('Approved Master data mount changed')
    root_free, data_free = shutil.disk_usage('/').free, shutil.disk_usage(MOUNT).free
    if min(root_free, data_free) < FLOOR:
        raise RuntimeError('Authorized five GB absolute floor crossed')
    return {'root_free_bytes': root_free, 'data_free_bytes': data_free,
            'mount_device': fields['SOURCE'], 'uuid': fields['UUID'],
            'absolute_floor_bytes': FLOOR,
            'old_static_peak_reservation_used': False}


def safety(expected):
    spec = importlib.util.spec_from_file_location('sf008_unchanged_safety_checks', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.storage = storage
    result = module.safety(expected)
    if result['status'] != 'LIVE_ENVIRONMENT_PASS':
        raise RuntimeError('Live cluster health gate failed')
    return result
