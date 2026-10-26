import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from evaluate_historical_controls import longest_run, percentile


class ControlCandidateTests(unittest.TestCase):
    def test_short_severe_spike_cannot_be_silenced(self):
        ratios = [1.0] * 15
        ratios[4] = 1.69
        self.assertGreater(max(ratios), 1.50)
        self.assertEqual(longest_run(value > 1.25 for value in ratios), 1)

    def test_percentile_uses_request_samples(self):
        self.assertEqual(percentile([1, 2, 3, 100], .95), 3)
        self.assertIsNone(percentile([], .95))


if __name__ == "__main__":
    unittest.main()
