import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from network_path_precheck import validate
from build_candidates import build


def fixture():
    path = 'NETWORK_PATH/worker3/worker2'
    pods = [
        {'uid': 'front', 'service': 'frontend', 'node': 'worker3'},
        {'uid': 'checkout', 'service': 'checkout', 'node': 'worker3'},
        {'uid': 'catalog', 'service': 'product-catalog', 'node': 'worker2'},
        {'uid': 'currency', 'service': 'currency', 'node': 'worker2'},
        {'uid': 'recommendation', 'service': 'recommendation', 'node': 'worker2'},
    ]
    specs = [
        ('frontend', 'product-catalog', 'front', 'catalog', 'worker3', 'worker2', 'TRAVERSES', path),
        ('checkout', 'currency', 'checkout', 'currency', 'worker3', 'worker2', 'TRAVERSES', path),
        ('recommendation', 'product-catalog', 'recommendation', 'catalog', 'worker2', 'worker2',
         'CONTROL_NONTRAVERSING', 'NETWORK_PATH/worker2/worker2'),
    ]
    edges = []
    evidence = []
    for index, (source, destination, source_uid, destination_uid, source_node,
                destination_node, relation, route) in enumerate(specs):
        edge_id = source + '->' + destination
        proof_id = 'proof-' + str(index)
        edges.append({'source_service': source, 'destination_service': destination,
                      'source_pod_uid': source_uid, 'destination_pod_uid': destination_uid,
                      'source_node': source_node, 'destination_node': destination_node,
                      'network_path_entity': route, 'relation': relation,
                      'route_or_flow_evidence_id': proof_id})
        proof = {'kind': 'pre_fault_flow_table', 'service_edge': edge_id,
                 'source_pod_uid': source_uid, 'destination_pod_uid': destination_uid,
                 'route_decision': route, 'flow_or_route_observation': f'flow-{index}'}
        digest = hashlib.sha256(json.dumps(proof, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        evidence.append({'id': proof_id, 'service_edge': edge_id, 'source_pod_uid': source_uid,
                         'destination_pod_uid': destination_uid, 'route_decision': route,
                         'captured_before_fault': True, 'proof_sha256': digest,
                         'public_proof': proof})
    return {'schema_version': 1, 'public_only': True, 'network_path_entity': path,
            'candidate_entities': [path],
            'nodes': [{'name': 'worker3', 'ready': True}, {'name': 'worker2', 'ready': True}],
            'pods': pods, 'service_edges': edges, 'route_evidence': evidence,
            'normal_observations': {'path_latency_ms': [5.0] * 20}}


class NetworkPathPrecheckTests(unittest.TestCase):
    def test_verified_edges_and_control_pass(self):
        result = validate(fixture())
        self.assertEqual(result['status'], 'NETWORK_PATH_PRECHECK_PASS')
        self.assertEqual(len(result['traversing_edges']), 2)

    def test_node_pair_without_route_proof_rejects(self):
        snapshot = fixture()
        snapshot['route_evidence'][0]['captured_before_fault'] = False
        with self.assertRaisesRegex(ValueError, 'route proof'):
            validate(snapshot)

    def test_control_on_same_path_rejects(self):
        snapshot = fixture()
        snapshot['service_edges'][-1]['network_path_entity'] = snapshot['network_path_entity']
        with self.assertRaisesRegex(ValueError, 'Control edge traverses'):
            validate(snapshot)

    def test_private_network_term_rejects(self):
        snapshot = fixture()
        snapshot['route_evidence'][0]['note'] = 'netem 100ms'
        with self.assertRaisesRegex(ValueError, 'Injection-specific'):
            validate(snapshot)

    def test_verified_path_enters_public_entity_graph(self):
        network = fixture()
        pods = [{**item, 'namespace': 'otel-demo', 'name': item['service'] + '-a'}
                for item in network['pods']]
        metadata = {'schema_version': 1, 'public_only': True, 'case_id': 'F04',
                    'services': [item['service'] for item in pods],
                    'nodes': network['nodes'], 'pods': pods,
                    'service_calls': [{'source': edge['source_service'],
                                       'destination': edge['destination_service']}
                                      for edge in network['service_edges']],
                    'pod_communications': [{'source_pod_uid': edge['source_pod_uid'],
                                            'destination_pod_uid': edge['destination_pod_uid']}
                                           for edge in network['service_edges']]}
        graph = build(metadata, network)
        self.assertEqual(graph['candidate_counts']['NETWORK_PATH'], 1)
        self.assertEqual(sum(item['type'] == 'TRAVERSES' for item in graph['relations']), 2)


if __name__ == '__main__':
    unittest.main()
