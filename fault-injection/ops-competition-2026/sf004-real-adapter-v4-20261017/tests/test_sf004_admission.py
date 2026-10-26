import importlib.util
from pathlib import Path
import unittest


FILE = Path(__file__).resolve().parents[1] / 'scripts/sf004_admission.py'
SPEC = importlib.util.spec_from_file_location('sf004_admission', FILE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PublicLatencyQuantile(unittest.TestCase):
    def test_one_abnormal_request_does_not_hide_in_successful_requests(self):
        self.assertEqual(MODULE.p95([10] * 19 + [260]), 10)
        self.assertEqual(MODULE.p95([10] * 18 + [260] * 2), 260)

    def test_no_samples_are_not_turned_into_zero_latency(self):
        self.assertIsNone(MODULE.p95([]))


if __name__ == '__main__':
    unittest.main()
