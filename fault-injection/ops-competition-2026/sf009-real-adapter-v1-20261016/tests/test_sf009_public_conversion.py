import importlib.util
from pathlib import Path
import sys
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location('sf009_export',
                                              SCRIPTS / 'sf009_export.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PublicControlBoundary(unittest.TestCase):
    def test_proxy_access_log_preserves_status_and_latency_without_control_flag(self):
        source = {'attributes': {'event.name': 'proxy.access'},
                  'body': '[2026-10-15T12:03:42Z] "GET / HTTP/1.1" 200 DI via_upstream 0 11491 257 5\n'}
        public, removed = MODULE.public_log_source(source)
        self.assertEqual(removed, 1)
        self.assertIn('200 ? via_upstream 0 11491 257 5', public['body'])
        self.assertIn('200 DI', source['body'])

    def test_unparseable_delay_log_fails_export(self):
        source = {'attributes': {'event.name': 'proxy.access'}, 'body': 'bad 200 DI record'}
        with self.assertRaisesRegex(RuntimeError, 'Cannot safely locate'):
            MODULE.public_log_source(source)

    def test_explicit_delay_flag_removed_but_real_status_retained(self):
        source = {'tags': [
            {'key': 'response_flags', 'value': 'DI'},
            {'key': 'http.status_code', 'value': '200'},
        ], 'tag': {'span@kind': 'server'}}
        public, removed = MODULE.public_trace_source(source)
        self.assertEqual(removed, 1)
        self.assertEqual(public['tags'], [{'key': 'http.status_code', 'value': '200'}])
        self.assertEqual(source['tags'][0]['value'], 'DI')

    def test_injected_header_in_storage_blocks_public_export(self):
        source = {'tags': [{'key': 'x-envoy-fault-delay-request', 'value': '250'}]}
        with self.assertRaisesRegex(RuntimeError, 'Injected request header'):
            MODULE.public_trace_source(source)


if __name__ == '__main__':
    unittest.main()
