"""Reverse-order admission uses independent clean and public service evidence."""

import importlib.util
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(__file__).resolve().parents[1] / 'scripts/sf009_admission.py'
SPEC = importlib.util.spec_from_file_location('sf009_admission', SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ReverseAdmission(unittest.TestCase):
    def test_two_event_contract(self):
        contract = yaml.safe_load((ROOT / 'sf009-formal-v5-20261018/design/'
                                   'reverse-admission-draft.yaml').read_text())
        self.assertEqual(contract['schema'], 'SF009_REVERSE_TWO_EVENT_ADMISSION_V2')
        self.assertEqual([(item['mechanism'], item['offset_seconds']) for item in
                          contract['ordered_events']], [
            ('HTTP_HOME_DELAY_250MS_60', 600), ('SERVICE_PAUSE_30', 960)])
        service = contract['ordered_events'][1]['public_recommendation']
        self.assertEqual(service['maximum_normalized_server_completion_ratio'], .20)
        self.assertGreaterEqual(service['minimum_caller_spans_during'], 30)
        self.assertEqual(contract['normal_controls']['image_absolute_p95_tolerance_ms'], 2.0)

    def test_clean_and_fault_completion_separate(self):
        self.assertGreater(MODULE.normalized_completion_ratio(644, 62, 300, 30), .9)
        self.assertLess(MODULE.normalized_completion_ratio(660, 2, 300, 30), .2)
        self.assertGreaterEqual(MODULE.control_p95_limit(1.628, 1.25, 2), 3.123)
        self.assertEqual(MODULE.control_p95_limit(20.499, 1.25), 20.499 * 1.25)


if __name__ == '__main__':
    unittest.main()
