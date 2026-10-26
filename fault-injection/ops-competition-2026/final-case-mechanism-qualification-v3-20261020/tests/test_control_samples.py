import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ops = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ops / 'scripts'))
sys.path.insert(0, str(ops / 'final-case-unified-execution-v2-20261019/scripts'))
sys.path.insert(0, str(ops / 'final-case-formal-entry-v5-20261019/scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from assess_qualification import minimum_control_samples


class ControlSampleContractTests(unittest.TestCase):
    def window(self, seconds):
        start = datetime(2026, 10, 20, tzinfo=timezone.utc)
        return {'start_utc': start.isoformat(),
                'end_utc': (start + timedelta(seconds=seconds)).isoformat()}

    def test_pre_registered_ninety_percent_rate(self):
        for seconds, required in ((180, 162), (240, 216), (300, 270)):
            with self.subTest(seconds=seconds):
                self.assertEqual(minimum_control_samples(self.window(seconds)),
                                 required)

    def test_unregistered_duration_fails_closed(self):
        with self.assertRaises(ValueError):
            minimum_control_samples(self.window(239))


if __name__ == '__main__':
    unittest.main()
