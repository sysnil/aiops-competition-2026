import json
from pathlib import Path
import unittest


POLICY = Path(__file__).resolve().parents[1] / 'design/workload-policy.json'


class FrozenLoadContract(unittest.TestCase):
    def test_low_stable_and_independent_controls_match_registered_rates(self):
        value = json.loads(POLICY.read_text())
        workload = value['workload']
        self.assertEqual(workload['duration_seconds'], 1800)
        self.assertEqual(workload['profile'], 'LOW')
        self.assertEqual(workload['policy_name'], 'LOW_STABLE')
        self.assertEqual(workload['rate_sessions_per_second'], 0.7)
        self.assertEqual({item['name']: item['rate_per_second']
                          for item in workload['control_streams']},
                         {'image_asset': 1.0, 'cart_view': 1.0,
                          'home': 1.0, 'recommendations': 1.0})
        self.assertTrue(value['fixed_across_before_during_after'])


if __name__ == '__main__':
    unittest.main()
