import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from trace_storage_reader import (MAX_IDENTICAL_DUPLICATE_FRACTION,
                                  read_window, register_hit)


class ExactDuplicateTests(unittest.TestCase):
    def test_identical_documents_have_one_canonical_span(self):
        seen, duplicates = {}, []
        source = {'traceID': 'a' * 32, 'spanID': 'b' * 16, 'startTime': 1}
        self.assertTrue(register_hit({'_id': 'first', '_source': source},
                                     seen, duplicates))
        self.assertFalse(register_hit({'_id': 'second', '_source': dict(source)},
                                      seen, duplicates))
        self.assertEqual(len(seen), 1)
        self.assertEqual(duplicates[0]['first_document_id'], 'first')
        self.assertEqual(duplicates[0]['duplicate_document_id'], 'second')

    def test_conflicting_content_fails_closed(self):
        seen, duplicates = {}, []
        original = {'traceID': 'a' * 32, 'spanID': 'b' * 16, 'duration': 1}
        register_hit({'_id': 'first', '_source': original}, seen, duplicates)
        with self.assertRaisesRegex(ValueError, 'Conflicting duplicate'):
            register_hit({'_id': 'second', '_source': dict(original, duration=2)},
                         seen, duplicates)

    def test_duplicate_limit_is_half_percent(self):
        self.assertEqual(MAX_IDENTICAL_DUPLICATE_FRACTION, 0.005)

    def test_raw_pages_and_duplicate_audit_are_retained(self):
        start = datetime(2026, 10, 20, tzinfo=timezone.utc)
        end = start + timedelta(minutes=1)
        stamp = int(start.timestamp() * 1_000_000) + 10
        index = 'jaeger-jaeger-span-2026-10-20'
        source = {'traceID': 'a' * 32, 'spanID': 'b' * 16,
                  'startTime': stamp, 'startTimeMillis': stamp // 1000,
                  'duration': 100, 'operationName': 'GET /',
                  'process': {'serviceName': 'frontend'}}
        response = {'_scroll_id': 'scroll-1', '_shards': {'failed': 0},
                    'hits': {'total': {'relation': 'eq', 'value': 2},
                             'hits': [{'_index': index, '_id': doc,
                                       '_source': dict(source)}
                                      for doc in ('one', 'two')]}}
        writes = {}

        def request(url, body=None, method=None):
            if '_settings' in url:
                return {index: {'settings': {'index': {'uuid': 'fixed'}}}}
            if '_mapping' in url:
                return {index: {'mappings': {}}}
            if method == 'DELETE':
                return {}
            return response

        def write(path, value):
            writes[path.name] = value

        with patch('trace_storage_reader.MAX_IDENTICAL_DUPLICATE_FRACTION', 1):
            metadata = read_window(start, end, Path('/unused'), 'http://unused',
                                   request, write)
        self.assertEqual(metadata['total_storage_hits'], 2)
        self.assertEqual(metadata['unique_spans'], 1)
        self.assertEqual(metadata['identical_duplicate_hits'], 1)
        self.assertEqual(len(writes['page-0000.json']['hits']['hits']), 2)
        self.assertEqual(len(writes['storage-0000.json']['data'][0]['spans']), 1)
        self.assertEqual(writes['trace-storage-duplicate-audit.json'][
                         'duplicate_documents'][0]['duplicate_document_id'], 'two')

    def test_excessive_duplicate_fraction_fails_closed(self):
        self.assertLess(MAX_IDENTICAL_DUPLICATE_FRACTION, 1 / 2)


if __name__ == '__main__':
    unittest.main()
