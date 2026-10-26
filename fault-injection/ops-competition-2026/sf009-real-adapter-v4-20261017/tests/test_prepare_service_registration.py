import copy
import importlib.util
import json
from pathlib import Path
import unittest


FIXTURES = Path(__file__).resolve().parent / 'fixtures'
FILE = Path(__file__).resolve().parents[1] / 'scripts/prepare_service_registration.py'
SPEC = importlib.util.spec_from_file_location('prepare_sf009_service', FILE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FreshServiceBinding(unittest.TestCase):
    def test_authoritative_runtime_plan_path_is_explicit(self):
        self.assertEqual(MODULE.SOURCE.name, 'COMP-SF01-SERVICE-SHORT.json')

    def setUp(self):
        self.historical = json.loads((FIXTURES / 'historical-service-plan.json').read_text())
        self.inspection = json.loads((FIXTURES / 'historical-identity.json').read_text())
        self.inspection['identity_sha256'] = 'a' * 64

    def test_current_qualified_targets_bind(self):
        registration = MODULE.build(self.historical, self.inspection)
        self.assertEqual(registration['id'], MODULE.ID)
        self.assertEqual(len(registration['targets']), 2)
        self.assertEqual(registration['duration_seconds'], 30)

    def test_process_replacement_fails_closed(self):
        inspection = copy.deepcopy(self.inspection)
        target = next(row for row in inspection['targets'] if row['service'] == 'recommendation')
        target['processes'][0]['start_ticks'] = 'DIFFERENT'
        with self.assertRaisesRegex(ValueError, 'binding changed'):
            MODULE.build(self.historical, inspection)

    def test_pod_uid_replacement_fails_closed(self):
        inspection = copy.deepcopy(self.inspection)
        target = next(row for row in inspection['targets'] if row['service'] == 'recommendation')
        target['uid'] = 'new-pod'
        with self.assertRaisesRegex(ValueError, 'candidate identity changed'):
            MODULE.build(self.historical, inspection)


if __name__ == '__main__':
    unittest.main()
