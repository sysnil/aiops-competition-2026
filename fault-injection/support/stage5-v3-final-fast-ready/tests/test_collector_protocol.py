import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import run_collector_study as study


class CollectorProtocolTests(unittest.TestCase):
    def test_only_registered_candidate(self):
        data = {'status': 'PREREGISTERED_COLLECTOR_SINGLE_CANDIDATE',
            'id': 'S5V3FFR-COLLECTOR-20261013', 'duration_seconds': 1800,
            'seed': 864001, 'fault_injected': False,
            'candidate_spike_limit_percentage': 15,
            'original_spike_limit_percentage': 25,
            'data_root': '/data/stage5-v3/final-fast-ready',
            'planned_raw_bytes': 5 * 1024 ** 3}
        with patch.object(study, 'validate_workload'):
            study.validate_prereg(data)
            for key, value in (('candidate_spike_limit_percentage', 14),
                               ('duration_seconds', 2700), ('fault_injected', True)):
                changed = {**data, key: value}
                with self.assertRaises(ValueError): study.validate_prereg(changed)

    def test_counter_missing_is_unknown_and_reset_has_no_delta(self):
        def metrics(accepted, refused, limiter):
            return {key: {'status': 'OBSERVED', 'value': value} for key, value in
                {'receiver_accepted_grpc': accepted, 'receiver_refused_grpc': refused,
                 'limiter_refused': limiter, 'jaeger_sent': accepted,
                 'jaeger_send_failed': 0}.items()}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'monitor.jsonl'
            rows = [{'metrics': metrics(100, 4, 2)},
                    {'metrics': metrics(180, 7, 3)}]
            rows[1]['metrics']['limiter_refused'] = {'status': 'UNKNOWN_NOT_ZERO',
                                                     'value': None}
            path.write_text('\n'.join(json.dumps(row) for row in rows))
            outcome = study.summarize_monitor(path)
            self.assertEqual(outcome['receiver_refused_grpc']['delta'], 3)
            self.assertIsNone(outcome['limiter_refused']['delta'])
            self.assertEqual(outcome['limiter_refused']['status'],
                             'UNKNOWN_MISSING_OR_AMBIGUOUS')
            rows[1]['metrics']['receiver_refused_grpc']['value'] = 1
            path.write_text('\n'.join(json.dumps(row) for row in rows))
            outcome = study.summarize_monitor(path)
            self.assertEqual(outcome['receiver_refused_grpc']['resets'], 1)
            self.assertIsNone(outcome['receiver_refused_grpc']['delta'])

    def test_collector_query_failure_propagates(self):
        with patch.object(study.urllib.request, 'urlopen', side_effect=OSError('offline')):
            with self.assertRaises(OSError): study.scalar('otelcol_receiver_accepted_spans_total', 'pod')


if __name__ == '__main__': unittest.main()
