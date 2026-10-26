import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import urllib.error
from datetime import datetime, timedelta, timezone


CAPTURE = Path(__file__).resolve().parents[1] / 'capture'
sys.path.insert(0, str(CAPTURE))
spec = importlib.util.spec_from_file_location('capture_live_v4', CAPTURE / 'capture_live_c2.py')
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)
import trace_storage_reader as storage


def rejected(code, body=b'{"error":"circuit breaker"}'):
    return urllib.error.HTTPError('http://opensearch/_search/scroll', code,
                                  'Too Many Requests', {}, io.BytesIO(body))


class TraceRequestTests(unittest.TestCase):
    def test_scroll_429_retries_post_and_records_reason(self):
        result = io.BytesIO(b'{"hits":{"hits":[]}}')
        with patch.object(capture.urllib.request, 'urlopen', side_effect=[rejected(429), result]), \
             patch.object(capture.time, 'sleep') as sleep, \
             contextlib.redirect_stderr(io.StringIO()) as errors:
            value = capture.request_json(capture.OPENSEARCH + '/_search/scroll', b'{}')
        self.assertEqual(value['hits']['hits'], [])
        self.assertIn('circuit breaker', errors.getvalue())
        sleep.assert_called_once_with(1)

    def test_post_outside_trace_search_is_not_retried(self):
        with patch.object(capture.urllib.request, 'urlopen', side_effect=rejected(429)) as send, \
             contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(urllib.error.HTTPError):
                capture.request_json(capture.PROMETHEUS + '/api/v1/query', b'{}')
        self.assertEqual(send.call_count, 1)

    def test_trace_search_retries_are_bounded(self):
        with patch.object(capture.urllib.request, 'urlopen', side_effect=rejected(429)) as send, \
             patch.object(capture.time, 'sleep'), \
             contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(urllib.error.HTTPError):
                capture.request_json(capture.OPENSEARCH + '/_search/scroll', b'{}')
        self.assertEqual(send.call_count, 6)

    def test_cleanup_error_does_not_hide_invalid_span(self):
        start = datetime(2026, 10, 17, tzinfo=timezone.utc)
        end = start + timedelta(minutes=1)
        index = storage.index_names(start, end)[0]

        def request(url, body=None, method=None):
            if method == 'DELETE':
                raise rejected(429)
            if '/_settings' in url:
                return {index: {'settings': {'index': {'uuid': 'test-uuid'}}}}
            if '/_mapping' in url:
                return {index: {'mappings': {}}}
            return {'_scroll_id': 'test-scroll', '_shards': {'failed': 0},
                    'hits': {'total': {'relation': 'eq', 'value': 1},
                             'hits': [{'_index': index, '_id': 'bad-span', '_source': {}}]}}

        with self.assertRaisesRegex(ValueError, 'Malformed OpenSearch trace/span identity'):
            storage.read_window(start, end, Path('/unused'), capture.OPENSEARCH,
                                request, lambda path, value: None)


if __name__ == '__main__':
    unittest.main()
