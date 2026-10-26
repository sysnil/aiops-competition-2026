import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import analyze_network_controls as network


class NetworkControlTests(unittest.TestCase):
    def test_uid_and_parent_edge_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / 'load_pair/controls').mkdir(parents=True)
            (run / 'schedule.json').write_text(json.dumps({
                'scheduled_start_utc': '2026-10-13T00:00:00+00:00'}))
            (run / 'source-lock.json').write_text(json.dumps({'node_placement': {
                'payment': [['p1', 'k8s-worker1', '10.0.0.1'],
                            ['p2', 'k8s-worker2', '10.0.0.2']]},
                'pod_uids': {'image-provider': ['i1'], 'cart': ['c1']}}))
            spans = {}
            start_us = 1791849600 * 1_000_000
            controls = {'image': [], 'cart': []}
            for minute in range(25):
                for name, service, uid, parent in (
                    ('image', 'image-provider', 'i1', 'frontend-proxy'),
                    ('cart', 'cart', 'c1', 'frontend'),
                    ('payment', 'payment', 'p1', 'checkout')):
                    trace = name + str(minute)
                    client_id, server_id = 'client', 'server'
                    spans[(trace, client_id)] = {'traceID': trace, 'spanID': client_id,
                        'process': {'serviceName': parent}, 'tag': {'span@kind': 'client'},
                        'duration': 5000}
                    spans[(trace, server_id)] = {'traceID': trace, 'spanID': server_id,
                        'process': {'serviceName': service,
                            'tags': [{'key': 'k8s.pod.uid', 'value': uid}]},
                        'tag': {'span@kind': 'server'},
                        'startTime': start_us + minute * 60_000_000 + 2_000_000,
                        'references': [{'traceID': trace, 'spanID': client_id}]}
                    if name in controls: controls[name].append(json.dumps({'trace_id': trace}))
            for name, stream in (('image', 'image_asset'), ('cart', 'cart_view')):
                (run / 'load_pair/controls' / (stream + '.jsonl')).write_text(
                    '\n'.join(controls[name]))
            with patch.object(network, 'spans_by_phase', return_value=(spans,
                    {'before': 'a', 'during': 'b', 'after': 'c'})):
                result = network.analyze(run)
            self.assertEqual(result['image']['minimum_edge_spans_per_minute'], 1)
            self.assertEqual(result['cart']['minimum_edge_spans_per_minute'], 1)
            self.assertEqual(result['payment_worker1']['minimum_edge_spans_per_minute'], 1)
            self.assertFalse(result['payment_worker1']['per_minute_30_spans_pass'])
            self.assertFalse(result['shared_checkout_source_independence_under_fault_verified'])


if __name__ == '__main__': unittest.main()
