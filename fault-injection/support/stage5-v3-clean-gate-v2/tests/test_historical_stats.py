import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from analyze_historical_windows import longest_run, wilson


class HistoricalStatsTests(unittest.TestCase):
    def test_runs_are_contiguous_not_independent_minute_counts(self):
        self.assertEqual(longest_run([False, True, True, False, True]), 2)

    def test_small_window_uncertainty_remains_wide(self):
        low, high = wilson(2, 2)
        self.assertLess(low, .5)
        self.assertEqual(high, 1)
        self.assertIsNone(wilson(0, 0))


if __name__ == "__main__":
    unittest.main()
