import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from startup_timing import registration_identity_age, startup_budget


class IdentityStartupBudget(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)

    def iso(self, delta):
        return (self.now + timedelta(seconds=delta)).isoformat()

    def test_att01_age_is_rejected_without_relaxing_frozen_limit(self):
        with self.assertRaisesRegex(RuntimeError, 'freshness margin'):
            startup_budget(self.iso(-180.2), self.iso(190), self.now.timestamp())

    def test_last_refresh_keeps_margin_after_capture_handshake(self):
        self.assertLessEqual(registration_identity_age(self.iso(-20),
                             self.now.timestamp()), 30)
        future = self.now + timedelta(seconds=100)
        budget = startup_budget(self.iso(-20), self.iso(300), future.timestamp())
        self.assertEqual(budget['identity_age_seconds'], 120)
        self.assertEqual(budget['identity_margin_seconds'], 60)

    def test_registration_requires_last_moment_refresh(self):
        with self.assertRaisesRegex(RuntimeError, 'immediately'):
            registration_identity_age(self.iso(-61), self.now.timestamp())

    def test_startup_time_window_and_age_boundaries(self):
        with self.assertRaisesRegex(RuntimeError, 'startup window'):
            startup_budget(self.iso(-20), self.iso(241), self.now.timestamp())
        with self.assertRaisesRegex(RuntimeError, 'freshness margin'):
            startup_budget(self.iso(-151), self.iso(180), self.now.timestamp())


if __name__ == '__main__':
    unittest.main()
