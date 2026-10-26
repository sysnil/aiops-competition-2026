"""Prospective service evidence must separate completion from status flags."""

import importlib.util
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(__file__).resolve().parents[1] / 'scripts/sf005_admission.py'
SPEC = importlib.util.spec_from_file_location('sf007_admission', SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ServiceAdmission(unittest.TestCase):
    def test_frozen_candidate_uses_public_completion_and_clean_noise(self):
        contract = yaml.safe_load((ROOT / 'sf007-formal-v8-20261018/design/'
                                   'service-admission-draft.yaml').read_text())
        self.assertEqual(contract['schema'], 'SF007_SERVICE_PAUSE30_ADMISSION_V2')
        evidence = contract['public_root_evidence']
        self.assertEqual(evidence['maximum_normalized_server_completion_ratio'], .20)
        self.assertGreaterEqual(evidence['minimum_recommendation_caller_spans_during'], 30)
        self.assertEqual(contract['normal_controls']['image_absolute_p95_tolerance_ms'], 2.0)
        self.assertEqual(contract['normal_controls'][
            'maximum_during_to_before_client_p95_ratio'], 1.25)

    def test_no_fault_completion_is_distinct_from_pause(self):
        self.assertGreater(MODULE.normalized_completion_ratio(644, 62, 300, 30), .9)
        self.assertLess(MODULE.normalized_completion_ratio(660, 2, 300, 30), .2)
        self.assertGreaterEqual(MODULE.control_p95_limit(1.628, 1.25, 2), 3.123)
        self.assertEqual(MODULE.control_p95_limit(20.499, 1.25), 20.499 * 1.25)


if __name__ == '__main__':
    unittest.main()
