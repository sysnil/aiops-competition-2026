"""Exact microsecond query excludes spans sharing a boundary millisecond."""

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'scripts/trace_storage_reader.py'
spec = importlib.util.spec_from_file_location('phase3_trace_reader', SOURCE)
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)


class TraceWindowTest(unittest.TestCase):
    def test_exact_microsecond_filter_and_validation(self):
        start = datetime(2026, 10, 10, 7, 18, 3, 374204, timezone.utc)
        end = datetime(2026, 10, 10, 7, 19, 3, 374204, timezone.utc)
        start_us = int(start.timestamp() * 1_000_000)
        end_us = int(end.timestamp() * 1_000_000)
        index = 'jaeger-jaeger-span-2026-10-10'
        span = {'traceID': 'a' * 32, 'spanID': 'b' * 16,
                'startTime': start_us, 'startTimeMillis': start_us // 1000,
                'duration': 1, 'operationName': 'GET /',
                'process': {'serviceName': 'frontend'}, 'references': []}
        hit = {'_index': index, '_id': '1', '_source': span}
        requests = []

        def request(url, body=None, method=None):
            if body and method != 'DELETE':
                import json
                query = json.loads(body)
                requests.append(query)
                return {'_scroll_id': 'scroll', 'hits': {'total': {'value': 1, 'relation': 'eq'},
                        'hits': [hit]}, '_shards': {'failed': 0}}
            if url.endswith('_mapping'):
                return {index: {'mappings': {'startTime': {'type': 'long'}}}}
            if '_settings' in url:
                return {index: {'settings': {'index': {'uuid': 'uuid'}}}}
            return {}

        def write(path, value):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{}')

        with tempfile.TemporaryDirectory() as temp:
            result = reader.read_window(start, end, Path(temp), 'http://storage', request, write)
        self.assertEqual(result['unique_spans'], 1)
        filters = requests[0]['query']['bool']['filter']
        self.assertIn({'range': {'startTime': {'gte': start_us, 'lt': end_us}}}, filters)
        earlier = {**span, 'startTime': start_us - 1,
                   'startTimeMillis': (start_us - 1) // 1000}
        with self.assertRaisesRegex(ValueError, 'outside the exact acquisition window'):
            reader.validate_span({**hit, '_source': earlier}, {index}, start_us, end_us)


if __name__ == '__main__':
    unittest.main()
