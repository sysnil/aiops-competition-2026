from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'algorithms'))

from microrca_xl import birch_anomaly, correlation, run


class MicroRcaXLTests(unittest.TestCase):
    def test_author_birch_parameters_detect_shift(self):
        self.assertFalse(birch_anomaly([5.0] * 20, [5.0] * 20))
        self.assertTrue(birch_anomaly([5.0] * 20, [100.0] * 20))

    def test_correlation_requires_aligned_nonconstant_evidence(self):
        self.assertAlmostEqual(correlation([1, 2, 3, 4], [2, 4, 6, 8]), 1.0)
        self.assertIsNone(correlation([1, 1, 1, 1], [2, 4, 6, 8]))
        self.assertIsNone(correlation([1, 2, 3], [1, 2, 3]))

    def test_four_layer_graph_runs_without_gt(self):
        edge = 'frontend->catalog'
        values = [5.0] * 20 + [100.0] * 20
        path = 'NETWORK_PATH/worker-a/worker-b'
        bundle = {
            'schema_version': 1, 'public_only': True,
            'public_case_id': 'df955a8a-0b4e-44e2-8262-fb8903156124', 'variant': 'B',
            'candidates': [
                {'entity': 'SERVICE/frontend', 'layer': 'SERVICE'},
                {'entity': 'SERVICE/catalog', 'layer': 'SERVICE'},
                {'entity': 'POD/demo/catalog-a', 'layer': 'POD'},
                {'entity': 'NODE/worker-b', 'layer': 'NODE'},
                {'entity': path, 'layer': 'NETWORK_PATH'},
            ],
            'relations': [
                {'type': 'CALLS', 'source': 'SERVICE/frontend', 'target': 'SERVICE/catalog'},
                {'type': 'HAS_INSTANCE', 'source': 'SERVICE/catalog', 'target': 'POD/demo/catalog-a'},
                {'type': 'RUNS_ON', 'source': 'POD/demo/catalog-a', 'target': 'NODE/worker-b'},
                {'type': 'TRAVERSES', 'source': 'SERVICE_EDGE/frontend/catalog', 'target': path},
            ],
            'time_bins_unix_ns': list(range(40)),
            'invocation_series': {edge: {'before': values[:20], 'during': values[20:]}},
            'resource_series': {entity: {'latency_or_resource': values} for entity in (
                'SERVICE/catalog', 'POD/demo/catalog-a', 'NODE/worker-b', path)},
            'clean_null_pagerank_maxima': {layer: [0.01] * 8 for layer in (
                'SERVICE', 'POD', 'NODE', 'NETWORK_PATH')},
        }
        result = run(bundle)
        self.assertEqual(result['status'], 'PASS')
        self.assertTrue(result['edge_anomalies'][edge])
        self.assertEqual(len(result['ranking']), 5)
        self.assertEqual({item['level'] for item in result['ranking']},
                         {'SERVICE', 'POD', 'NODE', 'NETWORK_PATH'})


if __name__ == '__main__':
    unittest.main()
