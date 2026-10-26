import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('final_public_export',
                                              SCRIPTS / 'public_export.py')
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


class PublicExportTests(unittest.TestCase):
    def test_half_open_query_boundary(self):
        self.assertTrue(exporter.log_partition(500, 0, 1000))
        self.assertFalse(exporter.log_partition(1000, 0, 1000))
        with self.assertRaises(RuntimeError):
            exporter.log_partition(2_000_000, 0, 1000)

    def test_explicit_envoy_marker_redacted_without_erasing_latency(self):
        span = {'duration': 250_000,
                'tags': [{'key': 'response_flags', 'value': 'DI'},
                         {'key': 'http.url', 'value': 'http://demo/'}]}
        public, count = exporter.public_trace_source(span)
        self.assertEqual(count, 1)
        self.assertEqual(public['duration'], 250_000)
        self.assertNotIn('DI', str(public))
        with self.assertRaisesRegex(RuntimeError, 'Injected request header'):
            exporter.public_trace_source({'tags': [
                {'key': 'x-envoy-fault-delay-request', 'value': '250'}]})


if __name__ == '__main__':
    unittest.main()
