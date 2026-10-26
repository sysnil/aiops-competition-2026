from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from service_node_precheck import node, service


def baseline():
    return {'start_unix_ns': 0, 'end_unix_ns': 600_000_000_000}


class ServiceNodePrecheckTests(unittest.TestCase):
    def test_service_requires_two_stable_replicas(self):
        snapshot = {'schema_version': 1, 'public_only': True, 'target_service': 'recommendation',
                    'candidate_entities': ['SERVICE/recommendation'], 'rollout_pending': False,
                    'before_window': baseline(),
                    'pods': [{'uid': 'a', 'service': 'recommendation', 'ready': True,
                              'stable_through_before': True},
                             {'uid': 'b', 'service': 'recommendation', 'ready': True,
                              'stable_through_before': True}],
                    'pod_metric_sample_counts': {'a': {'cpu_usage': 30}, 'b': {'cpu_usage': 30}}}
        self.assertEqual(service(snapshot)['status'], 'SERVICE_CANDIDATE_PRECHECK_PASS')
        snapshot['pods'].pop()
        with self.assertRaisesRegex(ValueError, 'two distinct'):
            service(snapshot)

    def test_node_requires_multiple_services_and_host_metrics(self):
        snapshot = {'schema_version': 1, 'public_only': True, 'target_node': 'k8s-worker2',
                    'candidate_entities': ['NODE/k8s-worker2'], 'node_ready_before': True,
                    'rollout_pending': False, 'before_window': baseline(),
                    'pods': [{'node': 'k8s-worker2', 'service': 'cart', 'ready': True,
                              'business_service': True},
                             {'node': 'k8s-worker2', 'service': 'currency', 'ready': True,
                              'business_service': True}],
                    'node_metric_sample_counts': {'cpu_usage': 30, 'cpu_time': 30,
                                                  'memory_available': 30}}
        self.assertEqual(node(snapshot)['status'], 'NODE_CANDIDATE_PRECHECK_PASS')
        snapshot['node_metric_sample_counts']['cpu_time'] = 0
        with self.assertRaisesRegex(ValueError, 'metric insufficient'):
            node(snapshot)


if __name__ == '__main__':
    unittest.main()
