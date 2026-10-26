from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from pod_precheck import FAMILIES, validate


def fixture():
    uid = 'uid-001'
    target = {'namespace': 'otel-demo', 'name': 'cart-abc', 'uid': uid,
              'node': 'k8s-worker2', 'container_id': 'container-001'}
    observations = [{**target, 'observed_unix_ns': stamp, 'ready': True}
                    for stamp in (0, 600_000_000_000)]
    samples = [{'time_unix_ns': index * 15_000_000_000, 'value': float(index)}
               for index in range(41)]
    return {'schema_version': 1, 'public_only': True,
            'before_window': {'start_unix_ns': 0, 'end_unix_ns': 600_000_000_000},
            'scrape_interval_seconds': 15, 'target_pod': target,
            'candidate_entities': ['POD/otel-demo/cart-abc'], 'rollout_pending': False,
            'pod_observations': observations,
            'metric_series': {family: {'container_id': target['container_id'],
                                       'samples': samples} for family in FAMILIES}}


class PodPrecheckTests(unittest.TestCase):
    def test_stable_pod_passes_before_fault(self):
        result = validate(fixture())
        self.assertEqual(result['status'], 'POD_CANDIDATE_PRECHECK_PASS')
        self.assertEqual(result['during_uid_status'], 'PENDING_POST_FAULT_ADMISSION')

    def test_uid_change_rejects(self):
        snapshot = fixture()
        snapshot['pod_observations'][-1]['uid'] = 'replacement-uid'
        with self.assertRaisesRegex(ValueError, 'identity'):
            validate(snapshot)

    def test_sparse_cgroup_rejects(self):
        snapshot = fixture()
        snapshot['metric_series'][FAMILIES[-1]]['samples'] = snapshot['metric_series'][FAMILIES[-1]]['samples'][:8]
        with self.assertRaisesRegex(ValueError, 'Insufficient'):
            validate(snapshot)

    def test_candidate_absence_rejects(self):
        snapshot = fixture()
        snapshot['candidate_entities'] = []
        with self.assertRaisesRegex(ValueError, 'absent'):
            validate(snapshot)


if __name__ == '__main__':
    unittest.main()
