from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from signal_rules import error_log_rate, latency_p95, network_drop_fraction, resource_metric


class SignalRuleTests(unittest.TestCase):
    def test_resource_shift_and_missing_samples(self):
        self.assertTrue(resource_metric([10.0] * 20, [20.0] * 10))
        self.assertFalse(resource_metric([10.0] * 20, [10.1] * 10))
        self.assertIsNone(resource_metric([10.0] * 19, [20.0] * 10))

    def test_trace_latency_needs_traffic(self):
        self.assertTrue(latency_p95([5.0] * 30, [100.0] * 30))
        self.assertIsNone(latency_p95([5.0] * 29, [100.0] * 30))

    def test_log_and_network_rates(self):
        self.assertTrue(error_log_rate(0, 10, 600, 300))
        self.assertFalse(error_log_rate(0, 1, 600, 300))
        self.assertTrue(network_drop_fraction(0, 1000, 30, 1000))
        self.assertIsNone(network_drop_fraction(0, 99, 30, 1000))


if __name__ == '__main__':
    unittest.main()
