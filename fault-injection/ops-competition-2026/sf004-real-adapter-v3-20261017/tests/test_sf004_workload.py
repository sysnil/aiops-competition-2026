import importlib.util
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timedelta, timezone


FILE = Path(__file__).resolve().parents[1] / 'scripts/sf004_workload.py'
SPEC = importlib.util.spec_from_file_location('sf004_workload', FILE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FormalHomeDelay(unittest.TestCase):
    def test_every_business_request_records_dispatch_start_before_completion(self):
        row = MODULE.execute_with_start(lambda: {'time_utc': datetime.now(timezone.utc).isoformat(),
            'trace_id': 'a' * 32, 'success': True})
        self.assertIn('request_started_utc', row)
        self.assertLessEqual(MODULE.timestamp(row['request_started_utc']),
                             MODULE.timestamp(row['time_utc']))

    def test_only_fresh_exact_scope_lease_permits_header(self):
        now = datetime.now(timezone.utc)
        lease = {'run_id': 'SF-004-ATTEMPT-02', 'plan_sha256': 'a' * 64,
                 'permission': 'BOUND_HTTP_REQDELAY', 'safety_pass': True,
                 'issued_utc': now.isoformat(),
                 'expires_utc': (now + timedelta(seconds=4)).isoformat()}
        self.assertTrue(MODULE.lease_valid(lease, 'a' * 64, now.timestamp()+1))
        self.assertFalse(MODULE.lease_valid(lease, 'a' * 64, now.timestamp()+5))
        self.assertFalse(MODULE.lease_valid(lease, 'b' * 64, now.timestamp()+1))

    def test_public_user_agent_is_neutral_and_source_anchor_is_exact(self):
        source = "headers = {'User-Agent': 'stage5-workload-rehearsal/1.0'}"
        self.assertIn("'User-Agent': 'business-workload/1.0'",
                      MODULE.neutralize_user_agent(source))
        with self.assertRaisesRegex(RuntimeError, 'anchor changed'):
            MODULE.neutralize_user_agent(source + source)

    def test_exact_home_window_and_control_exclusion(self):
        self.assertFalse(MODULE.should_delay('http://demo/', 'GET', 899.999, 900, 960))
        self.assertTrue(MODULE.should_delay('http://demo/', 'GET', 900, 900, 960))
        self.assertTrue(MODULE.should_delay('http://demo/', 'GET', 959.999, 900, 960))
        self.assertFalse(MODULE.should_delay('http://demo/', 'GET', 960, 900, 960))
        self.assertFalse(MODULE.should_delay('http://demo/api/cart', 'GET', 920, 900, 960))
        self.assertFalse(MODULE.should_delay('http://demo/', 'POST', 920, 900, 960))

    def test_wrong_case_identity_fails_before_source_access(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, 'Wrong SF-004 run identity'):
                MODULE.run({'id': 'SF-009-ATTEMPT-01', 'formal_case': True,
                            'duration_seconds': 1800, 'profile': 'MEDIUM'},
                           Path(directory) / 'new-output', 'a' * 64)


if __name__ == '__main__':
    unittest.main()
