import importlib.util
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location('sf008_preflight',
                                              SCRIPTS / 'sf008_preflight.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FormalFaultPreflight(unittest.TestCase):
    def test_run_identity_is_bound_before_common_module_import(self):
        root = '/data/stage5-v3/ops-competition-2026/runs/SF-008-ATTEMPT-05'
        with patch.dict(os.environ, {}, clear=True):
            MODULE.bind_run_environment({'id': 'SF-008-ATTEMPT-05', 'raw_root': root})
            self.assertEqual(os.environ['SF008_RUN'], root)
        with self.assertRaisesRegex(RuntimeError, 'Run identity'):
            MODULE.bind_run_environment({'id': 'SF-008-ATTEMPT-01', 'raw_root': root})

    def test_old_observer_is_rejected_during_formal_preflight(self):
        plan = {'controller_unit': 'ops-sf008-formal-controller05-20261017',
                'observer_unit': 'ops-sf008-formal-observer05-20261017',
                'capture_controller_unit': 'ops-sf008-formal-capture05-20261017'}
        active = '\n'.join(name+' loaded active running' for name in (
            'ops-sf008-formal-controller05-20261017.service',
            'ops-sf008-formal-observer05-20261017.service'))
        self.assertEqual(len(MODULE.check_master_competition_units(active, plan)), 2)
        with self.assertRaisesRegex(RuntimeError, 'Unregistered active competition unit'):
            MODULE.check_master_competition_units(
                active+'\nops-sf008-formal-observer03-20261016.service loaded active running', plan)

    def heartbeat(self):
        at = datetime.now(timezone.utc).isoformat()
        return {'run_id': 'SF-008-ATTEMPT-05', 'plan_sha256': 'a' * 64,
                'status': 'DYNAMIC_STORAGE_PASS', 'at_utc': at,
                'snapshot': {host: {disk: 15_000_000_000 for disk in disks}
                    for host, disks in MODULE.MOUNTS.items()},
                'forecast': {host: {disk: 7_000_000_000 for disk in disks}
                    for host, disks in MODULE.MOUNTS.items()}}

    def test_fresh_all_mounts_pass(self):
        MODULE.check_five_host_heartbeat(self.heartbeat(), 'a' * 64,
                                         datetime.now(timezone.utc).timestamp())

    def test_stale_or_low_space_fails_closed(self):
        value = self.heartbeat()
        value['at_utc'] = '2020-01-01T00:00:00+00:00'
        with self.assertRaisesRegex(RuntimeError, 'stale'):
            MODULE.check_five_host_heartbeat(value, 'a' * 64,
                                             datetime.now(timezone.utc).timestamp())
        value = self.heartbeat()
        value['snapshot']['worker3']['data_free_bytes'] = 11_999_999_999
        with self.assertRaisesRegex(RuntimeError, 'floor'):
            MODULE.check_five_host_heartbeat(value, 'a' * 64,
                                             datetime.now(timezone.utc).timestamp())

    def test_unregistered_monitor_source_is_rejected(self):
        value = self.heartbeat()
        value['source_sha256'] = 'a' * 64
        with self.assertRaisesRegex(RuntimeError, 'stale or foreign'):
            MODULE.check_five_host_heartbeat(value, 'a' * 64,
                datetime.now(timezone.utc).timestamp(), 'b' * 64)

    def test_missing_mature_chunk_fails_before_fault(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, 'quality missing'):
                MODULE.wait_for_quality([Path(directory) / 'quality.json'], 0)


if __name__ == '__main__':
    unittest.main()
