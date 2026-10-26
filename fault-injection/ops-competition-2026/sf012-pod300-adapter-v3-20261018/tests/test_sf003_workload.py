import importlib.util
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timezone


FILE = Path(__file__).resolve().parents[1] / 'scripts/sf003_workload.py'
SPEC = importlib.util.spec_from_file_location('sf003_workload', FILE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FormalPodBusinessLoad(unittest.TestCase):
    def test_every_business_request_records_dispatch_start_before_completion(self):
        row = MODULE.execute_with_start(lambda: {'time_utc': datetime.now(timezone.utc).isoformat(),
            'trace_id': 'a' * 32, 'success': True})
        self.assertIn('request_started_utc', row)
        self.assertLessEqual(MODULE.stamp(row['request_started_utc']),
                             MODULE.stamp(row['time_utc']))

    def test_public_user_agent_is_neutral_and_source_anchor_is_exact(self):
        source = "headers = {'User-Agent': 'stage5-workload-rehearsal/1.0'}"
        self.assertIn("'User-Agent': 'business-workload/1.0'",
                      MODULE.neutralize_user_agent(source))
        with self.assertRaisesRegex(RuntimeError, 'source changed'):
            MODULE.neutralize_user_agent(source + source)

    def test_wrong_case_identity_fails_before_source_access(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, 'Wrong SF-012 run'):
                MODULE.run({'id': 'SF-009-ATTEMPT-06', 'formal_case': True,
                            'duration_seconds': 1800, 'profile': 'MEDIUM'},
                           Path(directory) / 'new-output', 'a' * 64)

    def test_http_fault_capability_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, 'fault capability'):
                MODULE.run({'id': 'SF-012-ATTEMPT-03', 'formal_case': True,
                            'duration_seconds': 1800, 'profile': 'MEDIUM',
                            'request_delay': {'delay_ms': 250}},
                           Path(directory) / 'new-output', 'a' * 64)


if __name__ == '__main__':
    unittest.main()
