import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from convert_live import (load_network_probe, safe_public, same_calibration,
                          target_pod_coverage)
from pod_precheck import FAMILIES


class LiveConverterTests(unittest.TestCase):
    def test_frozen_calibration_tolerates_only_floating_roundoff(self):
        current = {'clean_null_pagerank_maxima': {'SERVICE': [0.1] * 8}}
        archived = {'clean_null_pagerank_maxima': {'SERVICE': [0.1 + 1e-12] * 8}}
        self.assertTrue(same_calibration(current, archived))
        archived['clean_null_pagerank_maxima']['SERVICE'][0] = 0.11
        self.assertFalse(same_calibration(current, archived))

    def test_probe_samples_bind_to_correct_path_and_windows(self):
        step = 15_000_000_000
        captures = {'before': {'window': {'start_unix_ns': 0, 'end_unix_ns': 40 * step}},
                    'during': {'window': {'start_unix_ns': 40 * step,
                                          'end_unix_ns': 60 * step}}}
        path = {'network_path_entity': 'NETWORK_PATH/worker3/worker2',
                'service_edges': [{'relation': 'TRAVERSES',
                                   'destination_pod_uid': 'uid-b'}],
                'pods': [{'uid': 'uid-b', 'pod_ip': '10.0.0.2'}]}
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'probe.jsonl'
            with source.open('w') as stream:
                for index in range(60):
                    stream.write(json.dumps({'schema_version': 1,
                                             'source': 'live_node_path_probe',
                                             'source_node': 'worker3',
                                             'destination_node': 'worker2',
                                             'destination_ip': '10.0.0.2',
                                             'sample_index': index,
                                             'scheduled_unix_ns': index * step,
                                             'observed_unix_ns': index * step,
                                             'sent': 5, 'received': 5,
                                             'rtt_ms': [1.0] * 5}) + '\n')
            result = load_network_probe(source, captures, path)
            self.assertEqual(len(result['phases']['before']), 40)
            self.assertEqual(len(result['phases']['during']), 20)
            with self.assertRaisesRegex(ValueError, 'route map'):
                load_network_probe(source, captures, None)

    def test_public_scanner_rejects_private_case_label(self):
        with self.assertRaisesRegex(ValueError, 'Private token'):
            safe_public({'message': 'F04 route'})

    def test_target_pod_needs_five_stable_before_during_series(self):
        pod = {'namespace': 'demo', 'name': 'ad-a', 'uid': 'uid-a',
               'node': 'worker2', 'container_ids': {'ad': 'containerd://id-a'}}
        rows = {phase: [{'entity': 'POD/demo/ad-a', 'family': family,
                         'time_unix_ns': index, 'series_id': family}
                        for family in FAMILIES for index in range(count)]
                for phase, count in (('before', 20), ('during', 10))}
        result = {'metadata': {'pods': [pod]}, 'during_metadata': {'pods': [pod]},
                  'metrics': rows}
        coverage = target_pod_coverage(result, 'ad-a')
        self.assertEqual(len(coverage['sample_counts']['during']), 5)
        result['metrics']['during'].pop()
        with self.assertRaisesRegex(ValueError, 'lacks stable'):
            target_pod_coverage(result, 'ad-a')


if __name__ == '__main__':
    unittest.main()
