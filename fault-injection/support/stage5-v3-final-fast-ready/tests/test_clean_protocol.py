import json
from pathlib import Path
import tempfile
import unittest

from clean_protocol import (DATA_ROOT, UUID, next_stage, save_new, sha,
    validate_archive, validate_capture, validate_disk, validate_plan,
    validate_snapshot, validate_trace)


def plan():
    return {'status': 'PREREGISTERED_NO_FAULT_CLEAN', 'fault_injected': False,
        'formal_case': False, 'duration_seconds': 1500,
        'phases_seconds': {'before': 600, 'during': 300, 'after': 600},
        'target_path': 'NETWORK_PATH/k8s-worker2/k8s-worker3',
        'trace_source': 'JAEGER_OPENSEARCH_STORAGE', 'seed': 864101,
        'id': 'S5V3FFR-CLEAN-20261013-S1', 'planned_raw_bytes': 5 * 1024 ** 3,
        'data_root': str(DATA_ROOT), 'workload': {'sessions_per_second': 1.4,
        'weights': {'browse': 20, 'shopping': 30, 'checkout': 50},
        'image_per_second': 1, 'cart_per_second': 2}}


def disk():
    return {'source': '/dev/sdb1', 'uuid': UUID, 'fstype': 'ext4',
        'rw': True, 'writable': True, 'same_device_as_root': False}


class ProtocolTests(unittest.TestCase):
    def test_pre_registered_plan(self):
        self.assertEqual(validate_plan(plan()), plan()['id'])
        for key, value in [('seed', 864999), ('fault_injected', True),
                           ('target_path', 'NETWORK_PATH/k8s-worker3/k8s-worker2')]:
            broken = plan(); broken[key] = value
            with self.assertRaises(ValueError): validate_plan(broken)

    def test_disk_mount_missing_wrong_uuid_readonly_space_inode_root_and_path(self):
        target = DATA_ROOT / 'runs' / 'next'
        validate_disk(disk(), target, 47_000_000_000, 100 * 1024 ** 3,
                      200_000, 5 * 1024 ** 3)
        variants = [('source', '/dev/sda1'), ('uuid', 'wrong'), ('rw', False),
                    ('writable', False), ('same_device_as_root', True)]
        for key, value in variants:
            changed = disk(); changed[key] = value
            with self.assertRaises(RuntimeError, msg=key):
                validate_disk(changed, target, 47_000_000_000,
                              100 * 1024 ** 3, 200_000, 5 * 1024 ** 3)
        for root, data, inodes, path in (
            (45_000_000_000, 100 * 1024 ** 3, 200_000, target),
            (47_000_000_000, 65 * 1024 ** 3, 200_000, target),
            (47_000_000_000, 100 * 1024 ** 3, 0, target),
            (47_000_000_000, 100 * 1024 ** 3, 200_000, Path('/root/runs/x'))):
            with self.assertRaises(RuntimeError):
                validate_disk(disk(), path, root, data, inodes, 5 * 1024 ** 3)

    def test_source_lock_uid_endpoint_path_collector(self):
        row = {'pod_uids': {'cart': ['uid']}, 'endpoint_slices_sha256': 'a',
               'node_placement': {'cart': 'k8s-worker3'}, 'route_proof_sha256': 'b',
               'live_route_sha256': 'route',
               'collector_config_sha256': 'c',
               'network_path': 'NETWORK_PATH/k8s-worker2/k8s-worker3'}
        validate_snapshot(row, row.copy())
        for key in ('pod_uids', 'endpoint_slices_sha256', 'node_placement',
                    'route_proof_sha256', 'live_route_sha256',
                    'collector_config_sha256'):
            other = row.copy(); other[key] = 'changed'
            with self.assertRaises(RuntimeError): validate_snapshot(row, other)

    def test_capture_collector_failure_and_trace_timing(self):
        row = {'phase': 'during', 'trace_source': 'JAEGER_OPENSEARCH_STORAGE',
               'window': {'start_utc': 'a', 'end_utc': 'b'}, 'sha256': {'x': 'hash'},
               'trace_records': 3, 'log_records': 4}
        validate_capture('during', row, 'a', 'b')
        with self.assertRaises(RuntimeError):
            validate_capture('during', {**row, 'trace_records': 0}, 'a', 'b')
        first = {'phase': 'during', 'query_end_utc': '2026-10-13T00:00:00+00:00'}
        scoring = {'phase': 'during', 'status': 'FINAL_SNAPSHOT_FROZEN',
                   'completed_utc': '2026-10-13T00:02:30+00:00',
                   'query_duration_seconds': 30}
        audit = {'phase': 'during', 'status': 'LATE_AUDIT_FROZEN_UNCLASSIFIED',
                 'completed_utc': '2026-10-13T00:04:30+00:00',
                 'query_duration_seconds': 30}
        validate_trace('during', first, first, scoring, audit)
        with self.assertRaises(RuntimeError):
            validate_trace('during', first, first,
                {**scoring, 'completed_utc': '2026-10-13T00:03:10+00:00'}, audit)

    def test_archive_sha_restore_and_output_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / 'source'; worker = root / 'worker'
            for folder in (source, worker):
                (folder / 'capture-pipeline').mkdir(parents=True)
                (folder / 'schedule.json').write_text('{"ok": true}')
                (folder / 'capture-pipeline/pipeline-result.json').write_text('{"ok": true}')
            self.assertEqual(validate_archive(source, [worker])['file_count'], 2)
            (worker / 'schedule.json').write_text('{"changed": true}')
            with self.assertRaises(RuntimeError): validate_archive(source, [worker])
            with self.assertRaises(ValueError): save_new(source / 'schedule.json', {})

    def test_terminal_lifecycle_and_final_health_failure(self):
        current = None
        for stage in ('PREFLIGHT', 'SOURCE_LOCK', 'BEFORE', 'DURING', 'AFTER',
                      'TRACE_COMPLETION', 'QUALITY_EVALUATION', 'ARCHIVE', 'FINAL_HEALTH'):
            current = next_stage(current, stage)
        self.assertEqual(current, 'FINAL_HEALTH')
        self.assertEqual(next_stage('ARCHIVE', 'FINAL_HEALTH', failed=True),
                         'FAILED_TERMINAL')
        with self.assertRaises(ValueError): next_stage('BEFORE', 'ARCHIVE')


if __name__ == '__main__': unittest.main()
