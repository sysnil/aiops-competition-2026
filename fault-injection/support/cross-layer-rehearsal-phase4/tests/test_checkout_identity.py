"""Checkout UID instrumentation must be uniform and reversible."""

import importlib.util
from pathlib import Path
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'scripts/checkout_identity_rollout.py'
spec = importlib.util.spec_from_file_location('checkout_rollout', SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CheckoutIdentityTest(unittest.TestCase):
    def test_resource_env_uses_downward_api_before_expansion(self):
        original = [{'name': 'OTEL_RESOURCE_ATTRIBUTES',
                     'value': 'service.version=3.0.0,service.criticality=critical'}]
        updated = module.enriched(original)
        self.assertEqual([row['name'] for row in updated[:2]], ['WG_POD_UID', 'WG_POD_NAME'])
        self.assertEqual(updated[0]['valueFrom']['fieldRef']['fieldPath'], 'metadata.uid')
        self.assertIn('k8s.pod.uid=$(WG_POD_UID)', updated[2]['value'])
        self.assertEqual(original[0]['value'],
                         'service.version=3.0.0,service.criticality=critical')


if __name__ == '__main__':
    unittest.main()
