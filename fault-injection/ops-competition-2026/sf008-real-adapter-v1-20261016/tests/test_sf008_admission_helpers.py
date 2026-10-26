import importlib.util
from pathlib import Path
import unittest


FILE = Path(__file__).resolve().parents[1] / 'scripts/sf008_admission.py'
SPEC = importlib.util.spec_from_file_location('sf008_admission', FILE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AdmissionMath(unittest.TestCase):
    def test_p95_uses_ceil_nearest_rank(self):
        self.assertEqual(MODULE.p95(range(1, 21)), 19)
        self.assertIsNone(MODULE.p95([]))

    def test_consecutive_evidence_cannot_bridge_a_missing_gap(self):
        self.assertEqual(MODULE.longest_consecutive([0, 15, 30, 45]), 4)
        self.assertEqual(MODULE.longest_consecutive([0, 15, 46, 61]), 2)

    def test_duplicate_timestamp_is_not_an_extra_sample(self):
        self.assertEqual(MODULE.longest_consecutive([0, 15, 15, 30]), 3)

    def test_windows_are_half_open(self):
        windows = {'before': (0, 10), 'during': (10, 20)}
        self.assertEqual(MODULE.interval(9.999, windows), 'before')
        self.assertEqual(MODULE.interval(10, windows), 'during')
        self.assertIsNone(MODULE.interval(20, windows))

    def test_no_fault_public_cannot_enter_formal_admission(self):
        manifest = {'case_id': 'SF-008',
                    'window': {'run_id': 'SF-008-ATTEMPT-01', 'formal_case': False}}
        with self.assertRaisesRegex(RuntimeError, 'Formal SF-008 Public'):
            MODULE.verify_formal_public_window(manifest, 'SF-008-ATTEMPT-01')
        manifest['window']['formal_case'] = True
        MODULE.verify_formal_public_window(manifest, 'SF-008-ATTEMPT-01')


if __name__ == '__main__':
    unittest.main()
