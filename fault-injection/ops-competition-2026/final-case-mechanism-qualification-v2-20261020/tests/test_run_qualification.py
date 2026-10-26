import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/run_qualification.py'
sys.modules['safety_monitor'] = SimpleNamespace(sample=lambda *_: None)
spec = importlib.util.spec_from_file_location('run_qualification', SCRIPT)
qualification = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = qualification
spec.loader.exec_module(qualification)


class QualificationContractTests(unittest.TestCase):
    def test_windows_are_adjacent_and_fault_duration_is_exact(self):
        start = 1792454400
        values = qualification.windows(start, 300)
        self.assertEqual(qualification.stamp(values['before']['start_utc']), start)
        self.assertEqual(qualification.stamp(values['before']['end_utc']), start + 180)
        self.assertEqual(qualification.stamp(values['during']['end_utc']), start + 480)
        self.assertEqual(qualification.stamp(values['after']['end_utc']), start + 660)

    def test_effect_is_never_dispatched_twice_after_completion(self):
        self.assertFalse(qualification.should_dispatch(False, False, 100, 100))
        self.assertTrue(qualification.should_dispatch(True, False, 100, 100))
        self.assertFalse(qualification.should_dispatch(True, True, 410, 100))

    def test_collector_restart_or_refusal_cannot_pass(self):
        before = {'series': {'spans': {'instance-a': 0},
                             'logs': {'instance-a': 0},
                             'metrics': {'instance-a': 0}}}
        after = {'series': {'spans': {'instance-a': 1},
                            'logs': {'instance-a': 0},
                            'metrics': {'instance-a': 0}}}
        self.assertEqual(qualification.refused_delta(before, after), 1)
        after['series']['spans'] = {'instance-b': 0}
        with self.assertRaisesRegex(RuntimeError, 'INSTANCE_CHANGED'):
            qualification.refused_delta(before, after)

    def test_pod_and_service_target_scope(self):
        targets = [
            {'node': 'k8s-worker1', 'service': 'frontend', 'uid': 'a',
             'name': 'frontend-a', 'container_id': 'containerd://a',
             'restart_count': 0},
            {'node': 'k8s-worker2', 'service': 'frontend', 'uid': 'b',
             'name': 'frontend-b', 'container_id': 'containerd://b',
             'restart_count': 0},
        ]
        with patch.object(qualification, 'sha', return_value='a' * 64):
            value = qualification.registration(
                'QUAL-SF-013-POD300-V5-ABCDEFGH', 'SF-013', targets)
        self.assertEqual([row['uid'] for row in value['targets']], ['b'])
        with self.assertRaisesRegex(RuntimeError, 'TARGET_SCOPE_CHANGED'):
            with patch.object(qualification, 'sha', return_value='a' * 64):
                qualification.registration(
                    'QUAL-SF-014-SERVICE300-V5-ABCDEFGH', 'SF-014', targets)


if __name__ == '__main__':
    unittest.main()
