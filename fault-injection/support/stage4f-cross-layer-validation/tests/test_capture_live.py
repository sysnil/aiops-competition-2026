from io import BytesIO
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from capture_live import jaeger_segments, request_json


class LiveCaptureTests(unittest.TestCase):
    def test_jaeger_500_splits_window_without_dropping_a_segment(self):
        failure = HTTPError('http://example.test/traces', 500, 'temporary', {}, None)
        with patch('capture_live.request_json', side_effect=[failure, {'data': []},
                                                          {'data': []}]) as request:
            self.assertEqual(len(jaeger_segments('frontend', 0, 300_000_000)), 2)
            self.assertEqual(request.call_count, 3)

    def test_transient_jaeger_read_retries_but_auth_failure_does_not(self):
        failure = HTTPError('http://example.test/traces', 500, 'temporary', {}, None)
        with patch('urllib.request.urlopen', side_effect=[failure, BytesIO(b'{"data":[]}')]) as call:
            with patch('time.sleep'):
                self.assertEqual(request_json('http://example.test/traces'), {'data': []})
            self.assertEqual(call.call_count, 2)
        unauthorized = HTTPError('http://example.test/traces', 401, 'no', {}, None)
        with patch('urllib.request.urlopen', side_effect=unauthorized):
            with self.assertRaises(HTTPError):
                request_json('http://example.test/traces')


if __name__ == '__main__':
    unittest.main()
