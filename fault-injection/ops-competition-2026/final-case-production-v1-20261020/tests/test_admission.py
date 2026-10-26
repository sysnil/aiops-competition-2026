import importlib.util
import sys
import unittest
from collections import defaultdict
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/admission.py'
spec = importlib.util.spec_from_file_location('final_admission', SCRIPT)
admission = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = admission
spec.loader.exec_module(admission)


class AdmissionTests(unittest.TestCase):
    def test_two_action_windows_do_not_overlap(self):
        start = 1792454400
        manifest = {'start_utc': admission.datetime.fromtimestamp(
            start, admission.timezone.utc).isoformat(),
            'actions': [{'action_id': 'http', 'planned_start_utc':
                         admission.datetime.fromtimestamp(start + 600,
                             admission.timezone.utc).isoformat(),
                         'duration_seconds': 240},
                        {'action_id': 'service', 'planned_start_utc':
                         admission.datetime.fromtimestamp(start + 960,
                             admission.timezone.utc).isoformat(),
                         'duration_seconds': 240}]}
        values = admission.windows(manifest)
        self.assertEqual(values['http']['after'], (start + 840, start + 960))
        self.assertEqual(values['service']['before'],
                         (start + 840, start + 960))
        self.assertEqual(values['service']['after'],
                         (start + 1320, start + 1620))

    def test_service_can_be_valid_with_zero_during_server_spans(self):
        servers = defaultdict(lambda: defaultdict(set))
        for uid in ('a', 'b'):
            servers[uid]['before'] = {str(i) for i in range(20)}
            servers[uid]['after'] = {str(i) for i in range(20)}
        callers = defaultdict(set)
        callers['during'] = {str(i) for i in range(40)}
        trace = {'service_servers': servers, 'rec_callers': callers,
                 'rec_proxy': defaultdict(set)}
        checks, _ = admission.service_pause(trace,
            {'before': (0, 300), 'during': (300, 600),
             'after': (600, 900)},
            [{'uid': 'a'}, {'uid': 'b'}],
            {'minimum_server_trace_ids_per_pod_before_and_after': 10,
             'minimum_public_caller_or_proxy_spans_during': 30,
             'maximum_during_to_before_server_completion_rate_ratio': .2,
             'minimum_after_to_before_server_completion_rate_ratio': .5})
        self.assertTrue(all(checks.values()))

    def test_http_requires_recovery_and_samples(self):
        rule = {'minimum_home_proxy_spans_each_window': 30,
                'minimum_p95_increase_milliseconds': 100,
                'maximum_after_minus_before_p95_milliseconds': 100}
        trace = {'home_proxy': {'before': {str(i): 20 for i in range(40)},
                                'during': {str(i): 270 for i in range(40)},
                                'after': {str(i): 22 for i in range(40)}}}
        checks, _ = admission.http_delay(trace, rule)
        self.assertTrue(all(checks.values()))
        trace['home_proxy']['after'] = {str(i): 200 for i in range(40)}
        checks, _ = admission.http_delay(trace, rule)
        self.assertFalse(checks['public_home_p95_recovery'])


if __name__ == '__main__':
    unittest.main()
