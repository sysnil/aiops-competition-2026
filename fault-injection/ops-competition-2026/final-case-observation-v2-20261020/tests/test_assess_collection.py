import json
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from assess_collection import assess, normal_requests

CONTRACT = json.loads((Path(__file__).resolve().parents[1] /
                       'design/collection-contract-draft.json').read_text())


def summary():
    return {'duration_seconds': 1800, 'observer_status': 'OBSERVER_COMPLETE',
            'collector_delta': {'spans': 0, 'logs': 0, 'metrics': 0},
            'chunks': [{'chunk': n, 'window_matches': True,
                        'trace_indices_stable': True,
                        'trace_source': 'JAEGER_OPENSEARCH_STORAGE',
                        'hash_chain_valid': True, 'trace_records': 100,
                        'log_records': 100, 'metric_files': 11,
                        'start_lag_seconds': 6,
                        'completion_lag_seconds': 30,
                        'final_spans': 1000, 'late_spans': 1}
                       for n in range(6)],
            'control_streams': {name: {'planned_requests': 1800,
                                      'successful_requests': 1790,
                                      'missed_arrivals': 10}
                                for name in ('image_asset', 'cart_view')},
            'control_success_by_chunk': {name: [298, 298, 298, 298, 299, 299]
                                         for name in ('image_asset', 'cart_view')},
            'identified_control_requests': 3600,
            'matched_control_proxy_spans': 3580,
            'unmatched_control_trace_ids': ['real-missing-id'] * 20,
            'control_proxy_match_by_name': {
                'image_asset': {'identified': 1800, 'matched': 1790},
                'cart_view': {'identified': 1800, 'matched': 1790}},
            'target_request_count': 600,
            'target_request_proxy_span_missing_count': 400,
            'normal_control_scope_only': True}


class CollectionTests(unittest.TestCase):
    def test_target_failures_do_not_enter_normal_span_denominator(self):
        rows = [{'operation': 'recommendations', 'trace_id': 'target-failed'},
                {'operation': 'image_asset', 'trace_id': 'normal-observed'}]
        self.assertEqual(normal_requests(rows, CONTRACT['normal_controls']),
                         [rows[1]])
        self.assertEqual(assess(summary(), CONTRACT)['status'],
                         'DATA_COLLECTION_ACCEPTED_FOR_ADMISSION')

    def test_bounded_control_and_span_gaps_are_visible_but_accepted(self):
        result = assess(summary(), CONTRACT)
        self.assertEqual(result['status'], 'DATA_COLLECTION_ACCEPTED_FOR_ADMISSION')
        self.assertEqual(len(result['unmatched_control_trace_ids']), 20)

    def test_missing_modality_and_collector_refusal_block(self):
        value = summary()
        value['chunks'][2]['log_records'] = 0
        value['collector_delta']['spans'] = 1
        result = assess(value, CONTRACT)
        self.assertIn('CHUNK_MODALITY_MISSING:2', result['issues'])
        self.assertIn('COLLECTOR_OR_OBSERVER', result['issues'])

    def test_normal_control_coverage_and_late_fraction_block(self):
        value = summary()
        value['control_streams']['cart_view']['successful_requests'] = 1500
        value['control_success_by_chunk']['cart_view'][2] = 200
        value['chunks'][4]['late_spans'] = 100
        result = assess(value, CONTRACT)
        self.assertIn('CONTROL_COVERAGE:cart_view', result['issues'])
        self.assertIn('TRACE_LATE_FRACTION', result['issues'])


if __name__ == '__main__':
    unittest.main()
