import json
from pathlib import Path
import unittest


POLICY = Path(__file__).resolve().parents[1] / 'design/workload-policy.json'


class FrozenLoadContract(unittest.TestCase):
    def test_medium_and_independent_controls_match_verified_rates(self):
        value = json.loads(POLICY.read_text())
        workload = value['workload']
        self.assertEqual(workload['duration_seconds'], 1800)
        self.assertEqual(workload['profile'], 'MEDIUM')
        self.assertEqual(workload['rate_sessions_per_second'], 1.4)
        self.assertEqual({item['name']: item['rate_per_second']
                          for item in workload['control_streams']},
                         {'image_asset': 1.0, 'cart_view': 2.0})
        self.assertEqual(workload['control_concurrency_per_stream'],
                         {'image_asset': 3, 'cart_view': 6})
        self.assertTrue(value['fixed_across_before_during_after'])


if __name__ == '__main__':
    unittest.main()
