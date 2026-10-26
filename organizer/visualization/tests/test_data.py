from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest

import data


class CaseDiscoveryTests(unittest.TestCase):
    def test_unready_and_unpublished_cases_are_hidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIsNone(data.load_case('SF-002', root))
            manifest = root / 'manifests/SF-001.json'
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({'case_id': 'SF-001',
                'classification': 'PUBLIC_COMPETITION_DATA',
                'publication_status': 'draft', 'version': 'v1'}))
            self.assertIsNone(data.load_case('SF-001', root))
            with self.assertRaises(ValueError): data.load_case('../private', root)

    def test_actual_sf001_public_window_and_counts(self):
        case = data.load_case('SF-001')
        self.assertIsNotNone(case)
        self.assertEqual((case['end'] - case['start']).total_seconds(), 3600)
        self.assertEqual(data.counts(case, case['start'], case['end']),
            {'metrics': 80358, 'logs': 567346, 'traces': 817529,
             'trace_index': 119091})
        self.assertIsNone(data.load_case('SF-002'))

    def test_live_public_queries_and_waterfall(self):
        case = data.load_case('SF-001')
        facet = data.facets(case)
        self.assertTrue(facet['metrics'])
        self.assertTrue(facet['services'])
        metric = data.metric_series(case, case['start'], case['end'],
                                    facet['metrics'][0], None, None)
        self.assertFalse(metric.empty)
        cpu = data.metric_series(case, case['start'], case['end'],
                                 'container_cpu_usage_seconds_total', 'frontend', None)
        self.assertFalse(cpu.empty)
        self.assertTrue(cpu.pod.str.startswith('frontend-').any())
        self.assertFalse(data.log_summary(case, case['start'], case['end'],
                                          None, None).empty)
        self.assertFalse(data.logs(case, case['start'], case['end'],
                                   None, None, '', limit=10).empty)
        traces = data.trace_list(case, case['start'], case['end'],
                                 None, None, None, limit=10)
        self.assertFalse(traces.empty)
        waterfall = data.trace_spans(case, traces.iloc[0]['trace_id'])
        self.assertFalse(waterfall.empty)
        self.assertEqual(set(waterfall.trace_id), {traces.iloc[0]['trace_id']})
        self.assertFalse(data.edge_summary(case, case['start'], case['end'],
                                            None, None).empty)
        frontend_pod = next(p for p in data.facets(case)['pods'] if p.startswith('frontend-'))
        self.assertFalse(data.trace_list(case, case['start'], case['end'],
                                         'frontend', frontend_pod, None, limit=2).empty)
        edge = ('checkout', 'cart')
        self.assertFalse(data.trace_list(case, case['start'], case['end'],
                                         None, None, edge, limit=2).empty)

    def test_manifest_hashes(self):
        case = data.load_case('SF-001')
        checked = data.verify_files(case)
        self.assertEqual(set(checked), set(data.FILES))
        self.assertTrue(all(row['match'] and row['size_match']
                            for row in checked.values()))


if __name__ == '__main__':
    unittest.main()
