import importlib.util
from pathlib import Path
import unittest


FILE = Path(__file__).resolve().parents[1] / 'scripts/sf009_contract.py'
SPEC = importlib.util.spec_from_file_location('sf009_contract', FILE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ObserverPlanContract(unittest.TestCase):
    def setUp(self):
        root = '/data/stage5-v3/ops-competition-2026/runs/SF-009-ATTEMPT-01'
        self.plan = {'id': 'SF-009-ATTEMPT-01', 'raw_root': root,
                     'control_root': root + '/control', 'collector_uid': 'collector-uid',
                     'targets_and_siblings': [
                         {'service': 'frontend', 'uid': 'f1'},
                         {'service': 'frontend', 'uid': 'f2'},
                         {'service': 'recommendation', 'uid': 'r1'},
                         {'service': 'recommendation', 'uid': 'r2'}]}

    def test_full_candidate_list_passes(self):
        MODULE.validate_plan_fields(self.plan)

    def test_missing_observer_field_fails_before_run(self):
        del self.plan['targets_and_siblings']
        with self.assertRaisesRegex(ValueError, 'PLAN_FIELDS'):
            MODULE.validate_plan_fields(self.plan)


if __name__ == '__main__':
    unittest.main()
