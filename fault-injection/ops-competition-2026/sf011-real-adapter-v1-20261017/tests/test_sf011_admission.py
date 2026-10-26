"""Prospective two-event admission contract stays tied to the intended order."""

import importlib.util
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(__file__).resolve().parents[1] / 'scripts/sf011_admission.py'
SPEC = importlib.util.spec_from_file_location('sf011_admission', SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AdmissionContract(unittest.TestCase):
    def test_faults_are_separate_and_controls_cover_both(self):
        contract = yaml.safe_load((ROOT / 'sf011-formal-v1-20261017/design/'
                                   'sf011-admission-draft.yaml').read_text())
        events = contract['ordered_events']
        self.assertEqual([(item['mechanism'], item['offset_seconds'],
                           item['duration_seconds']) for item in events], [
            ('SERVICE_PAUSE_30', 600, 30),
            ('HTTP_HOME_DELAY_250MS_60', 960, 60)])
        service = events[0]['public_recommendation']
        self.assertLessEqual(service['after_window_offsets'][1],
                             events[1]['offset_seconds'])
        self.assertGreaterEqual(service['minimum_failed_caller_spans_during'], 5)
        controls = contract['normal_controls']
        self.assertGreaterEqual(controls['image_requests_per_30s_window'], 25)
        self.assertEqual(controls['maximum_during_to_before_client_p95_ratio'], 1.25)
        self.assertTrue(contract['result_policy']['no_retroactive_threshold_change'])

    def test_time_windows_and_rank_statistic(self):
        self.assertEqual(MODULE.periods(960, 60, 120, 120), {
            'before': (840, 960), 'during': (960, 1020),
            'after': (1020, 1140)})
        self.assertEqual(MODULE.p95(range(1, 101)), 95)
        self.assertIsNone(MODULE.p95([]))


if __name__ == '__main__':
    unittest.main()
