import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from merge_network_probe import merge


class MergeNetworkProbeTests(unittest.TestCase):
    def test_preserves_gap_and_rejects_overlapping_time(self):
        with tempfile.TemporaryDirectory() as temp:
            before, during = (Path(temp) / name for name in ('before.jsonl', 'during.jsonl'))
            before.write_text(''.join(json.dumps({
                'sample_index': index, 'source_node': 'worker3',
                'destination_node': 'worker2', 'destination_ip': '10.0.0.2', 'sent': 5,
                'scheduled_unix_ns': index * 15_000_000_000}) + '\n'
                for index in range(60)))
            during.write_text(''.join(json.dumps({
                'sample_index': index, 'source_node': 'worker3',
                'destination_node': 'worker2', 'destination_ip': '10.0.0.2', 'sent': 5,
                'scheduled_unix_ns': (index + 40) * 15_000_000_000 + 120_000_000_000}) + '\n'
                for index in range(40, 60)))
            self.assertEqual(len(merge(before, during)), 60)
            rows = [json.loads(line) for line in during.read_text().splitlines()]
            rows[0]['scheduled_unix_ns'] = 0
            during.write_text(''.join(json.dumps(row) + '\n' for row in rows))
            with self.assertRaisesRegex(ValueError, 'overlap'):
                merge(before, during)


if __name__ == '__main__':
    unittest.main()
