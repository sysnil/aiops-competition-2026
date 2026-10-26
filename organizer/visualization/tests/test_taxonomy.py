import hashlib
import json
from pathlib import Path
import unittest


TAXONOMY = Path(__file__).resolve().parents[1] / 'anomaly-taxonomy-v1.json'
EXPECTED = (
    'REQUEST_LATENCY_INCREASE', 'REQUEST_ERROR_INCREASE',
    'REQUEST_TIMEOUT_INCREASE', 'CPU_USAGE_INCREASE',
    'MEMORY_USAGE_INCREASE', 'POD_RESTART_INCREASE',
    'LOG_ERROR_BURST', 'TRACE_LATENCY_INCREASE',
    'CALL_EDGE_LATENCY_INCREASE', 'CALL_EDGE_ERROR_INCREASE',
    'OTHER_OBSERVABLE_ANOMALY',
)


class PublishedTaxonomyTests(unittest.TestCase):
    def test_competition_schema_matches_player_manual(self):
        raw = TAXONOMY.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
            'e972da4a2f82407fb30f985463e61d6943ce3fda043ef76846b03be71ca7282a')
        self.assertEqual(tuple(row['id'] for row in json.loads(raw)['types']), EXPECTED)


if __name__ == '__main__':
    unittest.main()
