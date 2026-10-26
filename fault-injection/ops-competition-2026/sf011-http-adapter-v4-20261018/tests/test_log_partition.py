"""Half-open OpenSearch log window checks for the prospective exporter."""

from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from sf004_export import log_partition


class LogPartitionTest(unittest.TestCase):
    def test_in_window(self):
        self.assertTrue(log_partition(1_000_000, 1_000_000, 2_000_000))
        self.assertTrue(log_partition(1_999_999, 1_000_000, 2_000_000))

    def test_query_boundary_neighbors_are_counted_and_excluded(self):
        self.assertFalse(log_partition(797_573, 1_000_000, 2_000_000))
        self.assertFalse(log_partition(2_000_000, 1_000_000, 2_000_000))

    def test_distant_out_of_window_logs_fail(self):
        with self.assertRaises(RuntimeError):
            log_partition(-1, 1_000_000, 2_000_000)
        with self.assertRaises(RuntimeError):
            log_partition(3_000_001, 1_000_000, 2_000_000)


if __name__ == '__main__':
    unittest.main()
