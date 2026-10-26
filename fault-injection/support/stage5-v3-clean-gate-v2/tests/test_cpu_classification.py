import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from analyze_cpu_references import kind, percentile


class CpuClassificationTests(unittest.TestCase):
    def test_business_and_observation_are_distinct(self):
        self.assertEqual(kind({"namespace": "otel-demo",
                               "container": "recommendation"}), "business")
        self.assertEqual(kind({"namespace": "otel-demo",
                               "container": "otel-collector"}), "observability")
        self.assertEqual(kind({"namespace": "kube-system",
                               "container": "coredns"}), "other_container")

    def test_empty_percentile_is_missing_not_zero(self):
        self.assertIsNone(percentile([], .95))


if __name__ == "__main__":
    unittest.main()
