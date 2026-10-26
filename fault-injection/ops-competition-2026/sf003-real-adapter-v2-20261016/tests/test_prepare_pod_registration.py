import copy
import importlib.util
from pathlib import Path
import unittest


FILE = Path(__file__).resolve().parents[1] / 'scripts/prepare_pod_registration.py'
SPEC = importlib.util.spec_from_file_location('prepare_sf003_pod', FILE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FreshPodBinding(unittest.TestCase):
    def test_authoritative_runtime_plan_path_is_explicit(self):
        self.assertEqual(MODULE.SOURCE.name, 'COMP-SF01-POD-SHORT.json')

    def setUp(self):
        target = {'service': 'frontend', 'name': 'frontend-w2', 'uid': 'uid-w2',
                  'node': 'k8s-worker2', 'container_id': 'containerd://abc',
                  'restart_count': 0, 'cgroup': '/sys/fs/cgroup/pod_uid-w2/abc',
                  'processes': [{'pid': 42, 'start_ticks': '100'}],
                  'conservative_32MiB_plus_32MiB_overhead_fits_80pct': True}
        sibling = {'service': 'frontend', 'name': 'frontend-w1', 'uid': 'uid-w1',
                   'node': 'k8s-worker1', 'container_id': 'containerd://def',
                   'restart_count': 0}
        recommendations = [{'service': 'recommendation', 'name': f'rec-{n}',
                            'uid': f'rec-{n}', 'node': 'k8s-worker3',
                            'container_id': f'containerd://rec-{n}', 'restart_count': 0}
                           for n in (1, 2)]
        self.inspection = {'targets': [target, sibling, *recommendations],
                           'identity_sha256': 'a' * 64}
        self.historical = {'id': 'COMP-SF01-POD-SHORT', 'layer': 'POD',
                           'host': 'k8s-worker2', 'duration_seconds': 60,
                           'allocation_bytes': 32 * 1024**2,
                           'targets': [{**{k: target[k] for k in
                               ('uid', 'container_id', 'cgroup')},
                               'pid': 42, 'start_ticks': '100'}]}

    def test_current_qualified_targets_bind(self):
        registration = MODULE.build(self.historical, self.inspection)
        self.assertEqual(registration['id'], MODULE.ID)
        self.assertEqual(len(registration['targets']), 1)
        self.assertEqual(registration['duration_seconds'], 60)

    def test_process_replacement_fails_closed(self):
        inspection = copy.deepcopy(self.inspection)
        target = next(row for row in inspection['targets'] if row['service'] == 'frontend' and row['node'] == 'k8s-worker2')
        target['processes'][0]['start_ticks'] = 'DIFFERENT'
        with self.assertRaisesRegex(ValueError, 'binding changed'):
            MODULE.build(self.historical, inspection)

    def test_pod_uid_replacement_fails_closed(self):
        inspection = copy.deepcopy(self.inspection)
        target = next(row for row in inspection['targets'] if row['service'] == 'frontend' and row['node'] == 'k8s-worker2')
        target['uid'] = 'new-pod'
        with self.assertRaisesRegex(ValueError, 'candidate identity changed'):
            MODULE.build(self.historical, inspection)

    def test_memory_headroom_rejected(self):
        inspection = copy.deepcopy(self.inspection)
        inspection['targets'][0]['conservative_32MiB_plus_32MiB_overhead_fits_80pct'] = False
        with self.assertRaisesRegex(ValueError, 'memory safety headroom'):
            MODULE.build(self.historical, inspection)


if __name__ == '__main__':
    unittest.main()
