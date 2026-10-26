import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'algorithms'))

from openrca_xl_runner import normalize, public_summary, run


def fixture():
    return {'schema_version': 1, 'public_only': True,
            'public_case_id': 'f8191aa3-818e-48d6-b3a3-251894a7b310', 'variant': 'B',
            'candidates': [{'entity': 'SERVICE/ad', 'layer': 'SERVICE'},
                           {'entity': 'POD/demo/ad-a', 'layer': 'POD'},
                           {'entity': 'NODE/worker-a', 'layer': 'NODE'},
                           {'entity': 'NETWORK_PATH/worker-a/worker-b',
                            'layer': 'NETWORK_PATH'}],
            'relations': [{'source': 'SERVICE/ad', 'type': 'HAS_INSTANCE',
                           'target': 'POD/demo/ad-a'}],
            'signals': {'SERVICE/ad': {'metrics': [
                {'family': 'metric.cpu', 'observed': True, 'abnormal': True}]}}}


class StubHandler(BaseHTTPRequestHandler):
    received = []

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        StubHandler.received.append(request)
        public = json.loads(request['messages'][1]['content'])
        entity = next(item for item in public['candidates'] if item['entity'] == 'SERVICE/ad')
        answer = {'ranking': [{'entity': 'SERVICE/ad',
                               'evidence_ids': [entity['evidence'][0]['id']],
                               'reason': 'Observed public CPU signal'}]}
        payload = {'model': 'synthetic-transport-stub',
                   'choices': [{'message': {'content': json.dumps(answer)}}]}
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_):
        pass


class OpenRCAXLRunnerTests(unittest.TestCase):
    def test_blind_http_protocol_smoke(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), StubHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = run(fixture(), f'http://127.0.0.1:{server.server_port}/chat/completions',
                         'synthetic-transport-stub')
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        self.assertEqual(result['root_entity'], 'SERVICE/ad')
        self.assertEqual(result['ranking'][0]['raw_score'], None)
        sent = json.dumps(StubHandler.received[-1])
        self.assertNotIn('evaluator_gt', sent)
        self.assertNotIn('root_entity', sent)
        self.assertNotIn('temperature', StubHandler.received[-1])

    def test_missing_real_endpoint_stays_blocked(self):
        result = run(fixture(), '', '')
        self.assertEqual(result['status'], 'BLOCKED_PREREQUISITE')
        self.assertEqual(result['ranking'], [])

    def test_private_field_rejected_before_endpoint(self):
        bundle = fixture()
        bundle['evaluator_gt'] = {'root_layer': 'SERVICE'}
        with self.assertRaisesRegex(ValueError, 'Private key'):
            public_summary(bundle)

    def test_all_four_typed_layers_and_route_proof_normalize(self):
        bundle = fixture()
        path = 'NETWORK_PATH/worker-a/worker-b'
        bundle['network_context'] = {
            'network_path_entity': path,
            'service_edges': [{'source_service': 'ad', 'destination_service': 'cart'}],
            'route_evidence': [{'id': 'public-flow-ad-cart',
                                'service_edge': 'ad->cart',
                                'route_decision': path,
                                'proof_sha256': 'a' * 64}],
            'normal_observations': {'path_latency_ms': [1.0] * 20}}
        summary, catalog, candidates = public_summary(bundle)
        self.assertEqual(summary['network_context']['network_path_entity'], path)
        self.assertIn('public-flow-ad-cart', catalog)
        for entity, layer in candidates.items():
            evidence = ['public-flow-ad-cart'] if entity == path else []
            result = normalize({'ranking': [{'entity': entity,
                                             'evidence_ids': evidence,
                                             'reason': 'Public evidence'}]},
                               catalog, candidates, bundle['public_case_id'], 'B', {})
            self.assertEqual(result['root_layer'], layer)


if __name__ == '__main__':
    unittest.main()
