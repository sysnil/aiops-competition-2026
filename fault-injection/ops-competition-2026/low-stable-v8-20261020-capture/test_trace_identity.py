import json
import tempfile
import unittest
from pathlib import Path

from trace_identity import span_map
from trace_storage_reader import digest


class AuditedIdentityTests(unittest.TestCase):
    def source(self, root, conflict=False):
        (root / 'trace-storage-pages').mkdir()
        span = {'traceID': 'a' * 32, 'spanID': 'b' * 16, 'duration': 10}
        other = dict(span, duration=11) if conflict else dict(span)
        hits = [{'_id': 'one', '_source': span},
                {'_id': 'two', '_source': other}]
        (root / 'trace-storage-pages/page-0000.json').write_text(json.dumps({
            'hits': {'hits': hits}}))
        (root / 'trace-storage-source.json').write_text(json.dumps({
            'total_storage_hits': 2, 'unique_spans': 1,
            'identical_duplicate_hits': 1}))
        (root / 'trace-storage-duplicate-audit.json').write_text(json.dumps({
            'duplicate_documents': [{
                'trace_id': 'a' * 32, 'span_id': 'b' * 16,
                'first_document_id': 'one', 'duplicate_document_id': 'two',
                'source_sha256': digest(span)}]}))

    def test_identical_raw_duplicate_reconciles(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.source(root)
            from unittest.mock import patch
            with patch('trace_identity.MAX_IDENTICAL_DUPLICATE_FRACTION', 1):
                self.assertEqual(len(span_map(root)), 1)

    def test_conflicting_raw_duplicate_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.source(root, conflict=True)
            with self.assertRaisesRegex(ValueError, 'Conflicting source'):
                span_map(root)

    def test_audit_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.source(root)
            (root / 'trace-storage-duplicate-audit.json').write_text(json.dumps({
                'duplicate_documents': []}))
            from unittest.mock import patch
            with patch('trace_identity.MAX_IDENTICAL_DUPLICATE_FRACTION', 1):
                with self.assertRaisesRegex(ValueError, 'audit'):
                    span_map(root)


if __name__ == '__main__':
    unittest.main()
