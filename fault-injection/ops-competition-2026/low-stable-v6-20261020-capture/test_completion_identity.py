import json
import tempfile
import unittest
from pathlib import Path

from schedule_trace_completion_v2 import OLD, span_keys


class CompletionIdentityTests(unittest.TestCase):
    def test_plus60_uses_same_version_reader(self):
        self.assertEqual(OLD.parent, Path(__file__).resolve().parent)

    def test_raw_duplicate_does_not_duplicate_canonical_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'traces').mkdir()
            (root / 'trace-storage-pages').mkdir()
            trace = {'traceID': 'a' * 32,
                     'spans': [{'spanID': 'b' * 16}]}
            (root / 'traces/storage-0000.json').write_text(json.dumps({
                'data': [trace]}))
            (root / 'trace-storage-pages/page-0000.json').write_text(json.dumps({
                'hits': {'hits': [{'_source': trace['spans'][0]},
                                  {'_source': trace['spans'][0]}]}}))
            self.assertEqual(span_keys(root), {('a' * 32, 'b' * 16)})

    def test_conflicting_canonical_duplicate_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'traces').mkdir()
            span = {'spanID': 'b' * 16}
            (root / 'traces/storage-0000.json').write_text(json.dumps({
                'data': [{'traceID': 'a' * 32, 'spans': [span, span]}]}))
            with self.assertRaisesRegex(ValueError, 'Duplicate canonical'):
                span_keys(root)


if __name__ == '__main__':
    unittest.main()
