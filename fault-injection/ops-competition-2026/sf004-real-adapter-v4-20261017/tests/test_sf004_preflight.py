import importlib.util
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location('sf004_preflight',
                                              SCRIPTS / 'sf004_preflight.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FormalFaultPreflight(unittest.TestCase):
    def heartbeat(self):
        at = datetime.now(timezone.utc).isoformat()
        return {'run_id': 'SF-004-ATTEMPT-03', 'plan_sha256': 'a' * 64,
                'status': 'DYNAMIC_STORAGE_PASS', 'at_utc': at,
                'active_competition_timers': {host: [] for host in MODULE.MOUNTS},
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
                MODULE.wait_for_quality(Path(directory) / 'quality.json', 0)

    def test_unregistered_worker_timer_blocks_http_fault(self):
        value = self.heartbeat()
        value['active_competition_timers']['worker2'] = ['ops-sf003-old.timer']
        with self.assertRaisesRegex(RuntimeError, 'Timer inventory'):
            MODULE.check_five_host_heartbeat(value, 'a' * 64,
                datetime.now(timezone.utc).timestamp())


if __name__ == '__main__':
    unittest.main()
