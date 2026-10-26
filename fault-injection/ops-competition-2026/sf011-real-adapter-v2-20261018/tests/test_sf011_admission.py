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
        contract = yaml.safe_load((ROOT / 'sf011-formal-v2-20261018/design/'
                                   'sf011-admission-draft.yaml').read_text())
        self.assertEqual(contract['schema'], 'SF011_FORWARD_TWO_EVENT_ADMISSION_V2')
        events = contract['ordered_events']
        self.assertEqual([(item['mechanism'], item['offset_seconds'],
                           item['duration_seconds']) for item in events], [
            ('SERVICE_PAUSE_30', 600, 30),
            ('HTTP_HOME_DELAY_250MS_60', 960, 60)])
        service = events[0]['public_recommendation']
        self.assertLessEqual(service['after_window_offsets'][1],
                             events[1]['offset_seconds'])
        self.assertGreaterEqual(service['minimum_failed_caller_spans_during'], 5)
        self.assertEqual(service['maximum_normalized_server_completion_ratio'], .20)
        self.assertGreaterEqual(service['minimum_caller_spans_during'], 30)
        controls = contract['normal_controls']
        self.assertGreaterEqual(controls['image_requests_per_30s_window'], 25)
        self.assertEqual(controls['maximum_during_to_before_client_p95_ratio'], 1.25)
        self.assertGreaterEqual(max(1.25 * 1.628, 1.628 +
                                    controls['image_absolute_p95_tolerance_ms']), 3.123)
        self.assertTrue(contract['result_policy']['no_retroactive_threshold_change'])

    def test_time_windows_and_rank_statistic(self):
        self.assertEqual(MODULE.periods(960, 60, 120, 120), {
            'before': (840, 960), 'during': (960, 1020),
            'after': (1020, 1140)})
        self.assertEqual(MODULE.p95(range(1, 101)), 95)
        self.assertIsNone(MODULE.p95([]))

    def test_recovery_uses_actual_post_preflight(self):
        post = {'status': 'SF011_FORMAL_PREFLIGHT_PASS', 'tag': 'POST',
                'at_utc': '2026-10-18T12:05:00+00:00'}
        lease = {'expires_utc': '2026-10-18T12:01:00+00:00'}
        end = MODULE.stamp('2026-10-18T12:00:00+00:00')
        self.assertTrue(MODULE.http_recovery_valid(post, lease, end))
        self.assertFalse(MODULE.http_recovery_valid(dict(post, tag='ARMED'), lease, end))
        self.assertFalse(MODULE.http_recovery_valid(
            post, {'expires_utc': '2026-10-18T12:06:00+00:00'}, end))

    def test_clean_completion_and_low_latency_noise(self):
        self.assertGreater(MODULE.normalized_completion_ratio(644, 62, 300, 30), .9)
        self.assertLess(MODULE.normalized_completion_ratio(660, 2, 300, 30), .2)
        self.assertGreaterEqual(MODULE.control_p95_limit(1.628, 1.25, 2), 3.123)
        self.assertEqual(MODULE.control_p95_limit(20.499, 1.25), 20.499 * 1.25)


if __name__ == '__main__':
    unittest.main()
