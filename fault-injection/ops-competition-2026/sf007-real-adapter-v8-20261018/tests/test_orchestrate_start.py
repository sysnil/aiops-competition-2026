"""Just-in-time registration leaves a real margin before capture startup."""

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import unittest


FILE = Path(__file__).resolve().parents[1] / 'scripts/orchestrate_start.py'
SPEC = importlib.util.spec_from_file_location('sf007_start_orchestrator', FILE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class StartScheduleTest(unittest.TestCase):
    def test_fresh_identity_and_bounded_start_window(self):
        now = datetime.now(timezone.utc).timestamp()
        identity = {'status': 'CANDIDATE_CGROUPS_INSPECTED_NOT_QUALIFIED',
                    'at_utc': datetime.fromtimestamp(now - 10, timezone.utc).isoformat()}
        self.assertAlmostEqual(MODULE.start_age(identity, now), 10, delta=1)
        self.assertAlmostEqual(MODULE.launch_time(now + 300, now), now + 70, delta=1)

    def test_stale_identity_or_delayed_registration_fails_closed(self):
        now = datetime.now(timezone.utc).timestamp()
        identity = {'status': 'CANDIDATE_CGROUPS_INSPECTED_NOT_QUALIFIED',
                    'at_utc': datetime.fromtimestamp(now - 101, timezone.utc).isoformat()}
        with self.assertRaisesRegex(RuntimeError, '100-second'):
            MODULE.start_age(identity, now)
        with self.assertRaisesRegex(RuntimeError, 'fresh capture startup'):
            MODULE.launch_time(now + 190, now)


if __name__ == '__main__':
    unittest.main()
