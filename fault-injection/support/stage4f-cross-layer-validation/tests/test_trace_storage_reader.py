from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from capture_live import write_json
from trace_storage_reader import (TRACE_SOURCE, index_names, legacy_traces,
                                  read_window, validate_span)


class TraceStorageReaderTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 10, 4, 1, 10, tzinfo=timezone.utc)
        self.end = self.start + timedelta(seconds=60)
        self.name = 'jaeger-jaeger-span-2026-10-04'
        stamp = int(self.start.timestamp() * 1_000_000) + 1000
        self.hit = {'_index': self.name, '_id': 'document-1', '_source': {
            'traceID': 'a' * 32, 'spanID': 'b' * 16,
            'operationName': 'request', 'startTime': stamp,
            'startTimeMillis': stamp // 1000, 'duration': 2500,
            'references': [{'refType': 'CHILD_OF', 'traceID': 'a' * 32,
                            'spanID': 'c' * 16}],
            'tags': [{'key': 'otel.status_code', 'value': 'OK'}],
            'tag': {'span@kind': 'client'},
            'process': {'serviceName': 'frontend'}}}

    def test_indices_use_exact_utc_days(self):
        self.assertEqual(index_names(self.start, self.end), [self.name])
        crossing = datetime(2026, 10, 4, 23, 59, tzinfo=timezone.utc)
        self.assertEqual(index_names(crossing, crossing + timedelta(minutes=2)),
                         [self.name, 'jaeger-jaeger-span-2026-10-05'])

    def test_direct_reader_archives_raw_source_and_legacy_trace(self):
        settings = {self.name: {'settings': {'index': {'uuid': 'uuid-1'}}}}
        mapping = {self.name: {'mappings': {'properties': {'traceID': {'type': 'keyword'}}}}}
        response = {'_scroll_id': 'scroll-1', 'hits': {
            'total': {'value': 1, 'relation': 'eq'}, 'hits': [self.hit]}}
        calls = []

        def request(url, body=None, method=None):
            calls.append((url, body, method))
            return [settings, mapping, response, {}, settings][len(calls) - 1]

        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            metadata = read_window(self.start, self.end, output,
                                   'http://storage.test:9200', request, write_json)
            trace = json.loads((output / 'traces/storage-0000.json').read_text())['data'][0]
            self.assertEqual(metadata['trace_source'], TRACE_SOURCE)
            self.assertEqual(metadata['unique_spans'], 1)
            self.assertEqual(trace['processes']['p0']['serviceName'], 'frontend')
            self.assertEqual(trace['spans'][0]['references'][0]['spanID'], 'c' * 16)
            self.assertIn({'key': 'span.kind', 'type': 'string', 'value': 'client'},
                          trace['spans'][0]['tags'])
            self.assertTrue((output / 'trace-storage-pages/page-0000.json').is_file())
            self.assertEqual(calls[-2][2], 'DELETE')

    def test_rejects_out_of_window_or_duplicate_identity(self):
        invalid = {'_index': self.name, '_id': 'document-2',
                   '_source': dict(self.hit['_source'], startTime=0)}
        with self.assertRaisesRegex(ValueError, 'outside'):
            validate_span(invalid, {self.name},
                          int(self.start.timestamp() * 1_000_000),
                          int(self.end.timestamp() * 1_000_000))
        settings = {self.name: {'settings': {'index': {'uuid': 'uuid-1'}}}}
        mapping = {self.name: {'mappings': {}}}
        response = {'_scroll_id': 'scroll-1', 'hits': {
            'total': {'value': 2, 'relation': 'eq'},
            'hits': [self.hit, dict(self.hit, _id='document-2')]}}
        results = iter((settings, mapping, response, {}))
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                read_window(self.start, self.end, Path(temp), 'http://storage.test',
                            lambda *_args, **_kwargs: next(results), write_json)

    def test_null_root_references_normalize_to_empty_list(self):
        root = dict(self.hit, _source=dict(self.hit['_source'], references=None))
        validate_span(root, {self.name}, int(self.start.timestamp() * 1_000_000),
                      int(self.end.timestamp() * 1_000_000))
        self.assertEqual(legacy_traces([root])[0]['spans'][0]['references'], [])

    def test_failed_shard_and_index_replacement_fail_closed(self):
        settings = {self.name: {'settings': {'index': {'uuid': 'uuid-1'}}}}
        mapping = {self.name: {'mappings': {}}}
        response = {'_scroll_id': 'scroll-1', '_shards': {'failed': 1},
                    'hits': {'total': {'value': 1, 'relation': 'eq'},
                             'hits': [self.hit]}}
        results = iter((settings, mapping, response, {}))
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, 'failed shards'):
                read_window(self.start, self.end, Path(temp), 'http://storage.test',
                            lambda *_args, **_kwargs: next(results), write_json)
        response['_shards']['failed'] = 0
        changed = {self.name: {'settings': {'index': {'uuid': 'uuid-2'}}}}
        results = iter((settings, mapping, response, {}, changed))
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, 'UUID changed'):
                read_window(self.start, self.end, Path(temp), 'http://storage.test',
                            lambda *_args, **_kwargs: next(results), write_json)


if __name__ == '__main__':
    unittest.main()
